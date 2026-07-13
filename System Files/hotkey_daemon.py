"""Make the global summon shortcut work even when Ember is fully QUIT.

Ember's in-app hotkey only works while the process is alive — once you Quit, nothing
is listening. This installs a tiny, always-running login helper (ember_hotkey_listener.py)
that listens for the shortcut and, on press, brings Ember forward: it reuses Ember's
existing single-instance localhost channel to summon a running instance, or launches
Ember if it isn't running. No new IPC of our own — single_instance already does that.

This module is the pure/testable core: the LaunchAgent / .desktop / registry builders
(KeepAlive so the helper always runs) and install/uninstall. The key listening lives in
ember_hotkey_listener.py. The plist/autostart dirs are env-overridable
(EMBER_LAUNCHAGENTS_DIR, EMBER_AUTOSTART_DIR) so install/uninstall are unit-testable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import autostart  # reuse program_args() + _xml_escape() + builders' style

LABEL = "com.ember.aiagent.hotkey"


# ---------------------------------------------------------------------------
# How the helper is launched
# ---------------------------------------------------------------------------

def _listener_script() -> Path:
    return Path(__file__).resolve().parent / "ember_hotkey_listener.py"


def listener_args(combo: str) -> list[str]:
    """argv that runs the always-on key listener for `combo`."""
    if getattr(sys, "frozen", False):
        # A frozen build re-runs itself in listener-only mode (main.py handles the flag).
        return [sys.executable, "--hotkey-listener", "--combo", combo]
    return [sys.executable, str(_listener_script()), "--combo", combo]


# ---------------------------------------------------------------------------
# Pure builders (unit-tested)
# ---------------------------------------------------------------------------

def macos_plist(label: str, args: list[str]) -> str:
    """LaunchAgent for the helper: start at login and ALWAYS keep it running (KeepAlive
    true — unlike the app's login item, the key listener should never stay dead)."""
    arg_xml = "\n".join(f"    <string>{autostart._xml_escape(a)}</string>" for a in args)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key>\n  <string>{autostart._xml_escape(label)}</string>\n'
        '  <key>ProgramArguments</key>\n  <array>\n'
        f'{arg_xml}\n'
        '  </array>\n'
        '  <key>RunAtLoad</key>\n  <true/>\n'
        '  <key>KeepAlive</key>\n  <true/>\n'
        '  <key>ProcessType</key>\n  <string>Background</string>\n'
        '</dict>\n</plist>\n'
    )


def linux_desktop(args: list[str]) -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Ember Hotkey\n"
            f"Exec={' '.join(args)}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Comment=Ember global summon shortcut (works even when Ember is quit)\n")


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def _launchagents_dir() -> Path:
    override = os.environ.get("EMBER_LAUNCHAGENTS_DIR")
    return Path(override) if override else (Path.home() / "Library" / "LaunchAgents")


def _macos_plist_path() -> Path:
    return _launchagents_dir() / f"{LABEL}.plist"


def _linux_desktop_path() -> Path:
    base = os.environ.get("EMBER_AUTOSTART_DIR")
    d = Path(base) if base else (Path.home() / ".config" / "autostart")
    return d / "ember-hotkey.desktop"


def is_installed() -> bool:
    try:
        if sys.platform == "darwin":
            return _macos_plist_path().exists()
        if sys.platform.startswith("win"):
            r = subprocess.run(
                ["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "EmberHotkey"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        return _linux_desktop_path().exists()
    except Exception:
        return False


def install(combo: str = "ctrl+shift+space") -> dict:
    """Install the always-on hotkey helper. Idempotent."""
    try:
        args = listener_args(combo)
        if sys.platform == "darwin":
            p = _macos_plist_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(macos_plist(LABEL, args), "utf-8")
            # Reload so it's active this session too — best-effort (don't fail the install
            # if launchctl is missing/unhappy; the plist on disk is what makes it persist).
            for sub in (["launchctl", "unload", str(p)], ["launchctl", "load", str(p)]):
                try:
                    subprocess.run(sub, capture_output=True, timeout=10)
                except Exception:
                    pass
            return {"ok": True, "method": "launchagent", "path": str(p)}
        if sys.platform.startswith("win"):
            cmd = subprocess.list2cmdline(args)
            r = subprocess.run(
                ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "EmberHotkey", "/t", "REG_SZ", "/d", cmd, "/f"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "reg add failed").strip()}
            # Also start it now so it works without a reboot.
            try:
                subprocess.Popen(args)
            except Exception:
                pass
            return {"ok": True, "method": "registry-run"}
        p = _linux_desktop_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(linux_desktop(args), "utf-8")
        return {"ok": True, "method": "xdg-autostart", "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def uninstall() -> dict:
    try:
        if sys.platform == "darwin":
            p = _macos_plist_path()
            if p.exists():
                try:
                    subprocess.run(["launchctl", "unload", str(p)], capture_output=True, timeout=10)
                except Exception:
                    pass
                p.unlink()
            return {"ok": True}
        if sys.platform.startswith("win"):
            subprocess.run(
                ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "EmberHotkey", "/f"], capture_output=True, text=True, timeout=10)
            return {"ok": True}
        p = _linux_desktop_path()
        if p.exists():
            p.unlink()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_enabled(enabled: bool, combo: str = "ctrl+shift+space") -> dict:
    return install(combo) if enabled else uninstall()


def status() -> dict:
    return {"ok": True, "installed": is_installed(),
            "command": listener_args("ctrl+shift+space")}
