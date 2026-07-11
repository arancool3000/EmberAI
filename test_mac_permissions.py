"""Hermetic tests for mac_permissions.py's Screen Recording + Microphone helpers.

Runs on Linux CI: sys.platform is monkeypatched to "darwin" for the duration of each test (and
restored after) so the darwin-only code paths execute, with fake Quartz/AVFoundation/subprocess
modules standing in for pyobjc and the OS - no real macOS, no real prompts.
Run: python test_mac_permissions.py"""
import sys
import types

import mac_permissions as mp

_REAL_PLATFORM = sys.platform


def _darwin():
    sys.platform = "darwin"


def _restore():
    sys.platform = _REAL_PLATFORM
    sys.modules.pop("Quartz", None)
    sys.modules.pop("AVFoundation", None)
    sys.modules.pop("CoreAudio", None)


def test_screen_recording_true_off_macos():
    _restore()
    assert mp.has_screen_recording() is True


def test_screen_recording_true_when_pyobjc_missing():
    _darwin()
    sys.modules.pop("Quartz", None)
    try:
        assert mp.has_screen_recording() is True   # can't check -> don't hard-block
    finally:
        _restore()


def test_screen_recording_reports_granted():
    _darwin()
    q = types.ModuleType("Quartz")
    q.CGPreflightScreenCaptureAccess = lambda: True
    q.CGRequestScreenCaptureAccess = lambda: True
    sys.modules["Quartz"] = q
    try:
        assert mp.has_screen_recording() is True
    finally:
        _restore()


def test_screen_recording_reports_not_granted_and_prompts_when_asked():
    _darwin()
    calls = []
    q = types.ModuleType("Quartz")
    q.CGPreflightScreenCaptureAccess = lambda: False
    q.CGRequestScreenCaptureAccess = lambda: calls.append("requested") or False
    sys.modules["Quartz"] = q
    try:
        assert mp.has_screen_recording(prompt=False) is False
        assert calls == []           # prompt=False must never trigger the OS prompt
        assert mp.has_screen_recording(prompt=True) is False
        assert calls == ["requested"]
    finally:
        _restore()


def test_open_screen_recording_settings_calls_open_with_the_right_url():
    _darwin()
    captured = {}
    import subprocess as real_subprocess
    orig_run = real_subprocess.run
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: pass
        return R()
    real_subprocess.run = fake_run
    try:
        mp.open_screen_recording_settings()
        assert captured["cmd"][0] == "open"
        assert "Privacy_ScreenCapture" in captured["cmd"][1]
    finally:
        real_subprocess.run = orig_run
        _restore()


def test_microphone_true_off_macos():
    _restore()
    assert mp.has_microphone() is True


def test_microphone_true_when_pyobjc_missing():
    _darwin()
    sys.modules.pop("AVFoundation", None)
    try:
        assert mp.has_microphone() is True
    finally:
        _restore()


def test_microphone_authorized_status():
    _darwin()
    av = types.ModuleType("AVFoundation")
    av.AVMediaTypeAudio = "soun"

    class _Dev:
        @staticmethod
        def authorizationStatusForMediaType_(_t):
            return 3   # AVAuthorizationStatusAuthorized
    av.AVCaptureDevice = _Dev
    sys.modules["AVFoundation"] = av
    try:
        assert mp.has_microphone() is True
    finally:
        _restore()


def test_microphone_not_determined_prompts_only_when_asked():
    _darwin()
    calls = []
    av = types.ModuleType("AVFoundation")
    av.AVMediaTypeAudio = "soun"

    class _Dev:
        @staticmethod
        def authorizationStatusForMediaType_(_t):
            return 0   # AVAuthorizationStatusNotDetermined

        @staticmethod
        def requestAccessForMediaType_completionHandler_(_t, cb):
            calls.append("requested")
            cb(True)
    av.AVCaptureDevice = _Dev
    sys.modules["AVFoundation"] = av
    try:
        assert mp.has_microphone(prompt=False) is False
        assert calls == []
        assert mp.has_microphone(prompt=True) is False   # still reports not-yet-granted
        assert calls == ["requested"]
    finally:
        _restore()


def test_microphone_denied_status_is_false():
    _darwin()
    av = types.ModuleType("AVFoundation")
    av.AVMediaTypeAudio = "soun"

    class _Dev:
        @staticmethod
        def authorizationStatusForMediaType_(_t):
            return 2   # AVAuthorizationStatusDenied
    av.AVCaptureDevice = _Dev
    sys.modules["AVFoundation"] = av
    try:
        assert mp.has_microphone(prompt=True) is False
    finally:
        _restore()


def test_open_microphone_settings_calls_open_with_the_right_url():
    _darwin()
    captured = {}
    import subprocess as real_subprocess
    orig_run = real_subprocess.run
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: pass
        return R()
    real_subprocess.run = fake_run
    try:
        mp.open_microphone_settings()
        assert captured["cmd"][0] == "open"
        assert "Privacy_Microphone" in captured["cmd"][1]
    finally:
        real_subprocess.run = orig_run
        _restore()


# --- System Audio Recording (the "Screen & System Audio Recording" pane) -------------------

def _fake_quartz(preflight: bool):
    q = types.ModuleType("Quartz")
    q.CGPreflightScreenCaptureAccess = lambda: preflight
    q.CGRequestScreenCaptureAccess = lambda: preflight
    sys.modules["Quartz"] = q
    return q


def test_system_audio_true_off_macos():
    _restore()
    assert mp.has_system_audio_recording() is True


def test_system_audio_true_when_pyobjc_missing():
    _darwin()
    sys.modules.pop("Quartz", None)
    try:
        assert mp.has_system_audio_recording() is True   # can't check -> don't hard-block
    finally:
        _restore()


def test_system_audio_reports_screen_capture_status():
    # System audio is authorised under the Screen Recording TCC service, so it tracks that status.
    _darwin()
    _fake_quartz(preflight=True)
    try:
        assert mp.has_system_audio_recording() is True
    finally:
        _restore()
    _darwin()
    _fake_quartz(preflight=False)
    try:
        assert mp.has_system_audio_recording() is False
    finally:
        _restore()


def test_system_audio_prompt_triggers_the_audio_tap_only_when_asked():
    _darwin()
    _fake_quartz(preflight=False)
    calls = []

    class _Desc:
        @staticmethod
        def alloc():
            return _Desc()

        def initStereoGlobalTapButExcludeProcesses_(self, procs):
            return self

    ca = types.ModuleType("CoreAudio")
    ca.CATapDescription = _Desc
    ca.AudioHardwareCreateProcessTap = lambda desc, out: calls.append("create") or (0, 99)
    ca.AudioHardwareDestroyProcessTap = lambda tid: calls.append(("destroy", tid))
    sys.modules["CoreAudio"] = ca
    try:
        mp.has_system_audio_recording(prompt=False)
        assert calls == []                       # prompt=False must never touch the OS
        mp.has_system_audio_recording(prompt=True)
        assert "create" in calls                 # prompt=True surfaces the system-audio consent
        assert ("destroy", 99) in calls          # and cleans up the throwaway tap
    finally:
        _restore()


def test_system_audio_tap_is_a_safe_noop_without_the_binding():
    # macOS but no CoreAudio process-tap API (older OS / missing binding) must not raise.
    _darwin()
    _fake_quartz(preflight=True)
    sys.modules.pop("CoreAudio", None)
    try:
        assert mp.has_system_audio_recording(prompt=True) is True
    finally:
        _restore()


def test_open_system_audio_settings_calls_open_with_the_right_url():
    _darwin()
    captured = {}
    import subprocess as real_subprocess
    orig_run = real_subprocess.run

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: pass
        return R()
    real_subprocess.run = fake_run
    try:
        mp.open_system_audio_settings()
        assert captured["cmd"][0] == "open"
        assert "Privacy_ScreenCapture" in captured["cmd"][1]
    finally:
        real_subprocess.run = orig_run
        _restore()


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            _restore()
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
