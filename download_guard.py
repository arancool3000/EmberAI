"""Real-time download protection for Ember.

A single background daemon thread polls the watched folder (default the user's
Downloads) every ~2 seconds. When a NEW file appears AND has finished being
written (its size is stable across two polls — guarding against scanning a
half-finished download), it is scanned with the antivirus engine. Threats are
recorded in a bounded in-memory event log and surfaced via the status/events
tools (your wiring can raise an alert / toast from there).

Design notes (mirrors extra_tools.watch_folder_* — daemon thread + a bounded
events buffer behind a lock):
  * No watchdog dependency: os.scandir + a seen-set of already-known paths.
  * In-progress download extensions (.crdownload/.part/.download/.tmp) are
    skipped until they settle into their final name.
  * `_SCANNER` is a module-level injection point. Default None -> the worker
    lazily `import antivirus; antivirus.scan_file(path)`. Tests set `_SCANNER`
    to a fake so nothing real is imported and the test stays fast/offline.
  * State is in-memory only — nothing is persisted to disk.

Standard library only at import time (antivirus is imported lazily, inside the
scan worker, so importing this module stays light and testable).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_POLL_SECONDS = 2.0           # how often the watcher scans the folder
_EVENTS_MAXLEN = 200          # bounded history of scan events
_IN_PROGRESS_EXTS = {".crdownload", ".part", ".download", ".tmp"}

# Executable / script types that warrant a heads-up the moment they land in Downloads —
# even if the content looks benign, a freshly-downloaded executable is worth flagging.
_DANGEROUS_DL_EXTS = {
    ".exe", ".msi", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".ps1", ".psm1", ".lnk", ".jar",
    ".app", ".pkg", ".dmg", ".command", ".sh", ".bash", ".zsh", ".scpt", ".reg", ".apk",
}

# Injection point for tests: a callable(path: str) -> dict shaped like
# antivirus.scan_file (i.e. with a "verdict" key). Default None -> use antivirus.
_SCANNER = None

# UI hook: callable(alert: dict) fired when a download is a threat or a cautionary
# executable, so the app can toast/quarantine. dict = {level, path, name, detail}.
_ON_THREAT = None


def set_on_threat(cb) -> None:
    """Register a callback the watcher fires (on its thread) for threat/caution downloads.
    The UI marshals this to the main thread to show an alert + offer quarantine."""
    global _ON_THREAT
    _ON_THREAT = cb

# ---------------------------------------------------------------------------
# Module-level state (all guarded by _LOCK)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_events: "deque[dict]" = deque(maxlen=_EVENTS_MAXLEN)
_seen: set[str] = set()
_watched_folder: Path | None = None
_running = False
_threats_found = 0


def _default_downloads() -> Path:
    """The user's Downloads folder."""
    return Path.home() / "Downloads"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _do_scan(path: str) -> dict:
    """Scan a file using the injected scanner if set, else antivirus.scan_file.

    antivirus is imported lazily here (never at module top) so importing this
    module is light and tests can run without the real scanner.
    """
    scanner = _SCANNER
    if scanner is None:
        import antivirus  # lazy: keeps module import light + testable
        scanner = antivirus.scan_file
    return scanner(path)


def _classify(result: dict) -> tuple[str, str]:
    """Map a scan result dict to (status, detail).

    antivirus.scan_file reports its finding under the "verdict" key
    ("clean"|"suspicious"|"malicious"). Anything other than a successful clean
    scan with no threat counts as either an error or a threat.
    """
    if not isinstance(result, dict):
        return "error", "scanner returned a non-dict result"
    if result.get("ok") is False:
        return "error", str(result.get("error") or "scan error")
    verdict = result.get("verdict")
    if verdict in ("malicious", "suspicious"):
        reasons = result.get("reasons")
        if isinstance(reasons, (list, tuple)):
            detail = f"{verdict}: " + "; ".join(str(r) for r in reasons)
        else:
            detail = str(verdict)
        return "threat", detail
    if verdict == "clean":
        return "clean", "no threats found"
    # Unknown / missing verdict — be conservative but don't crash.
    return "error", f"unrecognized scan result (verdict={verdict!r})"


def _record(path: str, status: str, detail: str) -> None:
    global _threats_found
    evt = {
        "time": _now_iso(),
        "path": path,
        "name": os.path.basename(path),
        "result": status,
        "detail": detail,
    }
    with _LOCK:
        _events.append(evt)
        if status == "threat":
            _threats_found += 1


def _list_files(folder: Path) -> dict[str, int]:
    """Return {path: size} for regular files directly in `folder`.

    Skips in-progress download temp files. Never raises.
    """
    out: dict[str, int] = {}
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    name = entry.name
                    ext = os.path.splitext(name)[1].lower()
                    if ext in _IN_PROGRESS_EXTS:
                        continue
                    out[entry.path] = entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _watch_loop(folder: Path, stop: threading.Event) -> None:
    """Poll the folder; scan files that are NEW and have stopped growing.

    A file is scanned only once both:
      * it is not already in `_seen` (so only new arrivals after start), and
      * its size is identical across two consecutive polls (settled / complete).
    """
    pending: dict[str, int] = {}  # path -> size observed last poll, not yet scanned
    while not stop.is_set():
        try:
            current = _list_files(folder)
            for path, size in current.items():
                with _LOCK:
                    already = path in _seen
                if already:
                    continue
                prev = pending.get(path)
                if prev is not None and prev == size:
                    # Size stable across two polls -> finished writing -> scan.
                    pending.pop(path, None)
                    with _LOCK:
                        _seen.add(path)
                    try:
                        result = _do_scan(path)
                        status, detail = _classify(result)
                    except Exception as e:  # never let a scan crash the thread
                        status, detail = "error", f"scan raised: {e}"
                    ext = os.path.splitext(path)[1].lower()
                    # A clean-but-EXECUTABLE download still deserves a heads-up (this is the
                    # 'virus.vbs slipped through' case — benign content, dangerous type).
                    if status == "clean" and ext in _DANGEROUS_DL_EXTS:
                        status = "caution"
                        detail = (f"Downloaded an executable file ({ext}). Don't open it unless "
                                  "you trust the source.")
                    _record(path, status, detail)
                    # ACT on it: auto-quarantine confirmed malware; alert on threat/caution.
                    if status in ("threat", "caution"):
                        if status == "threat" and "malicious" in detail.lower():
                            try:
                                import antivirus
                                qr = antivirus.quarantine_file(path, reasons=[detail])
                                if qr.get("ok"):
                                    detail += " — auto-quarantined"
                            except Exception:
                                pass
                        cb = _ON_THREAT
                        if cb:
                            try:
                                cb({"level": status, "path": path,
                                    "name": os.path.basename(path), "detail": detail})
                            except Exception:
                                pass
                else:
                    # First sighting, or still growing: remember its size.
                    pending[path] = size
            # Drop pending entries for files that vanished before settling.
            for gone in [p for p in pending if p not in current]:
                pending.pop(gone, None)
        except Exception:
            # Defensive: a polling hiccup must not kill the watcher.
            pass
        stop.wait(_POLL_SECONDS)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def download_guard_start(folder: str = "") -> dict:
    """Start real-time download protection. Idempotent.

    On start, primes the seen-set with files already present so only files that
    arrive AFTER this call are ever flagged. folder defaults to ~/Downloads.
    """
    global _thread, _stop_event, _watched_folder, _running
    try:
        target = Path(folder).expanduser() if folder else _default_downloads()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        with _LOCK:
            if _running and _thread is not None and _thread.is_alive():
                return {"ok": True, "watching": True,
                        "folder": str(_watched_folder),
                        "message": "download protection is already running"}
            # Prime the seen-set so existing files are NOT treated as new arrivals.
            _seen.clear()
            existing = _list_files(target)
            _seen.update(existing.keys())
            _watched_folder = target
            _stop_event = threading.Event()
            stop = _stop_event
            _thread = threading.Thread(
                target=_watch_loop, args=(target, stop),
                name="ember-download-guard", daemon=True,
            )
            _running = True
            _thread.start()
        return {"ok": True, "watching": True, "folder": str(target),
                "message": f"watching {target} for new downloads (primed "
                           f"{len(existing)} existing file(s))"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def download_guard_stop() -> dict:
    """Stop the background download watcher."""
    global _thread, _stop_event, _running
    try:
        with _LOCK:
            running = _running
            stop = _stop_event
            thread = _thread
            _running = False
            _stop_event = None
            _thread = None
        if not running or thread is None:
            return {"ok": True, "message": "download protection was not running"}
        if stop is not None:
            stop.set()
        thread.join(timeout=5.0)
        return {"ok": True, "message": "download protection stopped"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def download_guard_status() -> dict:
    """Report whether protection is running and what it has seen."""
    try:
        with _LOCK:
            running = bool(_running and _thread is not None and _thread.is_alive())
            folder = str(_watched_folder) if _watched_folder else ""
            scanned_count = sum(1 for e in _events if e.get("result") != "error")
            threats = _threats_found
        return {"ok": True, "running": running, "folder": folder,
                "scanned_count": scanned_count, "threats_found": threats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def download_guard_events(limit: int = 20) -> dict:
    """Return the most recent scan events (newest last)."""
    try:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 20
        if n <= 0:
            n = 20
        with _LOCK:
            events = list(_events)[-n:]
        return {"ok": True, "events": events}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Plain helpers for ui.py / agent.py autostart
# ---------------------------------------------------------------------------
def start(folder=None) -> dict:
    """Plain autostart helper (same as download_guard_start)."""
    return download_guard_start(folder or "")


def is_running() -> bool:
    """True if the background watcher thread is alive."""
    with _LOCK:
        return bool(_running and _thread is not None and _thread.is_alive())


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "download_guard_start",
        "description": "Start real-time download protection: auto-scan new files "
                       "arriving in the Downloads folder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "folder": {"type": "STRING",
                           "description": "folder to watch (default ~/Downloads)"},
            },
            "required": [],
        },
    },
    {
        "name": "download_guard_stop",
        "description": "Stop real-time download protection (the background watcher).",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "download_guard_status",
        "description": "Report whether real-time download protection is running, the "
                       "watched folder, how many files were scanned, and threats found.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "download_guard_events",
        "description": "List the most recent download scan events (clean/threat/error).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {"type": "INTEGER",
                          "description": "how many recent events to return (default 20)"},
            },
            "required": [],
        },
    },
]

TOOL_DISPATCH = {
    "download_guard_start": download_guard_start,
    "download_guard_stop": download_guard_stop,
    "download_guard_status": download_guard_status,
    "download_guard_events": download_guard_events,
}

READONLY_TOOLS = {"download_guard_status", "download_guard_events"}
INTERACTION_TOOLS = {"download_guard_start", "download_guard_stop"}
