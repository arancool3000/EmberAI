"""Headless EmberConnect — runs ONLY the phone-remote server, no Ember GUI.

The boot LaunchAgent runs this at login so your phone can control this Mac even if Ember
isn't open — e.g. if the keyboard/mouse drivers fail, you open the URL on your phone and
drive the Mac. Uses the stable PIN (same every boot).

Install it to run at login with INSTALL_PHONE_REMOTE.command.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import remote_server


def main():
    r = remote_server.start()
    if not r.get("ok"):
        print("EmberConnect failed to start:", r.get("error"))
        # keep the process alive so KeepAlive doesn't thrash; retry on next boot
        time.sleep(3600)
        return
    print(r.get("hint", r))
    try:
        d = remote_server._data_dir()
        (d / "remote_url.txt").write_text(f"{r.get('url', '')}   PIN {r.get('pin', '')}\n")
    except Exception:
        pass
    # Keep the process (and its daemon server thread) alive forever.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
