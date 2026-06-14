"""Single-instance enforcement + summon-IPC over a localhost socket."""
from __future__ import annotations

import socket
import threading
from typing import Callable

LOCK_PORT = 17654
SUMMON_MSG = b"SUMMON\n"


def _bind_listener() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # REUSEADDR so a crash that left the port in TIME_WAIT doesn't lock us out on the next
    # launch. A *live* instance still holds the listen socket, so bind still fails for it
    # (we detect that via the connect-probe below) — this only frees stale TIME_WAIT locks.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", LOCK_PORT))
    s.listen(1)
    return s


def acquire_or_summon() -> socket.socket | None:
    """Returns the bound listener socket if this is the first instance, else None.
    When None is returned the existing instance has been told to summon itself; caller should exit."""
    try:
        return _bind_listener()
    except OSError:
        pass
    # Bind failed. Probe: is a live instance actually listening, or is this a stale lock?
    try:
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(1.0)
        c.connect(("127.0.0.1", LOCK_PORT))
        c.sendall(SUMMON_MSG)
        c.close()
        return None  # a live instance answered and was told to summon itself
    except OSError:
        pass
    # Nothing answered — the lock was stale. Try to claim it once more.
    try:
        return _bind_listener()
    except OSError:
        return None


def listen_for_summon(listener: socket.socket, on_summon: Callable[[], None]):
    """Spawn a background thread that accepts SUMMON messages and calls the callback."""
    def _loop():
        while True:
            try:
                conn, _ = listener.accept()
                with conn:
                    data = conn.recv(64)
                    if SUMMON_MSG.strip() in data:
                        try:
                            on_summon()
                        except Exception:
                            pass
            except OSError:
                return
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def kill_old_debugai():
    """Find and terminate any leftover DebugAI.exe / DebugAI pythonw processes.
    Returns the number of processes killed."""
    killed = 0
    # DebugAI is a Windows-only legacy artifact. On macOS/Linux this scan just iterates every
    # process (slow on launch) for nothing, so skip it.
    import sys as _sys
    if not _sys.platform.startswith("win"):
        return 0
    try:
        import psutil
    except ImportError:
        return 0
    for p in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if (
                "debugai" in name
                or "debugai" in exe
                or ("debugai" in cmd and ("python" in name or "ember" in name))
            ):
                p.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed
