"""Hermetic microphone backend tests: PyAudio failure must fall back to sounddevice."""
import os
import sys
import tempfile
import time
import types

import audio_level
import voice


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


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} microphone fallback tests passed")
