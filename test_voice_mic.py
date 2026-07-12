"""Hermetic microphone backend tests: PyAudio failure must fall back to sounddevice."""
import os
import sys
import tempfile
import time
import types

import audio_level
import voice

# extra_tools.record_audio uses Ember's portable mic backend and needs no network, but the
# extra_tools MODULE does `import requests` at load for its HTTP tools. The minimal CI env
# (only rapidfuzz installed) has no requests, so stub it — requests is used only inside other
# functions, never at import time — letting these record_audio tests run everywhere.
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except Exception:
        sys.modules["requests"] = types.ModuleType("requests")
import extra_tools


class _BrokenMicrophone:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("PyAudio is not installed")


def _with_broken_pyaudio():
    previous = sys.modules.get("speech_recognition")
    sys.modules["speech_recognition"] = types.SimpleNamespace(Microphone=_BrokenMicrophone)
    return previous


def _restore_speech_recognition(previous):
    if previous is None:
        sys.modules.pop("speech_recognition", None)
    else:
        sys.modules["speech_recognition"] = previous


def test_mic_available_accepts_working_fallback():
    previous_sr = _with_broken_pyaudio()
    original_probe = audio_level.probe_microphone
    audio_level.probe_microphone = lambda: (True, "SoundDeviceStream")
    try:
        ok, detail = voice.mic_available()
    finally:
        audio_level.probe_microphone = original_probe
        _restore_speech_recognition(previous_sr)
    assert ok and detail == "SoundDeviceStream"


def test_hold_recorder_records_with_fallback_backend():
    previous_sr = _with_broken_pyaudio()
    original_open = audio_level.open_input_stream

    class Stream:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def read(self, _n):
            self.calls += 1
            if self.calls == 1:
                return b"\x01\x00" * 1024
            time.sleep(0.01)
            return b""

        def close(self):
            self.closed = True

    stream = Stream()
    audio_level.open_input_stream = lambda: stream
    recorder = voice.HoldRecorder(sample_rate=16000)
    try:
        assert recorder.start(), recorder.error
        time.sleep(0.04)
        path = recorder.stop()
    finally:
        audio_level.open_input_stream = original_open
        _restore_speech_recognition(previous_sr)
    assert path and os.path.exists(path)
    assert os.path.getsize(path) > 44
    assert stream.closed
    os.unlink(path)


def test_record_audio_uses_portable_backend():
    class Stream:
        def __init__(self):
            self.reads = 0
            self.closed = False

        def read(self, _n):
            self.reads += 1
            return b"\x00\x01" * 512   # 1024 bytes of PCM per read

        def close(self):
            self.closed = True

    stream = Stream()
    original_open = audio_level.open_input_stream
    audio_level.open_input_stream = lambda: stream
    out = os.path.join(tempfile.gettempdir(), f"ember_rec_test_{int(time.time() * 1000)}.wav")
    try:
        res = extra_tools.record_audio(seconds=0.5, path=out)
    finally:
        audio_level.open_input_stream = original_open
    assert res.get("ok"), res
    assert os.path.exists(out) and os.path.getsize(out) > 44
    assert stream.reads > 0 and stream.closed
    os.unlink(out)


def test_record_audio_reports_missing_backend_honestly():
    original_open = audio_level.open_input_stream

    def _no_backend():
        raise RuntimeError("no microphone backend is installed")

    audio_level.open_input_stream = _no_backend
    try:
        res = extra_tools.record_audio(seconds=0.5)
    finally:
        audio_level.open_input_stream = original_open
    assert res.get("ok") is False
    assert "backend" in res.get("error", "").lower()
    assert "sounddevice" in (res.get("fix", "")).lower()


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} microphone fallback tests passed")
