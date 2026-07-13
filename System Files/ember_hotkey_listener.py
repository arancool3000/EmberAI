"""Tiny always-on global-shortcut listener — runs at login (installed by hotkey_daemon)
so the summon shortcut works even when Ember itself is fully quit.

On the shortcut it brings Ember forward by reusing Ember's existing single-instance
channel: it sends SUMMON to the localhost lock port (a running Ember answers and shows
itself); if nothing is listening, Ember isn't running, so it launches it. Kept minimal —
a background process, supervised by the OS login item (KeepAlive) and its own retry loop.

Usage:  python ember_hotkey_listener.py --combo ctrl+shift+space
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time


def _summon(*_a) -> None:
    """Foreground a running Ember (via its single-instance socket), or launch it."""
    try:
        import single_instance
        port, msg = single_instance.LOCK_PORT, single_instance.SUMMON_MSG
    except Exception:
        port, msg = 17654, b"SUMMON\n"
    try:
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(1.0)
        c.connect(("127.0.0.1", port))
        c.sendall(msg)
        c.close()
        return                      # a running instance answered and was told to summon
    except OSError:
        pass                        # nothing listening -> Ember isn't running, launch it
    try:
        import autostart
        subprocess.Popen(autostart.program_args())
    except Exception:
        pass


def _pynput_combo(combo: str) -> str:
    """Convert 'ctrl+shift+space' to pynput's '<ctrl>+<shift>+<space>' form."""
    is_mac = sys.platform == "darwin"
    parts = []
    for k in combo.split("+"):
        k = k.strip().lower()
        if not k:
            continue
        if is_mac and k in ("win", "super", "meta"):
            k = "cmd"
        parts.append(f"<{k}>" if len(k) > 1 else k)
    return "+".join(parts)


def run(combo: str) -> int:
    """Supervised listen loop. Returns nonzero only if pynput is entirely unavailable."""
    try:
        from pynput import keyboard as pk
    except Exception as e:
        print(f"[ember-hotkey] pynput unavailable: {e}", file=sys.stderr)
        return 2
    pyn = _pynput_combo(combo)
    backoff = 1.0
    while True:
        try:
            with pk.GlobalHotKeys({pyn: _summon}) as h:
                backoff = 1.0
                h.join()
        except Exception as e:
            # macOS event taps can die on odd key events / permission hiccups; just restart.
            print(f"[ember-hotkey] listener restart after: {e}", file=sys.stderr)
            time.sleep(min(backoff, 10.0))
            backoff *= 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--combo", default="ctrl+shift+space")
    ns = ap.parse_args(argv)
    return run(ns.combo)


if __name__ == "__main__":
    raise SystemExit(main())
