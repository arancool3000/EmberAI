"""Ember Security Center — unified, always-on active scanning.

This is the supervisor that turns Ember's individual defenses into one continuous,
self-healing protection layer. Where the other modules each watch a single surface,
the Security Center actively and repeatedly scans *every* surface malware uses, on
its own schedule, and keeps the real-time monitors alive:

  * Processes   — keeps fileless_guard's behavioral process monitor running
                  (in-memory / living-off-the-land attacks).
  * Files       — keeps download_guard's real-time Downloads watcher running AND
                  periodically sweeps sensitive folders (Desktop, Documents, Temp…)
                  with the antivirus engine.
  * Network     — repeatedly inspects active connections + listening ports for
                  reverse-shell listeners, C2 / mining traffic and interpreters
                  phoning home.
  * Persistence — repeatedly inspects autostart / persistence locations (cron,
                  launchd, systemd, shell rc files, registry Run keys, Startup
                  folder, scheduled tasks) and scans each entry's command line.
  * Watchdog    — every cycle it restarts any core monitor that has died, so
                  scanning never silently stops.

Everything is funnelled into one bounded, de-duplicated threat feed surfaced via
security_center_status() / security_center_events(), plus on-demand tools
(run_full_scan, scan_network, scan_persistence).

Design (mirrors download_guard / fileless_guard): one daemon thread, bounded event
buffer behind a lock, only stdlib imported at module load (antivirus / the other
guards / psutil are imported lazily inside the workers), and every enumerator is
an injection point so tests run hermetically with synthetic data.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Tunables / state
# ---------------------------------------------------------------------------
_SUPERVISE_TICK = 2.0          # how often the supervisor wakes to check schedules
_EVENTS_MAXLEN = 400           # bounded aggregated threat feed
_FIRST_SWEEP_DELAY = 45        # delay the first (heavier) file sweep after launch

# Ports a backdoor / reverse shell commonly listens on.
_BACKDOOR_PORTS = {1337, 1234, 2323, 4321, 4444, 4445, 5554, 5555, 6666, 6667,
                   7777, 8888, 9001, 9002, 9999, 12345, 12346, 31337, 54321}
# Ports commonly used by crypto-mining pools.
_MINER_PORTS = {3032, 3333, 3357, 4444, 5555, 7777, 8333, 14444, 45560, 45700}
# Interpreters/tools that should rarely hold their own network connections.
_NET_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "nc", "ncat", "netcat", "socat",
               "python", "python3", "python2", "perl", "ruby", "php", "powershell",
               "pwsh", "telnet", "mshta", "rundll32", "regsvr32", "certutil"}

# Injection points for tests:
#   _NET_ENUM():     -> list[{"pid","name","laddr","raddr","lport","rport","status"}]
#   _PERSIST_ENUM(): -> list[{"location","name","command"}]
#   _NOTIFIER(text): -> push a threat alert to connected channels (default integrations.notify)
_NET_ENUM = None
_PERSIST_ENUM = None
_NOTIFIER = None

_LOCK = threading.Lock()
_thread: "threading.Thread | None" = None
_stop_event: "threading.Event | None" = None
_started_at: float = 0.0
_running = False

_events: "deque[dict]" = deque(maxlen=_EVENTS_MAXLEN)
_counts = {"process": 0, "file": 0, "network": 0, "persistence": 0, "watchdog": 0}
_threats = 0
_scan_cycles = 0

# de-dup memory so a standing condition isn't re-alerted every cycle
_net_seen: set = set()
_persist_seen: set = set()
_persist_baseline: set = set()
_persist_baseline_ready = False
_file_seen: set = set()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cfg() -> dict:
    try:
        import antivirus
        return antivirus.get_config()
    except Exception:
        return {}


def _record(source: str, severity: str, detail: str, extra: dict | None = None) -> None:
    """Append one finding to the aggregated feed (severity: info|suspicious|malicious)."""
    global _threats
    entry = {"time": _now_iso(), "source": source, "severity": severity, "detail": detail}
    if extra:
        entry.update(extra)
    with _LOCK:
        _events.append(entry)
        if source in _counts:
            _counts[source] += 1
        if severity in ("suspicious", "malicious"):
            _threats += 1
    _maybe_notify(entry)


def _maybe_notify(entry: dict) -> None:
    """If enabled + a channel is configured, push real threats to Slack/Telegram/etc.
    Runs outside the lock; fully failure-silent."""
    if entry.get("severity") not in ("suspicious", "malicious"):
        return
    try:
        if not _cfg().get("sc_notify", False):
            return
        fn = _NOTIFIER
        if fn is None:
            import integrations
            fn = integrations.notify
        fn(f"🛡️ Ember security [{entry['severity']}] {entry['source']}: "
           f"{str(entry.get('detail',''))[:240]}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Network scanning
# ---------------------------------------------------------------------------

def _enum_connections():
    """Snapshot of network connections. None if it can't be obtained."""
    if _NET_ENUM is not None:
        try:
            return _NET_ENUM()
        except Exception:
            return None
    # psutil gives pid + addresses + state in one call — preferred WHEN it yields anything.
    # On macOS it needs root and often returns [] (or raises AccessDenied) for a normal user,
    # so an empty/failed result must fall through to lsof/netstat instead of reporting "0".
    try:
        import psutil
        out = []
        for c in psutil.net_connections(kind="inet"):
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else ""
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else ""
            name = ""
            if c.pid:
                try:
                    name = psutil.Process(c.pid).name()
                except Exception:
                    name = ""
            out.append({"pid": c.pid, "name": name, "laddr": laddr, "raddr": raddr,
                        "lport": (c.laddr.port if c.laddr else None),
                        "rport": (c.raddr.port if c.raddr else None),
                        "status": c.status})
        if out:
            return out
    except Exception:
        pass
    # lsof works without root for the user's own connections on macOS/Linux and gives the
    # process name + pid + addresses + TCP state — the best fallback.
    res = _enum_connections_lsof()
    if res:
        return res
    return _enum_connections_fallback()


def _enum_connections_lsof():
    """Enumerate connections via `lsof -nP -i` (macOS/Linux). No root needed for the user's own
    processes; yields command name, pid, local/remote addresses and TCP state."""
    if not _which("lsof"):
        return None
    try:
        r = subprocess.run(["lsof", "-nP", "-i"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if not r.stdout:
        return None
    out = []
    for line in r.stdout.splitlines()[1:]:        # skip the COMMAND/PID/... header
        parts = line.split()
        if len(parts) < 9:
            continue
        proto = parts[7].upper()                  # NODE column: TCP / UDP
        if proto not in ("TCP", "UDP"):
            continue
        name = parts[0]
        try:
            pid = int(parts[1])
        except Exception:
            pid = None
        namecol = " ".join(parts[8:])             # NAME col, e.g. "1.2.3.4:51234->5.6.7.8:443 (ESTABLISHED)"
        m = re.search(r"\(([A-Z_]+)\)\s*$", namecol)
        status = m.group(1) if m else ""
        addrspec = re.sub(r"\s*\([A-Z_]+\)\s*$", "", namecol).strip()
        if "->" in addrspec:
            local, remote = (p.strip() for p in addrspec.split("->", 1))
        else:
            local, remote = addrspec.strip(), ""
        if not status:
            status = "ESTABLISHED" if remote else ("LISTEN" if proto == "TCP" else "")
        out.append({"pid": pid, "name": name, "laddr": local, "raddr": remote,
                    "lport": _port_of(local), "rport": _port_of(remote), "status": status})
    return out or None


def _which(cmd: str):
    import shutil
    return shutil.which(cmd)


def _enum_connections_fallback():
    """Best-effort connection list via netstat (no pid/name; ports only)."""
    try:
        r = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=12)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].lower().startswith("tcp"):
            continue
        status = parts[-1].upper()
        local = parts[-3] if len(parts) >= 3 else ""
        remote = parts[-2] if len(parts) >= 2 else ""
        lport = _port_of(local)
        rport = _port_of(remote)
        out.append({"pid": None, "name": "", "laddr": local, "raddr": remote,
                    "lport": lport, "rport": rport, "status": status})
    return out


def _port_of(addr: str):
    try:
        return int(str(addr).rsplit(":", 1)[-1].rsplit(".", 1)[-1])
    except Exception:
        return None


def _basename(name: str) -> str:
    return os.path.basename((name or "").lower()).removesuffix(".exe")


def _bad_ips() -> set:
    try:
        import antivirus
        raw = antivirus._signature_db()
        ips = raw.get("bad_ips") if isinstance(raw, dict) else None
        return set(ips or [])
    except Exception:
        return set()


_OWN_PIDS_CACHE: "set | None" = None
_OWN_PIDS_AT = 0.0


def _own_pids() -> set:
    """Ember's own process tree — so the interpreter-with-a-connection heuristic doesn't flag
    Ember's OWN python (talking to the Gemini/Claude API) as a reverse shell. Cached 30s."""
    global _OWN_PIDS_CACHE, _OWN_PIDS_AT
    now = time.time()
    if _OWN_PIDS_CACHE is not None and now - _OWN_PIDS_AT < 30:
        return _OWN_PIDS_CACHE
    pids = {os.getpid()}
    try:
        pids.add(os.getppid())
    except Exception:
        pass
    try:
        import psutil
        cur = psutil.Process(os.getpid())
        for rel in (cur.parents() + cur.children(recursive=True)):
            pids.add(rel.pid)
    except Exception:
        pass
    _OWN_PIDS_CACHE, _OWN_PIDS_AT = pids, now
    return pids


def _evaluate_connection(c: dict, bad_ips: set) -> tuple[str, str]:
    """Return (severity, detail). severity in clean|suspicious|malicious."""
    status = (c.get("status") or "").upper()
    name = _basename(c.get("name") or "")
    lport, rport = c.get("lport"), c.get("rport")
    raddr = c.get("raddr") or ""
    rip = raddr.rsplit(":", 1)[0] if raddr else ""

    if rip and rip in bad_ips:
        return "malicious", f"connection to known-malicious host {raddr} ({name or 'unknown'})"
    if "LISTEN" in status and lport in _BACKDOOR_PORTS:
        return "suspicious", f"process is listening on backdoor port {lport} ({name or 'unknown'})"
    if status.startswith("ESTAB"):
        # Don't flag Ember's OWN python (it talks to the Gemini/Claude API) as a reverse shell.
        if name in _NET_SHELLS and c.get("pid") not in _own_pids():
            return "suspicious", (f"interpreter '{name}' has an active connection to "
                                  f"{raddr or '?'} (possible reverse shell / C2)")
        if rport in _MINER_PORTS:
            return "suspicious", f"connection to {raddr} on a common mining-pool port {rport}"
    return "clean", ""


def scan_network() -> dict:
    """One-shot scan of network connections for backdoors / C2 / mining / shells.

    Returns a rich result so the UI can be useful even when nothing is flagged: how many
    connections were seen, an established/listening breakdown, the busiest remote hosts, and a
    plain-language summary (incl. a hint when the OS hid connections behind root)."""
    conns = _enum_connections()
    if conns is None:
        return {"ok": False, "error": "could not enumerate network connections",
                "summary": "Couldn't read network connections on this system."}
    bad_ips = _bad_ips()
    flagged = []
    established = listening = 0
    remotes: dict = {}
    for c in conns:
        status = (c.get("status") or "").upper()
        if "LISTEN" in status:
            listening += 1
        elif status.startswith("ESTAB"):
            established += 1
        rip = (c.get("raddr") or "").rsplit(":", 1)[0]
        if rip and not rip.startswith(("127.", "::1")):
            remotes[rip] = remotes.get(rip, 0) + 1
        sev, detail = _evaluate_connection(c, bad_ips)
        if sev in ("suspicious", "malicious"):
            flagged.append({"severity": sev, "detail": detail,
                            "name": c.get("name"), "pid": c.get("pid"),
                            "laddr": c.get("laddr"), "raddr": c.get("raddr"),
                            "status": c.get("status")})
    flagged.sort(key=lambda f: 0 if f["severity"] == "malicious" else 1)
    top_remote = sorted(remotes.items(), key=lambda kv: kv[1], reverse=True)[:5]

    n = len(conns)
    if n == 0:
        summary = ("No active network connections were visible. On macOS some connections are "
                   "only listed with elevated permissions — this isn't a sign of a problem.")
    elif flagged:
        mal = sum(1 for f in flagged if f["severity"] == "malicious")
        summary = (f"Checked {n} connections — {len(flagged)} need attention"
                   + (f" ({mal} malicious)" if mal else "") + ".")
    else:
        summary = (f"Checked {n} connections ({established} active, {listening} listening) — "
                   "all clean. No backdoors, C2, or known-bad hosts.")
    return {"ok": True, "scanned": n, "flagged_count": len(flagged), "flagged": flagged,
            "established": established, "listening": listening,
            "top_remote": [{"ip": ip, "count": cnt} for ip, cnt in top_remote],
            "summary": summary}


def _network_cycle() -> None:
    conns = _enum_connections() or []
    bad_ips = _bad_ips()
    for c in conns:
        sev, detail = _evaluate_connection(c, bad_ips)
        if sev not in ("suspicious", "malicious"):
            continue
        key = ("net", c.get("name"), c.get("laddr"), c.get("raddr"), sev)
        with _LOCK:
            if key in _net_seen:
                continue
            _net_seen.add(key)
        _record("network", sev, detail,
                {"raddr": c.get("raddr"), "name": c.get("name"), "pid": c.get("pid")})


# ---------------------------------------------------------------------------
# Persistence / autostart scanning
# ---------------------------------------------------------------------------

def _read_text(p: Path, limit: int = 8192) -> str:
    try:
        return p.read_text("utf-8", "ignore")[:limit]
    except Exception:
        return ""


def _enum_persistence():
    """Enumerate autostart / persistence entries as {location, name, command}."""
    if _PERSIST_ENUM is not None:
        try:
            return _PERSIST_ENUM()
        except Exception:
            return None
    try:
        if sys.platform.startswith("win"):
            return _enum_persistence_windows()
        return _enum_persistence_posix()
    except Exception:
        return None


def _enum_persistence_posix():
    items: list[dict] = []
    home = Path.home()
    # user crontab
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=8)
        if r.returncode == 0:
            for ln in (r.stdout or "").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    items.append({"location": "user-crontab", "name": ln[:48], "command": ln})
    except Exception:
        pass
    # system cron dirs
    for d in ("/etc/cron.d", "/etc/cron.hourly", "/etc/cron.daily"):
        try:
            for p in Path(d).glob("*"):
                if p.is_file():
                    items.append({"location": f"cron:{d}", "name": p.name,
                                  "command": _read_text(p, 2048)})
        except Exception:
            pass
    # XDG autostart (.desktop Exec=)
    try:
        for p in (home / ".config" / "autostart").glob("*.desktop"):
            txt = _read_text(p)
            m = re.search(r"(?im)^Exec\s*=\s*(.+)$", txt)
            items.append({"location": "xdg-autostart", "name": p.name,
                          "command": (m.group(1).strip() if m else txt[:200])})
    except Exception:
        pass
    # macOS launch agents/daemons
    if sys.platform == "darwin":
        for d in (home / "Library" / "LaunchAgents", Path("/Library/LaunchAgents"),
                  Path("/Library/LaunchDaemons")):
            try:
                for p in d.glob("*.plist"):
                    txt = _read_text(p)
                    args = re.findall(r"<string>([^<]+)</string>", txt)
                    items.append({"location": f"launchd:{d.name}", "name": p.name,
                                  "command": " ".join(args)[:400] or txt[:200]})
            except Exception:
                pass
    # systemd user units
    try:
        for p in (home / ".config" / "systemd" / "user").glob("*.service"):
            txt = _read_text(p)
            m = re.search(r"(?im)^ExecStart\s*=\s*(.+)$", txt)
            if m:
                items.append({"location": "systemd-user", "name": p.name,
                              "command": m.group(1).strip()})
    except Exception:
        pass
    # shell rc files (a classic place to append a curl|bash backdoor)
    for rc in (".bashrc", ".zshrc", ".profile", ".bash_profile", ".bash_login", ".zprofile"):
        f = home / rc
        if f.exists():
            items.append({"location": f"shell-rc:{rc}", "name": rc, "command": _read_text(f)})
    return items


def _enum_persistence_windows():
    items: list[dict] = []
    run_keys = [
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
    ]
    for key in run_keys:
        try:
            r = subprocess.run(["reg", "query", key], capture_output=True, text=True, timeout=10)
            for ln in (r.stdout or "").splitlines():
                parts = re.split(r"\s+REG_\w+\s+", ln.strip(), maxsplit=1)
                if len(parts) == 2 and parts[0].strip():
                    items.append({"location": f"registry:{key.split(chr(92))[-2]}\\Run",
                                  "name": parts[0].strip(), "command": parts[1].strip()})
        except Exception:
            pass
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        sf = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        try:
            for p in sf.iterdir():
                items.append({"location": "startup-folder", "name": p.name, "command": str(p)})
        except Exception:
            pass
    return items


def _evaluate_persistence(item: dict):
    try:
        import antivirus
        res = antivirus.scan_command_line(item.get("command", "") or "")
        return res.get("verdict", "clean"), res.get("reasons", [])
    except Exception:
        return "clean", []


def scan_persistence() -> dict:
    """One-shot scan of autostart / persistence locations for malicious commands."""
    items = _enum_persistence()
    if items is None:
        return {"ok": False, "error": "could not enumerate persistence locations"}
    flagged = []
    for it in items:
        verdict, reasons = _evaluate_persistence(it)
        if verdict in ("suspicious", "malicious"):
            flagged.append({"severity": verdict, "location": it.get("location"),
                            "name": it.get("name"),
                            "detail": "; ".join(reasons) or (it.get("command", "")[:160])})
    flagged.sort(key=lambda f: 0 if f["severity"] == "malicious" else 1)
    return {"ok": True, "scanned": len(items), "flagged_count": len(flagged), "flagged": flagged}


def _persistence_cycle() -> None:
    global _persist_baseline_ready
    items = _enum_persistence() or []
    for it in items:
        key = ("persist", it.get("location"), it.get("command"))
        verdict, reasons = _evaluate_persistence(it)
        if verdict in ("suspicious", "malicious"):
            with _LOCK:
                already = key in _persist_seen
                _persist_seen.add(key)
                _persist_baseline.add(key)
            if not already:
                _record("persistence", verdict,
                        f"{it.get('location')}: {it.get('name')} — "
                        f"{'; '.join(reasons) or it.get('command','')[:120]}", {"item": it})
            continue
        # A brand-new (but clean-looking) autostart entry is still worth noting.
        with _LOCK:
            is_new = _persist_baseline_ready and key not in _persist_baseline
            _persist_baseline.add(key)
        if is_new:
            _record("persistence", "info",
                    f"new autostart entry: {it.get('location')} {it.get('name')}", {"item": it})
    with _LOCK:
        _persist_baseline_ready = True


# ---------------------------------------------------------------------------
# File sweeping
# ---------------------------------------------------------------------------

def _default_roots() -> list[str]:
    home = Path.home()
    roots = [home / "Downloads", home / "Desktop", home / "Documents"]
    tmp = os.environ.get("TMPDIR") or ("/tmp" if not sys.platform.startswith("win") else os.environ.get("TEMP", ""))
    if tmp:
        roots.append(Path(tmp))
    return [str(r) for r in roots]


def _watch_roots(cfg: dict) -> list[str]:
    roots = list(cfg.get("sc_watch_roots") or [])
    roots += _default_roots()
    seen, out = set(), []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _file_sweep(cfg: dict) -> None:
    try:
        import antivirus
    except Exception:
        return
    max_files = int(cfg.get("sc_sweep_max_files", 1500))
    for root in _watch_roots(cfg):
        if not os.path.isdir(root):
            continue
        try:
            r = antivirus.scan_directory(root, deep=False, max_files=max_files)
        except Exception:
            continue
        for f in r.get("flagged", []) or []:
            key = ("file", f.get("path"))
            with _LOCK:
                if key in _file_seen:
                    continue
                _file_seen.add(key)
            verdict = f.get("verdict", "suspicious")
            detail = "; ".join(f.get("reasons", []) or [])[:180] or verdict
            _record("file", verdict, f"{f.get('path')}: {detail}", {"path": f.get("path")})


def run_full_scan(paths: "list[str] | str | None" = None, deep: bool = False) -> dict:
    """On-demand full malware sweep of folders (defaults to all watched roots).
    Confirmed-malicious files are quarantined by the antivirus engine."""
    try:
        import antivirus
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if isinstance(paths, str):
        paths = [paths]
    targets = paths or _watch_roots(_cfg())
    results, total_scanned, total_flagged = [], 0, 0
    for root in targets:
        if not os.path.isdir(root):
            continue
        r = antivirus.scan_directory(root, deep=deep, max_files=int(_cfg().get("sc_sweep_max_files", 1500)))
        if r.get("ok"):
            total_scanned += r.get("scanned", 0)
            total_flagged += r.get("flagged_count", 0)
            results.append({"root": root, "scanned": r.get("scanned", 0),
                            "flagged_count": r.get("flagged_count", 0),
                            "flagged": (r.get("flagged") or [])[:50]})
    return {"ok": True, "roots": len(results), "scanned": total_scanned,
            "flagged_count": total_flagged, "results": results}


# ---------------------------------------------------------------------------
# Watchdog: keep the core real-time monitors alive
# ---------------------------------------------------------------------------

def _ensure_monitors(cfg: dict) -> None:
    if cfg.get("fileless_protection", True):
        try:
            import fileless_guard
            if not fileless_guard.is_running():
                fileless_guard.start()
                _record("watchdog", "info", "started the fileless process monitor")
        except Exception:
            pass
    if cfg.get("scan_downloads", True):
        try:
            import download_guard
            if not download_guard.is_running():
                download_guard.start()
                _record("watchdog", "info", "started the real-time download monitor")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Supervisor loop
# ---------------------------------------------------------------------------

def _supervise(stop: "threading.Event") -> None:
    global _scan_cycles
    now = time.time()
    next_net = now
    next_persist = now
    next_sweep = now + _FIRST_SWEEP_DELAY  # delay the heavier sweep after launch
    while not stop.is_set():
        try:
            now = time.time()
            cfg = _cfg()
            _ensure_monitors(cfg)
            if cfg.get("sc_network_scan", True) and now >= next_net:
                _network_cycle()
                next_net = now + float(cfg.get("sc_network_interval", 20))
            if cfg.get("sc_persistence_scan", True) and now >= next_persist:
                _persistence_cycle()
                next_persist = now + float(cfg.get("sc_persistence_interval", 45))
            if cfg.get("sc_file_sweep", True) and now >= next_sweep:
                _file_sweep(cfg)
                next_sweep = now + float(cfg.get("sc_file_sweep_interval", 600))
            with _LOCK:
                _scan_cycles += 1
        except Exception:
            pass  # a cycle hiccup must never kill the supervisor
        stop.wait(_SUPERVISE_TICK)


def _initial_sweep() -> None:
    """Immediate first pass of the cheap surfaces so threats already present surface
    at once (the file sweep is deferred to avoid a heavy disk hit on launch)."""
    try:
        _network_cycle()
    except Exception:
        pass
    try:
        _persistence_cycle()
    except Exception:
        pass


def security_center_start() -> dict:
    """Start the unified always-on Security Center. Idempotent.

    Brings up the real-time monitors, runs an immediate network + persistence
    sweep, then supervises everything continuously."""
    global _thread, _stop_event, _running, _started_at
    try:
        with _LOCK:
            if _running and _thread is not None and _thread.is_alive():
                return {"ok": True, "running": True,
                        "message": "Security Center is already active"}
        # Bring up (or confirm) the core real-time monitors first.
        _ensure_monitors(_cfg())
        _initial_sweep()
        with _LOCK:
            _stop_event = threading.Event()
            stop = _stop_event
            _thread = threading.Thread(target=_supervise, args=(stop,),
                                       name="ember-security-center", daemon=True)
            _running = True
            _started_at = time.time()
            _thread.start()
        return {"ok": True, "running": True,
                "message": "Security Center active — continuously scanning processes, "
                           "files, network and persistence"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def security_center_stop() -> dict:
    """Stop the Security Center supervisor (leaves the individual monitors as-is)."""
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
            return {"ok": True, "message": "Security Center was not running"}
        if stop is not None:
            stop.set()
        thread.join(timeout=5.0)
        return {"ok": True, "message": "Security Center stopped"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def security_center_status() -> dict:
    """Aggregated status of every protection layer + the live threat tally."""
    try:
        with _LOCK:
            running = bool(_running and _thread is not None and _thread.is_alive())
            counts = dict(_counts)
            threats = _threats
            cycles = _scan_cycles
            uptime = int(time.time() - _started_at) if _started_at else 0
            last = _events[-1] if _events else None
        monitors = {}
        try:
            import fileless_guard
            monitors["process_monitor"] = fileless_guard.is_running()
        except Exception:
            monitors["process_monitor"] = None
        try:
            import download_guard
            monitors["download_monitor"] = download_guard.is_running()
        except Exception:
            monitors["download_monitor"] = None
        cfg = _cfg()
        return {"ok": True, "running": running, "uptime_seconds": uptime,
                "scan_cycles": cycles, "threats_found": threats,
                "by_source": counts, "monitors": monitors,
                "network_scan": cfg.get("sc_network_scan", True),
                "persistence_scan": cfg.get("sc_persistence_scan", True),
                "file_sweep": cfg.get("sc_file_sweep", True),
                "last_event": last}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def security_center_events(limit: int = 30) -> dict:
    """Return the most recent aggregated security events (newest last)."""
    try:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 30
        if n <= 0:
            n = 30
        with _LOCK:
            events = list(_events)[-n:]
        return {"ok": True, "events": events}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Plain helpers for ui.py / agent.py autostart
# ---------------------------------------------------------------------------

def start() -> dict:
    return security_center_start()


def is_running() -> bool:
    with _LOCK:
        return bool(_running and _thread is not None and _thread.is_alive())


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "security_center_start",
        "description": "Start Ember's always-on Security Center: continuous active "
                       "scanning of processes, files, network connections and "
                       "persistence/autostart, with a self-healing watchdog.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "security_center_stop",
        "description": "Stop the always-on Security Center supervisor.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "security_center_status",
        "description": "Aggregated security status: which monitors are live, scan "
                       "cycles run, total threats found, and counts per surface "
                       "(process/file/network/persistence).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "security_center_events",
        "description": "List the most recent aggregated security findings across all "
                       "surfaces (process, file, network, persistence, watchdog).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"limit": {"type": "INTEGER",
                                     "description": "how many recent events (default 30)"}},
            "required": [],
        },
    },
    {
        "name": "run_full_scan",
        "description": "Run an on-demand full malware sweep of folders (defaults to all "
                       "watched roots: Downloads, Desktop, Documents, Temp). "
                       "Confirmed-malicious files are quarantined.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "paths": {"type": "ARRAY", "items": {"type": "STRING"},
                          "description": "specific folders to scan (optional)"},
                "deep": {"type": "BOOLEAN", "description": "also consult VirusTotal (optional)"},
            },
            "required": [],
        },
    },
    {
        "name": "scan_network",
        "description": "Scan active network connections + listening ports for "
                       "backdoors, C2 / reverse shells, mining traffic and "
                       "interpreters phoning home. Read-only.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "scan_persistence",
        "description": "Scan autostart / persistence locations (cron, launchd, systemd, "
                       "shell rc files, registry Run keys, Startup folder) for malicious "
                       "commands. Read-only.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH = {
    "security_center_start": security_center_start,
    "security_center_stop": security_center_stop,
    "security_center_status": security_center_status,
    "security_center_events": security_center_events,
    "run_full_scan": run_full_scan,
    "scan_network": scan_network,
    "scan_persistence": scan_persistence,
}

READONLY_TOOLS = {"security_center_status", "security_center_events",
                  "scan_network", "scan_persistence"}
INTERACTION_TOOLS = {"security_center_start", "security_center_stop", "run_full_scan"}
