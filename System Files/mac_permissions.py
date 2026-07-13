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


def has_input_monitoring(prompt: bool = False) -> bool:
    """True if Ember has macOS Input Monitoring (needed to LISTEN to global keyboard/mouse,
    e.g. recording a workflow). CRITICAL: pynput's event tap hard-CRASHES the whole process
    if started without this grant, so callers must preflight here and refuse to start when
    it returns False. Returns True off macOS / when the API is unavailable (can't block)."""
    if sys.platform != "darwin":
        return True
    try:
        import Quartz
    except Exception:
        return True  # no pyobjc-Quartz -> can't check; don't hard-block
    try:
        if prompt and hasattr(Quartz, "CGRequestListenEventAccess"):
            try:
                Quartz.CGRequestListenEventAccess()   # shows the system prompt (once)
            except Exception:
                pass
        if hasattr(Quartz, "CGPreflightListenEventAccess"):
            return bool(Quartz.CGPreflightListenEventAccess())
        return True   # older macOS without the preflight API
    except Exception:
        return True


def open_input_monitoring_settings():
    """Open System Settings directly at Privacy -> Input Monitoring."""
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"],
            capture_output=True, timeout=10)
    except Exception:
        pass


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


def has_screen_recording(prompt: bool = False) -> bool:
    """True if Ember has macOS Screen Recording access (needed for Ember Link's mirror and the
    AI's screenshot tool). macOS is SUPPOSED to auto-prompt the first time a screen-capture API
    is used, but that quietly fails to fire for a lot of unsigned/ad-hoc-signed PyInstaller
    builds - the capture call just returns black/empty frames instead, which looks like the app
    never asked. CGRequestScreenCaptureAccess() forces the same registration/prompt explicitly
    instead of hoping an incidental screenshot call triggers it. Returns True off macOS / when
    pyobjc-Quartz is unavailable (can't check, so don't hard-block)."""
    if sys.platform != "darwin":
        return True
    try:
        import Quartz
    except Exception:
        return True
    try:
        if prompt and hasattr(Quartz, "CGRequestScreenCaptureAccess"):
            try:
                Quartz.CGRequestScreenCaptureAccess()
            except Exception:
                pass
        if hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
            return bool(Quartz.CGPreflightScreenCaptureAccess())
        return True   # older macOS without the preflight API
    except Exception:
        return True


def open_screen_recording_settings():
    """Open System Settings directly at Privacy -> Screen Recording."""
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"],
            capture_output=True, timeout=10)
    except Exception:
        pass


def has_microphone(prompt: bool = False) -> bool:
    """True if Ember has macOS Microphone access (needed for push-to-talk / voice chat). Same
    story as Screen Recording: the OS prompt is supposed to fire on first use, but a mic-open
    call buried inside a broad try/except elsewhere in the app can silently eat the resulting
    PermissionError/OSError before the user ever notices a prompt appeared. This asks explicitly
    and up front instead. Returns True off macOS / when pyobjc-AVFoundation is unavailable."""
    if sys.platform != "darwin":
        return True
    try:
        import AVFoundation
    except Exception:
        return True
    try:
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio)
        if status == 3:   # AVAuthorizationStatusAuthorized
            return True
        if prompt and status == 0:   # AVAuthorizationStatusNotDetermined
            try:
                AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVFoundation.AVMediaTypeAudio, lambda granted: None)
            except Exception:
                pass
        return False
    except Exception:
        return True


def open_microphone_settings():
    """Open System Settings directly at Privacy -> Microphone."""
    if sys.platform != "darwin":
        return
    try:
        import subprocess
        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
            capture_output=True, timeout=10)
    except Exception:
        pass
