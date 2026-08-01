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
