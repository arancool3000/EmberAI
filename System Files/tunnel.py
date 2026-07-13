"""Public tunnel for Ember Link — reach your computer from OUTSIDE your Wi-Fi.

Ember Link normally binds to the LAN. To use it from anywhere, the desktop opens an OUTBOUND
tunnel (Cloudflare Tunnel by default — free, no account, gives an HTTPS URL) that forwards a
public URL to the local Ember Link port. Combined with the pairing-token auth in remote_server
(a phone pairs on Wi-Fi, gets a long secret, then connects from anywhere), this is the
"start together, then roam" flow — without exposing a short PIN to the internet.

The URL parser is a PURE function (`parse_tunnel_url`) and the process spawn is injectable, so the
manager is unit-tested without launching anything. Importing this module pulls only stdlib.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

# trycloudflare quick-tunnel URL, and ngrok forwarding URL, as printed to the tunnel's output.
_CF_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com", re.IGNORECASE)
_NGROK_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.ngrok(?:-free)?\.(?:app|io|dev)", re.IGNORECASE)


def parse_tunnel_url(text: str) -> str:
    """Extract the first public https tunnel URL from a line/blob of tunnel output, or ''."""
    if not text:
        return ""
    m = _CF_RE.search(text) or _NGROK_RE.search(text)
    return m.group(0) if m else ""


def cloudflared_available() -> bool:
    return bool(shutil.which("cloudflared"))


def install_hint() -> str:
    import sys
    if sys.platform == "darwin":
        return "Install Cloudflare Tunnel: brew install cloudflared"
    if sys.platform.startswith("win"):
        return "Install Cloudflare Tunnel: winget install --id Cloudflare.cloudflared"
    return "Install cloudflared from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"


def _spawn_cloudflared(port: int):
    """Launch a Cloudflare quick tunnel to the local port. Returns the Popen."""
    return subprocess.Popen(
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{int(port)}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)


class TunnelManager:
    """Owns one tunnel process and the public URL it reports. `spawn(port)->proc` is injectable
    (proc needs .stdout (iterable of lines), .terminate(), .poll()) so tests use a fake."""

    def __init__(self, spawn=None):
        self._spawn = spawn or _spawn_cloudflared
        self._proc = None
        self._url = ""
        self._error = ""
        self._thread = None
        self._lock = threading.RLock()

    def _read(self, proc):
        try:
            for line in (proc.stdout or []):
                u = parse_tunnel_url(line if isinstance(line, str) else str(line))
                with self._lock:
                    # A reader draining an OLD process's buffered stdout (after stop()+start())
                    # must never clobber the NEW tunnel's URL - otherwise toggling remote access
                    # off/on can leave the dialog showing/copying a dead tunnel URL. Bail the
                    # moment this isn't the current process, and only ever set a URL once.
                    if self._proc is not proc:
                        return
                    if self._url:
                        return
                    if u:
                        self._url = u
                        return
        except Exception as e:
            with self._lock:
                if self._proc is proc:
                    self._error = str(e)

    def start(self, port: int, wait: float = 12.0) -> dict:
        """Start the tunnel and wait up to `wait` seconds for it to report a public URL."""
        with self._lock:
            if self._proc is not None:
                return {"ok": bool(self._url), "url": self._url, "already_running": True}
        if self._spawn is _spawn_cloudflared and not cloudflared_available():
            return {"ok": False, "error": "cloudflared is not installed", "install": install_hint()}
        try:
            proc = self._spawn(port)
        except Exception as e:
            return {"ok": False, "error": f"could not start tunnel: {e}"}
        with self._lock:
            self._proc = proc
            self._url = ""
            self._error = ""
        self._thread = threading.Thread(target=self._read, args=(proc,), daemon=True)
        self._thread.start()
        deadline = wait
        step = 0.1
        waited = 0.0
        while waited < deadline:
            with self._lock:
                if self._url:
                    return {"ok": True, "url": self._url}
                if self._proc is None or (hasattr(proc, "poll") and proc.poll() is not None):
                    return {"ok": False, "error": self._error or "tunnel exited before giving a URL"}
            time.sleep(step)
            waited += step
        return {"ok": False, "error": "tunnel did not report a URL in time (still trying in background)",
                "url": self._url}

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._url = ""
        if proc is not None:
            try:
                proc.terminate()
                # Reap the child so a terminated cloudflared doesn't linger as a zombie; escalate
                # to kill() if it ignores SIGTERM.
                if hasattr(proc, "wait"):
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            except Exception:
                pass
        return {"ok": True, "stopped": bool(proc)}

    def status(self) -> dict:
        with self._lock:
            running = self._proc is not None
            return {"running": running, "url": self._url, "error": self._error}
