"""Launch Ember at login (and relaunch on crash) so "Hey Ember" always works.

Ember's wake word runs inside the app; for it to respond when the app isn't open
at all, the app needs to be running. This installs an OS login item that starts
Ember at login and brings it back if it crashes:

  * macOS  -> a LaunchAgent plist in ~/Library/LaunchAgents (RunAtLoad + relaunch
              on abnormal exit; a clean Quit stays quit).
  * Windows -> an HKCU ...\\CurrentVersion\\Run registry entry.
  * Linux  -> a ~/.config/autostart/ember.desktop entry.

The plist / entry builders are pure functions (unit-tested); install()/uninstall()
shell out and degrade gracefully. Stdlib only.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.ember.aiagent.login"


# ---------------------------------------------------------------------------
# What command launches Ember
# ---------------------------------------------------------------------------

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _macos_app_bundle() -> "Path | None":
    """If running from a frozen .app, return the .../Ember.app path."""
    p = Path(sys.executable)
    for parent in p.parents:
        if parent.suffix == ".app":
            return parent
    return None


def program_args() -> list[str]:
    """The argv that (re)launches Ember on this platform/build."""
    if sys.platform == "darwin":
        app = _macos_app_bundle()
        if app is not None:
            return ["/usr/bin/open", str(app)]
        launcher = _base_dir() / "Ember.command"
        if launcher.exists():
            return ["/bin/bash", str(launcher)]
        return [sys.executable, str(_base_dir() / "main.py")]
    if sys.platform.startswith("win"):
        base = _base_dir()
        if getattr(sys, "frozen", False):
            return [sys.executable]
        bat = base / "run.bat"
        if bat.exists():
            return ["cmd", "/c", str(bat)]
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw) if pyw.exists() else sys.executable
        return [exe, str(base / "main.py")]
    # Linux
    base = _base_dir()
    sh = base / "run.sh"
    if sh.exists():
        return ["/bin/bash", str(sh)]
    return [sys.executable, str(base / "main.py")]


# ---------------------------------------------------------------------------
# Pure builders (unit-tested)
# ---------------------------------------------------------------------------

def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def macos_plist(label: str, args: list[str]) -> str:
    """Build a LaunchAgent plist: start at login, relaunch only on abnormal exit."""
    arg_xml = "\n".join(f"    <string>{_xml_escape(a)}</string>" for a in args)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'  <key>Label</key>\n  <string>{_xml_escape(label)}</string>\n'
        '  <key>ProgramArguments</key>\n  <array>\n'
        f'{arg_xml}\n'
        '  </array>\n'
        '  <key>RunAtLoad</key>\n  <true/>\n'
        '  <key>KeepAlive</key>\n  <dict>\n'
        '    <key>SuccessfulExit</key>\n    <false/>\n  </dict>\n'
        '  <key>ProcessType</key>\n  <string>Interactive</string>\n'
        '</dict>\n</plist>\n'
    )


def linux_desktop(args: list[str]) -> str:
    exec_line = " ".join(args)
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Ember\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Comment=Ember AI agent (Hey Ember wake word)\n")


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "ember.desktop"


def is_installed() -> bool:
    try:
        if sys.platform == "darwin":
            return _macos_plist_path().exists()
        if sys.platform.startswith("win"):
            r = subprocess.run(
                ["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "Ember"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        return _linux_desktop_path().exists()
    except Exception:
        return False


def install() -> dict:
    """Install the login item so Ember starts at login. Idempotent."""
    try:
        args = program_args()
        if sys.platform == "darwin":
            p = _macos_plist_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(macos_plist(LABEL, args), "utf-8")
            # Reload so it's active this session too (ignore errors on older launchctl).
            subprocess.run(["launchctl", "unload", str(p)], capture_output=True, timeout=10)
            subprocess.run(["launchctl", "load", str(p)], capture_output=True, timeout=10)
            return {"ok": True, "method": "launchagent", "path": str(p)}
        if sys.platform.startswith("win"):
            cmd = subprocess.list2cmdline(args)
            r = subprocess.run(
                ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "Ember", "/t", "REG_SZ", "/d", cmd, "/f"],
                capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or "reg add failed").strip()}
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
                subprocess.run(["launchctl", "unload", str(p)], capture_output=True, timeout=10)
                p.unlink()
            return {"ok": True}
        if sys.platform.startswith("win"):
            subprocess.run(
                ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                 "/v", "Ember", "/f"], capture_output=True, text=True, timeout=10)
            return {"ok": True}
        p = _linux_desktop_path()
        if p.exists():
            p.unlink()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_enabled(enabled: bool) -> dict:
    return install() if enabled else uninstall()


def status() -> dict:
    return {"ok": True, "installed": is_installed(), "command": program_args()}
