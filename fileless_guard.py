"""Real-time fileless-malware protection for Ember (always active).

Fileless / "living-off-the-land" attacks leave little or nothing on disk — they
run entirely in memory and abuse trusted tools (PowerShell, certutil, mshta,
rundll32, bash, python…) to download-and-execute payloads, open reverse shells,
dump credentials, mine crypto or wipe shadow copies. A file scanner never sees
them, so this module watches the one place they DO show up: running processes
and their command lines.

A single background daemon thread samples the process table every few seconds.
On start it sweeps everything already running (surfacing threats that predate
the watcher), then flags only NEW processes after that. Each process's command
line is classified by antivirus.scan_command_line() — the shared behavioral
IOC/signature engine — and combined with a process-lineage check (e.g. Word or a
script host spawning PowerShell, the classic macro-dropper pattern). Findings go
into a bounded in-memory event log surfaced via the status/events tools; if
`fileless_auto_terminate` is enabled in the security config, confirmed-malicious
processes are also killed. By default it only alerts (never kills) so a heuristic
can't take down a legitimate process.

Design notes (mirrors download_guard.py — daemon thread + bounded events buffer
behind a lock, lazy heavy imports, fully injectable for tests):
  * No hard dependency: psutil is used when present (it already is a dependency)
    and otherwise we fall back to `ps` / `wmic`. The module imports nothing heavy
    at top level.
  * `_SCANNER` and `_ENUMERATOR` are module-level injection points so tests can
    feed a synthetic process table + verdict without touching the real system.
  * State is in-memory only — nothing is persisted to disk.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
_POLL_SECONDS = 4.0           # how often the watcher samples the process table
_EVENTS_MAXLEN = 200          # bounded history of detections

# Process-lineage red flags: a document app or script host should not be
# spawning a shell / interpreter — that is the hallmark of a macro/exploit dropper.
_SUSPECT_PARENTS = {
    "winword.exe", "excel.exe", "powerpnt.exe", "outlook.exe", "msaccess.exe",
    "onenote.exe", "wscript.exe", "cscript.exe", "mshta.exe", "winword", "excel",
    "powerpnt", "outlook", "acrord32.exe", "soffice.bin",
}
_SUSPECT_CHILDREN = {
    "powershell.exe", "powershell", "pwsh.exe", "pwsh", "cmd.exe", "cmd",
    "wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe", "regsvr32.exe",
    "bitsadmin.exe", "certutil.exe", "curl.exe", "bash", "sh", "zsh",
    "python.exe", "python", "python3",
}

# Injection points for tests:
#   _SCANNER:    callable(cmdline:str, name:str) -> dict like antivirus.scan_command_line
#   _ENUMERATOR: callable() -> list[{"pid","ppid","name","cmdline"}]
_SCANNER = None
_ENUMERATOR = None

# ---------------------------------------------------------------------------
# Module-level state (all guarded by _LOCK)
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_thread: "threading.Thread | None" = None
_stop_event: "threading.Event | None" = None
_events: "deque[dict]" = deque(maxlen=_EVENTS_MAXLEN)
_seen: set[str] = set()
_running = False
_threats_found = 0
_processes_scanned = 0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Process enumeration  (psutil preferred; ps / wmic fallback)
# ---------------------------------------------------------------------------

def _enumerate_psutil():
    try:
        import psutil  # lazy: optional, keeps import light + testable
    except Exception:
        return None
    out = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            info = p.info
            cmd = info.get("cmdline") or []
            out.append({
                "pid": info.get("pid"),
                "ppid": info.get("ppid"),
                "name": info.get("name") or "",
                "cmdline": " ".join(cmd) if cmd else (info.get("name") or ""),
            })
        except Exception:
            continue
    return out


def _enumerate_posix():
    try:
        r = subprocess.run(["ps", "-eo", "pid=,ppid=,args="],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pid_s, ppid_s = parts[0], parts[1]
        args = parts[2] if len(parts) >= 3 else ""
        try:
            pid, ppid = int(pid_s), int(ppid_s)
        except ValueError:
            continue
        first = args.split()[0] if args.split() else ""
        out.append({"pid": pid, "ppid": ppid,
                    "name": os.path.basename(first), "cmdline": args})
    return out


def _enumerate_windows():
    try:
        r = subprocess.run(
            ["wmic", "process", "get",
             "ProcessId,ParentProcessId,Name,CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    out, cur = [], {}

    def _flush():
        if not cur:
            return
        try:
            out.append({
                "pid": int(cur.get("ProcessId", "0") or 0),
                "ppid": int(cur.get("ParentProcessId", "0") or 0),
                "name": cur.get("Name", "") or "",
                "cmdline": cur.get("CommandLine", "") or cur.get("Name", "") or "",
            })
        except Exception:
            pass

    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            _flush()
            cur = {}
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.strip()
    _flush()
    return out


def _enumerate_processes():
    """Best-effort snapshot of running processes. None if it can't be obtained."""
    if _ENUMERATOR is not None:
        try:
            return _ENUMERATOR()
        except Exception:
            return None
    procs = _enumerate_psutil()
    if procs:
        return procs
    if sys.platform.startswith("win"):
        return _enumerate_windows()
    return _enumerate_posix()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _do_cmd_scan(cmdline: str, name: str = "") -> dict:
    """Classify a command line via the injected scanner, else antivirus."""
    scanner = _SCANNER
    if scanner is None:
        import antivirus  # lazy: the shared behavioral IOC/signature engine
        scanner = antivirus.scan_command_line
    return scanner(cmdline, name)


def _evaluate(proc: dict, name_by_pid: dict) -> dict:
    """Score a single process. Returns an event dict with a verdict."""
    name = (proc.get("name") or "")
    cmd = proc.get("cmdline") or ""
    res = _do_cmd_scan(cmd, name.lower())
    verdict = res.get("verdict", "clean")
    reasons = list(res.get("reasons", []) or [])
    cats = list(res.get("categories", []) or [])
    has_hits = bool(res.get("hits"))

    # Process lineage: a document/script-host parent spawning a shell is a
    # textbook fileless dropper, even when the command line itself looks plain.
    parent = (name_by_pid.get(proc.get("ppid")) or "").lower()
    pbase = os.path.basename(parent)
    cbase = os.path.basename(name.lower())
    if pbase in _SUSPECT_PARENTS and cbase in _SUSPECT_CHILDREN:
        reasons.append(f"suspicious process lineage: {parent or '?'} -> {name} "
                       "(document/script host spawning a shell)")
        if "lineage" not in cats:
            cats.append("lineage")
        # Lineage alone -> at least suspicious; lineage + a command-line IOC -> malicious.
        verdict = "malicious" if has_hits else max(verdict, "suspicious", key=_VRANK.get)

    return {
        "verdict": verdict,
        "pid": proc.get("pid"),
        "ppid": proc.get("ppid"),
        "name": name,
        "parent": parent,
        "cmdline": cmd[:400],
        "categories": sorted(set(cats)),
        "reasons": reasons,
    }


_VRANK = {"clean": 0, "suspicious": 1, "malicious": 2}


# Injectable kill hook (tests set this to a no-op so on-by-default auto-terminate
# can never kill a REAL process that happens to share a synthetic test PID).
_TERMINATOR = None


def _terminate(pid) -> bool:
    """Best-effort kill of a process (used only when auto-terminate is enabled)."""
    if _TERMINATOR is not None:
        try:
            return bool(_TERMINATOR(pid))
        except Exception:
            return False
    try:
        try:
            import psutil
            psutil.Process(int(pid)).kill()
            return True
        except Exception:
            pass
        if not sys.platform.startswith("win"):
            os.kill(int(pid), 9)
            return True
        subprocess.run(["taskkill", "/F", "/PID", str(int(pid))],
                       capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def _auto_terminate_enabled() -> bool:
    try:
        import antivirus
        return bool(antivirus.get_config().get("fileless_auto_terminate", False))
    except Exception:
        return False


def _poll_seconds() -> float:
    try:
        import antivirus
        return float(antivirus.get_config().get("fileless_poll_seconds", _POLL_SECONDS))
    except Exception:
        return _POLL_SECONDS


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------

def _proc_key(proc: dict) -> str:
    return f"{proc.get('pid')}:{hash(proc.get('cmdline') or '')}"


def _record(ev: dict, action: str = "") -> None:
    global _threats_found
    entry = {
        "time": _now_iso(),
        "pid": ev.get("pid"),
        "name": ev.get("name"),
        "parent": ev.get("parent"),
        "verdict": ev.get("verdict"),
        "categories": ev.get("categories", []),
        "detail": "; ".join(ev.get("reasons", []) or []) or ev.get("verdict", ""),
        "cmdline": ev.get("cmdline", ""),
        "action": action or "alerted",
    }
    with _LOCK:
        _events.append(entry)
        if ev.get("verdict") in ("suspicious", "malicious"):
            _threats_found += 1


def _handle(ev: dict) -> None:
    """Apply the configured response to a flagged process and record it."""
    action = "alerted"
    if ev.get("verdict") == "malicious" and _auto_terminate_enabled():
        action = "terminated" if _terminate(ev.get("pid")) else "terminate_failed"
    _record(ev, action)


# ---------------------------------------------------------------------------
# One-shot scan (agent tool)
# ---------------------------------------------------------------------------

def scan_processes(include_clean: bool = False) -> dict:
    """Scan every running process once for fileless-malware behavior.

    Returns {ok, scanned, flagged_count, flagged:[...]} — flagged holds the
    suspicious/malicious processes (worst first). Read-only: it never kills."""
    global _processes_scanned
    try:
        procs = _enumerate_processes()
        if procs is None:
            return {"ok": False, "error": "could not enumerate running processes"}
        name_by_pid = {p.get("pid"): (p.get("name") or "") for p in procs}
        flagged, clean = [], 0
        for p in procs:
            try:
                ev = _evaluate(p, name_by_pid)
            except Exception:
                continue
            if ev["verdict"] in ("suspicious", "malicious"):
                flagged.append(ev)
            else:
                clean += 1
        flagged.sort(key=lambda e: -_VRANK.get(e["verdict"], 0))
        with _LOCK:
            _processes_scanned += len(procs)
        out = {"ok": True, "scanned": len(procs), "clean": clean,
               "flagged_count": len(flagged), "flagged": flagged[:100]}
        if include_clean:
            out["all"] = [{"pid": p.get("pid"), "name": p.get("name")} for p in procs]
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scan_command(command: str) -> dict:
    """Classify a single command line / script snippet for fileless-malware
    techniques (encoded PowerShell, download-exec, reverse shells, LOLBins,
    ransomware, credential dumping, miners, obfuscation). Read-only."""
    try:
        import antivirus
        return {"ok": True, **antivirus.scan_command_line(command or "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Background daemon
# ---------------------------------------------------------------------------

def _watch_loop(stop: "threading.Event") -> None:
    """Sample the process table; flag processes that are NEW since the last poll.
    The initial sweep (at start) is handled before this loop runs."""
    global _processes_scanned
    while not stop.is_set():
        try:
            procs = _enumerate_processes() or []
            name_by_pid = {p.get("pid"): (p.get("name") or "") for p in procs}
            for proc in procs:
                key = _proc_key(proc)
                with _LOCK:
                    known = key in _seen
                if known:
                    continue
                with _LOCK:
                    _seen.add(key)
                    _processes_scanned += 1
                try:
                    ev = _evaluate(proc, name_by_pid)
                except Exception:
                    continue
                if ev["verdict"] in ("suspicious", "malicious"):
                    _handle(ev)
            # Forget pids that are gone so a recycled pid is re-evaluated.
            live = {_proc_key(p) for p in procs}
            with _LOCK:
                stale = _seen - live
                if len(stale) > 4000:  # bounded; avoid unbounded growth
                    _seen.intersection_update(live)
        except Exception:
            pass  # a polling hiccup must never kill the watcher
        stop.wait(_poll_seconds())


def _initial_sweep() -> int:
    """Evaluate everything already running once, recording any threats, and prime
    the seen-set so the loop only reports processes that start afterwards."""
    procs = _enumerate_processes() or []
    name_by_pid = {p.get("pid"): (p.get("name") or "") for p in procs}
    threats = 0
    for proc in procs:
        with _LOCK:
            _seen.add(_proc_key(proc))
        try:
            ev = _evaluate(proc, name_by_pid)
        except Exception:
            continue
        if ev["verdict"] in ("suspicious", "malicious"):
            _handle(ev)
            threats += 1
    with _LOCK:
        global _processes_scanned
        _processes_scanned += len(procs)
    return threats


def fileless_guard_start() -> dict:
    """Start always-on fileless-malware protection. Idempotent.

    Performs an initial sweep of running processes (so pre-existing threats are
    surfaced immediately), then watches for newly launched processes."""
    global _thread, _stop_event, _running
    try:
        with _LOCK:
            if _running and _thread is not None and _thread.is_alive():
                return {"ok": True, "watching": True,
                        "message": "fileless protection is already running"}
            _seen.clear()
        threats = _initial_sweep()
        with _LOCK:
            _stop_event = threading.Event()
            stop = _stop_event
            _thread = threading.Thread(target=_watch_loop, args=(stop,),
                                       name="ember-fileless-guard", daemon=True)
            _running = True
            _thread.start()
        return {"ok": True, "watching": True,
                "initial_threats": threats,
                "message": f"real-time fileless protection active "
                           f"(initial sweep flagged {threats} process(es))"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fileless_guard_stop() -> dict:
    """Stop the background fileless-malware watcher."""
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
            return {"ok": True, "message": "fileless protection was not running"}
        if stop is not None:
            stop.set()
        thread.join(timeout=5.0)
        return {"ok": True, "message": "fileless protection stopped"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fileless_guard_status() -> dict:
    """Report whether real-time fileless protection is running and what it found."""
    try:
        with _LOCK:
            running = bool(_running and _thread is not None and _thread.is_alive())
            scanned = _processes_scanned
            threats = _threats_found
            last = _events[-1] if _events else None
        return {"ok": True, "running": running, "processes_scanned": scanned,
                "threats_found": threats, "auto_terminate": _auto_terminate_enabled(),
                "last_detection": last}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fileless_guard_events(limit: int = 20) -> dict:
    """Return the most recent fileless-malware detections (newest last)."""
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

def start() -> dict:
    """Plain autostart helper (same as fileless_guard_start)."""
    return fileless_guard_start()


def is_running() -> bool:
    """True if the background watcher thread is alive."""
    with _LOCK:
        return bool(_running and _thread is not None and _thread.is_alive())


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "scan_processes",
        "description": "Scan all running processes once for FILELESS malware / "
                       "living-off-the-land attacks (encoded PowerShell, "
                       "download-and-execute, reverse shells, LOLBins, ransomware, "
                       "credential dumping, miners) and suspicious process lineage. "
                       "Read-only; returns flagged processes worst-first.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "include_clean": {"type": "BOOLEAN",
                                  "description": "also list clean processes (default false)"},
            },
            "required": [],
        },
    },
    {
        "name": "scan_command",
        "description": "Analyze a single command line or script snippet for "
                       "fileless-malware techniques. Verdict: clean | suspicious | "
                       "malicious, with the matched indicator categories.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {"type": "STRING", "description": "the command/script text to analyze"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "fileless_guard_start",
        "description": "Start always-on real-time fileless-malware protection "
                       "(background process/behavior monitor).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "fileless_guard_stop",
        "description": "Stop real-time fileless-malware protection (the background monitor).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "fileless_guard_status",
        "description": "Report whether real-time fileless protection is running, how "
                       "many processes were scanned, threats found, and the last detection.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "fileless_guard_events",
        "description": "List the most recent fileless-malware detections (process, "
                       "verdict, reason, action).",
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
    "scan_processes": scan_processes,
    "scan_command": scan_command,
    "fileless_guard_start": fileless_guard_start,
    "fileless_guard_stop": fileless_guard_stop,
    "fileless_guard_status": fileless_guard_status,
    "fileless_guard_events": fileless_guard_events,
}

READONLY_TOOLS = {"scan_processes", "scan_command",
                  "fileless_guard_status", "fileless_guard_events"}
INTERACTION_TOOLS = {"fileless_guard_start", "fileless_guard_stop"}
