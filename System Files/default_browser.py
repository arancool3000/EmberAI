"""Which browser the user actually uses — and whether Ember can drive it.

Ember has two ways to touch the web, and it kept picking the wrong one:

* ``open_url`` hands a URL to the OS, which opens it in **your** default browser with
  **your** session — you stay signed in, and an already-open window is reused.
* the ``browser_*`` tools speak the Chrome DevTools Protocol, which gives Ember real DOM
  control (reading elements, filling forms) but only works with a Chromium browser, and
  historically launched Chrome against a *separate automation profile* — which is exactly
  why a signed-in page turned into a fresh window asking you to sign in again.

This module supplies the missing fact: what the default browser is, and whether it can be
driven over CDP. With that, Ember can use your own browser whenever DOM control isn't
needed, prefer *your* Chromium browser over hunting for Chrome when it is, and tell you
plainly when the two can't be reconciled (Safari and Firefox don't speak CDP).

The identification and ranking logic is pure and table-driven so it is unit-tested without
launching anything; only :func:`detect` touches the OS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

#: Browsers Ember understands, keyed by a short id.
#: ``cdp`` marks the ones that can be driven over the DevTools Protocol.
BROWSERS = {
    "chrome": {"name": "Google Chrome", "cdp": True,
               "bundles": ("com.google.chrome",),
               "mac": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
               "win": (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                       r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                       r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
               "linux": ("google-chrome", "google-chrome-stable")},
    "edge": {"name": "Microsoft Edge", "cdp": True,
             "bundles": ("com.microsoft.edgemac", "msedge"),
             "mac": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
             "win": (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                     r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
             "linux": ("microsoft-edge",)},
    "brave": {"name": "Brave", "cdp": True,
              "bundles": ("com.brave.browser",),
              "mac": ("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",),
              "win": (r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",),
              "linux": ("brave-browser", "brave")},
    "vivaldi": {"name": "Vivaldi", "cdp": True,
                "bundles": ("com.vivaldi.vivaldi",),
                "mac": ("/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",),
                "win": (r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",),
                "linux": ("vivaldi", "vivaldi-stable")},
    "arc": {"name": "Arc", "cdp": True,
            "bundles": ("company.thebrowser.browser",),
            "mac": ("/Applications/Arc.app/Contents/MacOS/Arc",),
            "win": (), "linux": ()},
    "chromium": {"name": "Chromium", "cdp": True,
                 "bundles": ("org.chromium.chromium",),
                 "mac": ("/Applications/Chromium.app/Contents/MacOS/Chromium",),
                 "win": (), "linux": ("chromium", "chromium-browser")},
    # No CDP. Ember can still open URLs in these — it just can't read or drive the DOM.
    "safari": {"name": "Safari", "cdp": False,
               "bundles": ("com.apple.safari",),
               "mac": ("/Applications/Safari.app/Contents/MacOS/Safari",),
               "win": (), "linux": ()},
    "firefox": {"name": "Firefox", "cdp": False,
                "bundles": ("org.mozilla.firefox",),
                "mac": ("/Applications/Firefox.app/Contents/MacOS/Firefox",),
                "win": (r"C:\Program Files\Mozilla Firefox\firefox.exe",),
                "linux": ("firefox",)},
}


# ---------------------------------------------------------------------------
# Identification (pure)
# ---------------------------------------------------------------------------

def identify(handler: str) -> Optional[str]:
    """Map an OS handler string to a browser id.

    Accepts whatever each platform reports: a macOS bundle id
    (``com.google.chrome``), a Windows ProgId (``ChromeHTML``,
    ``MSEdgeHTM``, ``FirefoxURL-…``), or a Linux desktop file
    (``firefox.desktop``, ``google-chrome.desktop``).
    """
    h = str(handler or "").strip().lower()
    if not h:
        return None
    for key, spec in BROWSERS.items():
        for bundle in spec["bundles"]:
            if h == bundle or h.startswith(bundle):
                return key
    # ProgId / desktop-file forms don't contain the bundle id, so fall back to matching
    # the browser's own name inside the string.
    for key in ("chromium", "chrome", "edge", "brave", "vivaldi", "arc", "safari", "firefox"):
        if key in h:
            return key
    if "msedge" in h:
        return "edge"
    return None


def can_drive(browser_id: Optional[str]) -> bool:
    """Whether Ember can take DOM-level control of this browser."""
    spec = BROWSERS.get(str(browser_id or ""))
    return bool(spec and spec["cdp"])


def display_name(browser_id: Optional[str]) -> str:
    spec = BROWSERS.get(str(browser_id or ""))
    return spec["name"] if spec else "your default browser"


def _expand_windows_vars(path: str) -> str:
    """Expand ``%VAR%`` regardless of the host OS.

    ``os.path.expandvars`` only understands the ``%VAR%`` form when it is actually running
    on Windows, so resolving the Windows table from anywhere else left the literal
    ``%LOCALAPPDATA%`` in the path. An unexpanded variable would silently never match.
    """
    import re

    def sub(match):
        return os.environ.get(match.group(1), match.group(0))

    return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", sub, str(path))


def binary_for(browser_id: str, *, platform: Optional[str] = None,
               exists=None, which=None) -> Optional[str]:
    """Executable path for a browser id, or None if it isn't installed.

    ``exists``/``which`` are injectable so the lookup table can be tested without the
    browsers actually being present.
    """
    spec = BROWSERS.get(str(browser_id or ""))
    if not spec:
        return None
    platform = platform or sys.platform
    exists = exists or (lambda p: Path(p).exists())
    which = which or shutil.which
    if platform == "darwin":
        candidates = spec["mac"]
    elif platform.startswith("win"):
        candidates = tuple(_expand_windows_vars(p) for p in spec["win"])
    else:
        return next((w for w in (which(c) for c in spec["linux"]) if w), None)
    for path in candidates:
        if path and exists(path):
            return path
    return None


def rank_for_control(default_id: Optional[str], installed: list) -> list:
    """Order Chromium browsers to try for DOM control, best first.

    The user's own default comes first when it can be driven. Ember used to hunt for
    Chrome regardless, which is why someone whose daily browser is Brave or Edge kept
    getting a Chrome window they never asked for.
    """
    ordered = []
    if default_id and can_drive(default_id) and default_id in installed:
        ordered.append(default_id)
    for key in ("chrome", "edge", "brave", "vivaldi", "chromium", "arc"):
        if key in installed and key not in ordered and can_drive(key):
            ordered.append(key)
    return ordered


# ---------------------------------------------------------------------------
# Detection (touches the OS)
# ---------------------------------------------------------------------------

def _mac_handler() -> Optional[str]:
    """Bundle id registered for http:// in LaunchServices."""
    try:
        from Foundation import NSURL  # type: ignore
        from AppKit import NSWorkspace  # type: ignore
        url = NSWorkspace.sharedWorkspace().URLForApplicationToOpenURL_(
            NSURL.URLWithString_("https://example.com"))
        if url is not None:
            # …/Google Chrome.app -> com.google.chrome, via the bundle itself.
            from Foundation import NSBundle  # type: ignore
            bundle = NSBundle.bundleWithURL_(url)
            if bundle is not None:
                return str(bundle.bundleIdentifier() or "") or str(url.path() or "")
            return str(url.path() or "")
    except Exception:
        pass
    # No pyobjc: read the LaunchServices preference directly.
    try:
        plist = (Path.home() / "Library/Preferences/com.apple.LaunchServices"
                 / "com.apple.launchservices.secure.plist")
        out = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(plist)],
                             capture_output=True, text=True, timeout=6).stdout
        # The http handler is the entry whose scheme block names "http".
        import re
        for block in re.findall(r"<dict>(.*?)</dict>", out, re.DOTALL):
            if ">http<" in block and "LSHandlerRoleAll" in block:
                m = re.search(r"<key>LSHandlerRoleAll</key>\s*<string>([^<]+)</string>", block)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _windows_handler() -> Optional[str]:
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\Shell\Associations"
               r"\UrlAssociations\https\UserChoice")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return str(winreg.QueryValueEx(k, "ProgId")[0])
    except Exception:
        return None


def _linux_handler() -> Optional[str]:
    for cmd in (["xdg-settings", "get", "default-web-browser"],
                ["xdg-mime", "query", "default", "x-scheme-handler/https"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            continue
    return None


def detect() -> dict:
    """What the default browser is and what Ember can do with it.

    Returns ``{"id", "name", "handler", "can_drive", "note"}``. Never raises — an
    undetectable default is reported as unknown, not as an error.
    """
    if sys.platform == "darwin":
        handler = _mac_handler()
    elif sys.platform.startswith("win"):
        handler = _windows_handler()
    else:
        handler = _linux_handler()

    bid = identify(handler or "")
    drive = can_drive(bid)
    if bid is None:
        note = ("Ember couldn't tell which browser is your default, so it will hand URLs "
                "to the OS — they'll still open wherever you normally browse.")
    elif drive:
        note = (f"{display_name(bid)} is your default and Ember can drive it directly, "
                f"using your own session.")
    else:
        note = (f"{display_name(bid)} is your default. Ember opens links there with your "
                f"own logins, but {display_name(bid)} has no automation protocol, so "
                f"reading or filling a page needs a Chromium browser instead.")
    return {"id": bid, "name": display_name(bid), "handler": handler or "",
            "can_drive": drive, "note": note}


def installed_drivable(platform: Optional[str] = None) -> list:
    """Chromium browsers actually present on this machine."""
    return [key for key, spec in BROWSERS.items()
            if spec["cdp"] and binary_for(key, platform=platform)]


def status() -> dict:
    """One call for the agent: the default browser plus what can be driven."""
    info = detect()
    drivable = installed_drivable()
    return {**info,
            "drivable_installed": drivable,
            "control_order": rank_for_control(info.get("id"), drivable)}
