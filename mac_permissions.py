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


def _request_system_audio_tap() -> None:
    """Best-effort: surface the macOS 14.4+ "record this computer's audio" consent by creating a
    throwaway Core Audio process tap, then destroying it. This is the dedicated 'System Audio
    Recording Only' prompt macOS shows for audio-only capture. Never raises; a clean no-op when the
    pyobjc CoreAudio binding or the OS is too old (the Screen Recording umbrella below still
    authorises system audio captured via ScreenCaptureKit)."""
    tap_id = None
    try:
        import CoreAudio
        tap_desc_cls = getattr(CoreAudio, "CATapDescription", None)
        create = getattr(CoreAudio, "AudioHardwareCreateProcessTap", None)
        if tap_desc_cls is None or create is None:
            return
        # A global tap of all system audio, excluding no processes -> the broadest consent prompt.
        desc = tap_desc_cls.alloc().initStereoGlobalTapButExcludeProcesses_([])
        result = create(desc, None)
        # pyobjc returns the out-param tapID alongside the OSStatus; tolerate either shape.
        tap_id = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
    except Exception:
        pass
    finally:
        try:
            if tap_id:
                import CoreAudio
                destroy = getattr(CoreAudio, "AudioHardwareDestroyProcessTap", None)
                if destroy:
                    destroy(tap_id)
        except Exception:
            pass


def has_system_audio_recording(prompt: bool = False) -> bool:
    """True if Ember may record this Mac's SYSTEM audio — the sound coming out of the speakers,
    used for song ID, "what just played" transcription and the screen mirror's audio track (this
    is separate from Microphone, which only captures the user's voice).

    On macOS this consent lives in the SAME "Screen & System Audio Recording" privacy pane as
    Screen Recording: system audio captured via ScreenCaptureKit is gated by the Screen Recording
    TCC service, and macOS 15 adds a dedicated per-app system-audio toggle in that pane. macOS is
    supposed to prompt on first capture, but that quietly fails to fire for a lot of unsigned /
    ad-hoc-signed builds (the same reason Screen Recording and the mic don't auto-ask), so Ember
    triggers it explicitly. Returns True off macOS / when the APIs are unavailable (can't check,
    so don't hard-block)."""
    if sys.platform != "darwin":
        return True
    if prompt:
        # The dedicated audio-only consent (macOS 14.4+). Best-effort — see _request_system_audio_tap.
        _request_system_audio_tap()
    # Status signal: system audio is authorised under the Screen Recording service, so its preflight
    # doubles as ours. (We don't re-prompt for screen here — the caller requests that separately.)
    return has_screen_recording(prompt=False)


def open_system_audio_settings():
    """Open System Settings at Privacy -> Screen & System Audio Recording (the combined pane that
    holds the system-audio toggle on macOS Sequoia and the Screen Recording list before it)."""
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
