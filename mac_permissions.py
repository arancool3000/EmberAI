"""macOS permission helpers for Ember.

This module intentionally contains only permission prompts/settings shortcuts. It does not
register macOS Services or any system-wide selected-text menu items.
"""
from __future__ import annotations

import sys


def request_accessibility(prompt: bool = True) -> bool:
    """Ask macOS for Accessibility access, which Ember needs for mouse/keyboard control.

    macOS does not auto-prompt for Accessibility the way it does for Screen Recording, so Ember
    explicitly calls AXIsProcessTrustedWithOptions. Returns True if already trusted. No-ops off
    macOS.
    """
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        try:
            from ApplicationServices import kAXTrustedCheckOptionPrompt as key
        except Exception:
            key = "AXTrustedCheckOptionPrompt"
        return bool(AXIsProcessTrustedWithOptions({key: bool(prompt)}))
    except Exception:
        return False


def open_accessibility_settings():
    """Open System Settings directly at Privacy -> Accessibility."""
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
