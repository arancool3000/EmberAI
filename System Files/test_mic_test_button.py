"""Tests for the 'Test microphone' button in Settings ▸ Voice.

The reported bug: the button reported

    ⚠️ Mic opened but capture failed: Could not find PyAudio; check installation

on a machine where the microphone worked fine. Two different audio paths were in play —
`voice.mic_available()` opens the device through Ember's own `audio_backend`, whose default is
sounddevice (its wheels bundle PortAudio), so the "opened" half succeeded. The capture half then
went through `speech_recognition.Microphone`, which hard-requires PyAudio. Ember deliberately
does not ship PyAudio: it has no macOS wheel and must be compiled against Homebrew PortAudio.

So the test told users to install something they should not need, for a microphone that was
already working.
"""
import os
import sys
import tempfile
import types
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("EMBER_SAFE_MODE", "1")
os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_mic_test_"))

import pytest

import audio_backend
import ui

SRC = Path(__file__).resolve().parent / "ui.py"


@pytest.fixture
def capture(monkeypatch):
    """The _quick_capture method bound to a bare object — it needs no widget state."""
    host = type("Host", (), {"_quick_capture": ui.SettingsDialog._quick_capture})()
    return host


def _silence(path, seconds=1, rate=16000):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * rate * seconds)
    return path


# --- the bug --------------------------------------------------------------------
def test_capture_does_not_go_through_speech_recognition_microphone():
    """sr.Microphone is the thing that requires PyAudio. It must not be on this path."""
    body = SRC.read_text(encoding="utf-8").split("def _quick_capture", 1)[1] \
              .split("\n    def ", 1)[0]
    # Match a CALL, not the prose: the docstring explains why sr.Microphone was removed, and
    # a bare substring check would flag its own explanation.
    assert "sr.Microphone(" not in body
    assert "audio_backend.record_wav" in body


def test_a_working_sounddevice_mic_reports_success(capture, monkeypatch):
    """The exact scenario from the bug report: no PyAudio anywhere, mic works."""
    monkeypatch.setitem(sys.modules, "pyaudio", None)   # importing it raises
    monkeypatch.setattr(audio_backend, "record_wav",
                        lambda secs, path, **kw: _silence(path))
    text, err = capture._quick_capture()
    assert err == "", f"a working mic must not report an error, got {err!r}"


def test_it_no_longer_mentions_pyaudio_when_the_mic_works(capture, monkeypatch):
    monkeypatch.setattr(audio_backend, "record_wav",
                        lambda secs, path, **kw: _silence(path))
    _text, err = capture._quick_capture()
    assert "PyAudio" not in err


def test_missing_speech_recognition_is_not_a_mic_failure(capture, monkeypatch):
    """Audio was captured; we just cannot turn it into words. That is a working microphone."""
    monkeypatch.setattr(audio_backend, "record_wav",
                        lambda secs, path, **kw: _silence(path))
    monkeypatch.setitem(sys.modules, "speech_recognition", None)
    text, err = capture._quick_capture()
    assert err == "" and text == ""


def test_a_real_recording_failure_is_still_reported(capture, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("No working microphone backend. Install sounddevice")
    monkeypatch.setattr(audio_backend, "record_wav", _boom)
    _text, err = capture._quick_capture()
    assert "sounddevice" in err


def test_transcription_is_returned_when_it_works(capture, monkeypatch):
    monkeypatch.setattr(audio_backend, "record_wav",
                        lambda secs, path, **kw: _silence(path))
    fake = types.ModuleType("speech_recognition")

    class _Rec:
        def record(self, src):
            return "audio"

        def recognize_google(self, audio):
            return "hello ember"

    class _File:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake.Recognizer = _Rec
    fake.AudioFile = _File
    monkeypatch.setitem(sys.modules, "speech_recognition", fake)
    text, err = capture._quick_capture()
    assert text == "hello ember" and err == ""


def test_a_failed_transcription_still_counts_as_a_working_mic(capture, monkeypatch):
    monkeypatch.setattr(audio_backend, "record_wav",
                        lambda secs, path, **kw: _silence(path))
    fake = types.ModuleType("speech_recognition")

    class _Rec:
        def record(self, src):
            return "audio"

        def recognize_google(self, audio):
            raise OSError("offline")

    class _File:
        def __init__(self, path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake.Recognizer = _Rec
    fake.AudioFile = _File
    monkeypatch.setitem(sys.modules, "speech_recognition", fake)
    text, err = capture._quick_capture()
    assert text == "" and err == ""


def test_the_temp_recording_is_cleaned_up(capture, monkeypatch):
    made = {}

    def _rec(secs, path, **kw):
        made["path"] = path
        return _silence(path)
    monkeypatch.setattr(audio_backend, "record_wav", _rec)
    capture._quick_capture()
    assert made["path"] and not os.path.exists(made["path"])


# --- the backend it now relies on -------------------------------------------------
def test_sounddevice_is_the_default_backend():
    """The whole fix rests on this: sounddevice ships wheels that bundle PortAudio, so it
    works without Homebrew. If PyAudio were ever preferred again, the bug returns."""
    assert audio_backend.preferred_order()[0] == "sounddevice"


def test_install_hint_leads_with_sounddevice():
    hint = audio_backend.install_hint()
    assert "sounddevice" in hint
    assert hint.index("sounddevice") < (hint.index("pyaudio") if "pyaudio" in hint else 10**6)


# --- recording inside the packaged app ---------------------------------------------
def test_recording_does_not_need_numpy(monkeypatch):
    """Ember.app excludes NumPy to keep the bundle small. sd.rec() returns a NumPy array, so
    using it made recording fail inside the bundle while the mic itself opened fine — the user
    saw "no working microphone backend" for a microphone that plainly worked."""
    src = Path(__file__).resolve().parent / "audio_backend.py"
    body = src.read_text(encoding="utf-8").split("def _record_sounddevice", 1)[1] \
              .split("\ndef ", 1)[0]
    # Drop the docstring before matching: it explains why sd.rec() was removed, and a bare
    # substring check would flag that explanation as the offence.
    code = body.split('"""', 2)[-1]
    assert "sd.rec(" not in code, "sd.rec needs NumPy, which the bundle excludes"
    assert "RawInputStream" in code


def test_numpy_is_still_excluded_from_the_bundle():
    """Guards the premise: if NumPy were bundled, the constraint above would be theatre — and
    if someone re-adds sd.rec() later, the pairing of these two tests explains why not."""
    spec = (Path(__file__).resolve().parent / "Ember.spec").read_text(encoding="utf-8")
    excludes = spec.split("excludes=[", 1)[1].split("]", 1)[0]
    assert '"numpy"' in excludes


def test_record_wav_uses_a_raw_stream(monkeypatch, tmp_path):
    """Drive the real recorder against a stub sounddevice with no NumPy in sight."""
    import sys
    import types
    import wave

    class _Raw:
        def __init__(self, **kw):
            self.started = False

        def start(self):
            self.started = True

        def read(self, n):
            return (b"\x01\x02" * n, False)

        def stop(self):
            pass

        def close(self):
            pass

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.RawInputStream = _Raw
    fake_sd.rec = lambda *a, **k: pytest.fail("sd.rec needs NumPy; must not be used")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    out = tmp_path / "rec.wav"
    audio_backend.record_wav(1, str(out), rate=8000, channels=1)
    with wave.open(str(out), "rb") as wf:
        assert wf.getframerate() == 8000
        assert wf.getnframes() == 8000          # a full second, not a truncated buffer


def test_the_frozen_build_does_not_tell_you_to_pip_install(monkeypatch):
    """sys.executable inside the app is the Ember binary. "Ember -m pip install sounddevice"
    is advice that can never work."""
    monkeypatch.setattr(audio_backend.sys, "frozen", True, raising=False)
    hint = audio_backend.install_hint()
    assert "pip install" not in hint
    assert "packaging fault" in hint


def test_the_source_build_still_gives_a_pip_command(monkeypatch):
    monkeypatch.delattr(audio_backend.sys, "frozen", raising=False)
    assert "pip install" in audio_backend.install_hint()
