"""Auto-protect: watch a folder and encrypt whatever lands in it, before it syncs.

This is what closes the phone gap on the desktop side. Turn iCloud Photos off, point the
iPhone's imports at a folder on this machine, and point this watcher at that folder with the
destination set to iCloud Drive. Photos arriving from the phone are encrypted on this machine
and only the ciphertext ever syncs.

It is equally useful without a phone in the picture: any folder you drop files into becomes a
protected one.

Design notes
------------
* Polling, not filesystem events. Ember already ships a polling folder watcher
  (``extra_tools.watch_folder_start``) and adding a native-notification dependency for this
  would be a new install requirement on two platforms for no behavioural gain at these
  intervals.
* A file is only encrypted once it has stopped changing — same size and mtime across two
  consecutive polls, and at least ``min_age`` seconds old. Encrypting a photo still being
  copied off a phone would otherwise produce a perfectly valid encryption of half a JPEG,
  which is worse than failing: it looks like success.
* ``.ember`` outputs are skipped, so pointing the destination inside the source folder cannot
  feed the watcher its own output.
* Shredding originals is off by default. When on, it only runs after the encrypted file is
  written and verified to exist.

The configuration persists in the data dir so the watcher can resume at launch —
``resume_if_enabled()`` is what ui.py calls on startup.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import data_protect

_MAX_EVENTS = 200
_DEFAULT_INTERVAL = 5.0
_DEFAULT_MIN_AGE = 2.0

_STATE: dict = {"thread": None, "stop": None, "events": [], "started": 0.0,
                "protected": 0, "failed": 0}
_LOCK = threading.Lock()


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


#: Module-level so tests can point it at a temp dir.
CONFIG_FILE = _data_dir() / "adp_watch.json"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_config() -> dict:
    try:
        p = Path(CONFIG_FILE)
        if p.exists():
            c = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(c, dict):
                return c
    except Exception:
        pass
    return {}


def save_config(cfg: dict) -> None:
    p = Path(CONFIG_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _event(kind: str, **fields) -> None:
    with _LOCK:
        _STATE["events"].append({"at": time.time(), "kind": kind, **fields})
        if len(_STATE["events"]) > _MAX_EVENTS:
            del _STATE["events"][:-_MAX_EVENTS]


# ---------------------------------------------------------------------------
# The watch loop
# ---------------------------------------------------------------------------
def _candidates(root: Path, all_files: bool):
    """Files in `root` that this watcher would consider protecting."""
    try:
        for f in sorted(root.rglob("*")):
            if f.suffix == data_protect.EXT:
                continue  # never re-protect our own output
            if not data_protect._selected(f, all_files):
                continue
            yield f
    except OSError:
        return


def _dest_for(f: Path, root: Path, out_root: Path | None) -> Path:
    out_dir = (out_root / f.parent.relative_to(root)) if out_root else f.parent
    return out_dir / (f.name + data_protect.EXT)


def _stable(f: Path, seen: dict, min_age: float) -> bool:
    """True once the file has stopped changing.

    A photo still being copied off a phone would otherwise encrypt cleanly as a half file —
    a valid ciphertext of truncated data, which reads as success and isn't.
    """
    try:
        st = f.stat()
    except OSError:
        return False
    key = str(f)
    sig = (st.st_size, st.st_mtime)
    was = seen.get(key)
    seen[key] = sig
    if was != sig:
        return False  # changed since the last poll — wait for it to settle
    return (time.time() - st.st_mtime) >= min_age


def _protect_one(f: Path, root: Path, out_root: Path | None, cfg: dict, pw: str) -> None:
    dest = _dest_for(f, root, out_root)
    if dest.exists():
        return
    err = data_protect._check_source(f)
    if err:
        _event("skipped", path=str(f), reason=err)
        return
    try:
        data_protect.encrypt_file(f, dest, pw, data_protect.load_recipients())
    except Exception as e:
        with _LOCK:
            _STATE["failed"] += 1
        _event("failed", path=str(f), error=str(e))
        return
    with _LOCK:
        _STATE["protected"] += 1
    _event("protected", path=str(f), dest=str(dest))
    # Only ever shred after the encrypted file is on disk.
    if cfg.get("delete_originals") and dest.exists():
        if data_protect._shred(f):
            _event("shredded", path=str(f))


def _loop(cfg: dict, stop: threading.Event) -> None:
    root = Path(cfg["source"]).expanduser()
    out_root = Path(cfg["dest"]).expanduser() if cfg.get("dest") else None
    all_files = bool(cfg.get("all_files", True))
    interval = float(cfg.get("interval", _DEFAULT_INTERVAL))
    min_age = float(cfg.get("min_age", _DEFAULT_MIN_AGE))
    seen: dict = {}
    while not stop.is_set():
        pw = data_protect._passphrase()
        if pw is None:
            # Protection was reset while watching. Keep the thread alive but do nothing, so the
            # watcher doesn't silently die and leave the folder unprotected without saying why.
            _event("idle", reason="data protection is not set up")
            stop.wait(interval)
            continue
        try:
            for f in _candidates(root, all_files):
                if stop.is_set():
                    break
                if _stable(f, seen, min_age):
                    _protect_one(f, root, out_root, cfg, pw)
        except Exception as e:
            _event("failed", path=str(root), error=str(e))
        stop.wait(interval)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
def is_running() -> bool:
    t = _STATE.get("thread")
    return bool(t is not None and t.is_alive())


def start(source: str, dest: str = "", delete_originals: bool = False,
          all_files: bool = True, interval: float = _DEFAULT_INTERVAL,
          min_age: float = _DEFAULT_MIN_AGE, persist: bool = True) -> dict:
    """Begin watching `source`, encrypting into `dest` (or alongside)."""
    if not data_protect.is_set_up():
        return {"ok": False, "error": "data protection is not set up — run adp_setup first"}
    root = Path(str(source)).expanduser()
    if not root.is_dir():
        return {"ok": False, "error": f"no such folder: {root}"}
    if is_running():
        return {"ok": False, "error": "already watching — stop it first"}
    cfg = {"source": str(root), "dest": str(Path(str(dest)).expanduser()) if dest else "",
           "delete_originals": bool(delete_originals), "all_files": bool(all_files),
           "interval": float(interval), "min_age": float(min_age), "enabled": True}
    stop = threading.Event()
    t = threading.Thread(target=_loop, args=(cfg, stop), daemon=True,
                         name="ember-adp-watch")
    with _LOCK:
        _STATE.update(thread=t, stop=stop, config=cfg, started=time.time(),
                      protected=0, failed=0, events=[])
    t.start()
    if persist:
        save_config(cfg)
    _event("started", source=cfg["source"], dest=cfg["dest"])
    return {"ok": True, "watching": cfg["source"], "dest": cfg["dest"] or "alongside originals",
            "delete_originals": cfg["delete_originals"], "all_files": cfg["all_files"]}


def stop_watching(persist: bool = True) -> dict:
    """Stop the watcher. Leaves already-protected files alone."""
    ev = _STATE.get("stop")
    t = _STATE.get("thread")
    if ev is None or not is_running():
        if persist:
            cfg = load_config()
            if cfg:
                cfg["enabled"] = False
                save_config(cfg)
        return {"ok": True, "was_running": False}
    ev.set()
    t.join(timeout=5.0)
    with _LOCK:
        _STATE["thread"] = None
    if persist:
        cfg = load_config()
        if cfg:
            cfg["enabled"] = False
            save_config(cfg)
    _event("stopped")
    return {"ok": True, "was_running": True}


def resume_if_enabled() -> dict:
    """Restart a previously-enabled watcher at launch. Called by ui.py on startup."""
    cfg = load_config()
    if not cfg.get("enabled") or not cfg.get("source"):
        return {"ok": True, "resumed": False}
    if not data_protect.is_set_up():
        return {"ok": True, "resumed": False, "reason": "data protection is not set up"}
    r = start(cfg["source"], cfg.get("dest", ""), cfg.get("delete_originals", False),
              cfg.get("all_files", True), cfg.get("interval", _DEFAULT_INTERVAL),
              cfg.get("min_age", _DEFAULT_MIN_AGE), persist=False)
    return {"ok": bool(r.get("ok")), "resumed": bool(r.get("ok")), "detail": r}


# ---------------------------------------------------------------------------
# Tools (exposed to the LLM)
# ---------------------------------------------------------------------------
def adp_watch_start(folder: str, dest_dir: str = "", delete_originals: bool = False,
                    all_files: bool = True) -> dict:
    """Continuously encrypt anything that appears in a folder."""
    return start(folder, dest_dir, delete_originals, all_files)


def adp_watch_stop() -> dict:
    """Stop auto-protecting a folder."""
    return stop_watching()


def adp_watch_status() -> dict:
    """Report whether auto-protect is running, what it is watching, and what it has done."""
    cfg = _STATE.get("config") or load_config()
    with _LOCK:
        events = list(_STATE["events"][-20:])
    return {"ok": True, "running": is_running(),
            "source": cfg.get("source", ""), "dest": cfg.get("dest", ""),
            "delete_originals": bool(cfg.get("delete_originals")),
            "all_files": bool(cfg.get("all_files", True)),
            "protected_count": _STATE.get("protected", 0),
            "failed_count": _STATE.get("failed", 0),
            "recent_events": events}


TOOL_DECLARATIONS = [
    {
        "name": "adp_watch_start",
        "description": "Continuously encrypt anything that appears in a folder, before it syncs "
                       "to iCloud. Point it at the folder your phone imports into, with the "
                       "destination set to iCloud Drive.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder to watch"},
            "dest_dir": {"type": "STRING", "description": "where to write the encrypted files, e.g. iCloud Drive; defaults to alongside the originals"},
            "delete_originals": {"type": "BOOLEAN", "description": "securely shred each original once it has been encrypted (default false)"},
            "all_files": {"type": "BOOLEAN", "description": "protect every file type, not just photos and videos (default true)"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_watch_stop",
        "description": "Stop auto-protecting a folder. Files already encrypted are unaffected.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_watch_status",
        "description": "Report whether folder auto-protect is running, what it is watching, and "
                       "what it has encrypted or failed on recently.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH = {
    "adp_watch_start": adp_watch_start,
    "adp_watch_stop": adp_watch_stop,
    "adp_watch_status": adp_watch_status,
}

READONLY_TOOLS = {"adp_watch_status"}
INTERACTION_TOOLS = {"adp_watch_start", "adp_watch_stop"}
