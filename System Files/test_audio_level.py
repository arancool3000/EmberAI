"""Hermetic tests for audio_level — the live mic-level meter that drives the
audio-reactive Siri glow/orb. No real audio: a fake stream feeds scripted PCM frames
and a fake recognizer returns a transcript, so the level math + VAD are tested directly.

Run: python test_audio_level.py
"""
import threading
from array import array

import audio_level


def _frame(value: int, n: int = audio_level.CHUNK) -> bytes:
    """A PCM frame of `n` constant 16-bit samples (RMS == |value|)."""
    return array("h", [value] * n).tobytes()


class _FakeStream:
    """Returns scripted frames in order; b"" (then forever) once exhausted."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0
        self.closed = False

    def read(self, n):
        if self._i >= len(self._frames):
            return b""
        fr = self._frames[self._i]
        self._i += 1
        return fr

    def close(self):
        self.closed = True


def _run(frames, recognizer=None, phrase_timeout=5.0, listen_timeout=1.5, timeout=4.0):
    """Drive one metered capture over scripted frames; return (text, err, levels, stream)."""
    stream = _FakeStream(frames)
    result = {}
    levels = []
    done = threading.Event()

    def _on_transcript(text, err):
        result["text"], result["err"] = text, err
        done.set()

    audio_level._STREAM_FACTORY = lambda: stream
    audio_level._RECOGNIZER = recognizer or (lambda raw, rate, width: "hello ember")
    try:
        started = audio_level.listen_metered(
            _on_transcript, phrase_timeout=phrase_timeout, listen_timeout=listen_timeout,
            on_level=levels.append)
        assert started is True, "listen_metered should start when hooks are installed"
        assert done.wait(timeout), "capture did not finish in time"
    finally:
        audio_level._STREAM_FACTORY = None
        audio_level._RECOGNIZER = None
    return result.get("text"), result.get("err"), levels, stream


def test_rms_of_silence_is_zero():
    assert audio_level.rms_of_frame(_frame(0)) == 0.0
    assert audio_level.rms_of_frame(b"") == 0.0


def test_rms_of_constant_signal():
    # RMS of a constant 16-bit value equals that value.
    assert abs(audio_level.rms_of_frame(_frame(8000)) - 8000.0) < 1.0


def test_rms_tolerates_odd_length():
    # A truncated trailing byte must not crash.
    assert audio_level.rms_of_frame(_frame(1000) + b"\x01") > 0.0


def test_normalize_level_bounds():
    assert audio_level.normalize_level(0.0) == 0.0
    assert audio_level.normalize_level(-5.0) == 0.0
    assert audio_level.normalize_level(audio_level._RMS_FULL) == 1.0
    assert audio_level.normalize_level(audio_level._RMS_FULL * 100) == 1.0  # clamps
    mid = audio_level.normalize_level(audio_level._RMS_FULL / 4)
    assert 0.0 < mid < 1.0


def test_available_with_hooks():
    audio_level._STREAM_FACTORY = lambda: _FakeStream([])
    audio_level._RECOGNIZER = lambda raw, rate, width: ""
    try:
        assert audio_level.available() is True
    finally:
        audio_level._STREAM_FACTORY = None
        audio_level._RECOGNIZER = None


def test_input_backend_falls_back_when_pyaudio_is_missing():
    original_primary = audio_level._PyAudioStream
    original_fallback = audio_level._SoundDeviceStream
    closed = []

    class BrokenPrimary:
        def __init__(self):
            raise ImportError("pyaudio missing")

    class WorkingFallback:
        def read(self, _n):
            return _frame(100)

        def close(self):
            closed.append(True)

    audio_level._PyAudioStream = BrokenPrimary
    audio_level._SoundDeviceStream = WorkingFallback
    try:
        stream = audio_level.open_input_stream()
        assert isinstance(stream, WorkingFallback)
        stream.close()
    finally:
        audio_level._PyAudioStream = original_primary
        audio_level._SoundDeviceStream = original_fallback
    assert closed == [True]


def test_probe_microphone_closes_fallback_stream():
    original = audio_level.open_input_stream
    closed = []

    class Stream:
        def close(self):
            closed.append(True)

    audio_level.open_input_stream = lambda: Stream()
    try:
        ok, backend = audio_level.probe_microphone()
    finally:
        audio_level.open_input_stream = original
    assert ok and backend == "Stream"
    assert closed == [True]


def test_capture_ends_on_silence_tail_and_transcribes():
    # 4 quiet calibration frames, a burst of speech, then a long quiet tail.
    frames = [_frame(0)] * 4 + [_frame(9000)] * 10 + [_frame(0)] * 20
    text, err, levels, stream = _run(frames)
    assert text == "hello ember", (text, err)
    assert err == ""
    assert max(levels) > 0.3, "should have published a real level during speech"
    assert stream.closed is True, "stream must be closed after capture"


def test_capture_ends_on_stream_exhaustion():
    frames = [_frame(0)] * 4 + [_frame(9000)] * 5  # stream runs dry -> capture ends
    text, err, _levels, _s = _run(frames)
    assert text == "hello ember", (text, err)
    assert err == ""


def test_no_speech_returns_error_not_text():
    frames = [_frame(0)] * 4  # only calibration, then dry -> nothing captured
    text, err, _levels, _s = _run(frames)
    assert text == ""
    assert "no speech" in err.lower()


def test_silent_mic_points_at_permission():
    # A dead / permission-denied mic delivers ~silence the whole time. That must read as a
    # microphone problem, not a bland "no speech" — the #1 cause of "voice doesn't work".
    frames = [_frame(0)] * 8
    text, err, _levels, _s = _run(frames)
    assert text == "" and "no speech" in err.lower()
    assert "microphone" in err.lower()          # actionable: it's a mic/permission issue


def test_quiet_speech_below_threshold_hints_louder():
    # Real audio that never clears the VAD threshold -> distinct "too quiet" hint (not the
    # silent-mic message), so the user knows to speak up / raise input gain.
    frames = [_frame(120)] * 12                  # 120 RMS: above silence, below the 180 floor
    text, err, _levels, _s = _run(frames)
    assert text == "" and "no speech" in err.lower()
    assert "quiet" in err.lower() or "louder" in err.lower()
    assert "microphone" not in err.lower()       # not misdiagnosed as a dead mic


def test_quiet_mic_speech_now_clears_lower_threshold():
    # The threshold floor dropped 350 -> 180 so quieter mics register. ~250 RMS speech used to
    # be swallowed ("no speech"); now it's captured and transcribed.
    frames = [_frame(0)] * 4 + [_frame(250)] * 10 + [_frame(0)] * 20
    text, err, _levels, _s = _run(frames)
    assert text == "hello ember" and err == ""


def test_loud_blip_during_calibration_does_not_deafen():
    # Median (not max) calibration: one loud blip while calibrating must NOT inflate the floor and
    # then swallow the actual speech that follows.
    frames = [_frame(9000)] + [_frame(0)] * 3 + [_frame(3000)] * 10 + [_frame(0)] * 20
    text, err, _levels, _s = _run(frames)
    assert text == "hello ember", (text, err)   # 3000-RMS speech still clears the floor


def test_level_inactive_after_capture():
    frames = [_frame(0)] * 4 + [_frame(9000)] * 6 + [_frame(0)] * 20
    _run(frames)
    # Once the turn is over, get_level() reports None so the animations fall back.
    assert audio_level.get_level() is None
    assert audio_level.is_active() is False


def test_recognizer_unknown_value_is_friendly():
    class UnknownValueError(Exception):  # mirrors speech_recognition.UnknownValueError
        pass

    def _rec(raw, rate, width):
        raise UnknownValueError()

    frames = [_frame(0)] * 4 + [_frame(9000)] * 8 + [_frame(0)] * 20
    text, err, _levels, _s = _run(frames, recognizer=_rec)
    assert text == ""
    assert "understand" in err.lower()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} audio_level tests passed")
