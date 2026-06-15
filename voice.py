"""Speech recognition (microphone in) and text-to-speech (assistant out)."""
from __future__ import annotations

import threading
from typing import Callable


_tts_engine = None
_tts_lock = threading.Lock()
_tts_thread: threading.Thread | None = None


def _ensure_tts():
    global _tts_engine
    if _tts_engine is None:
        import pyttsx3
        _tts_engine = pyttsx3.init()
        try:
            _tts_engine.setProperty("rate", 185)
        except Exception:
            pass
    return _tts_engine


def speak(text: str):
    """Speak `text` aloud in a background thread. Safe to call repeatedly; queues."""
    if not text or not text.strip():
        return

    def _run():
        try:
            with _tts_lock:
                eng = _ensure_tts()
                eng.say(text)
                eng.runAndWait()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def stop_speaking():
    global _tts_engine
    if _tts_engine:
        try:
            _tts_engine.stop()
        except Exception:
            pass


def listen_once(on_transcript: Callable[[str, str | None], None],
                phrase_timeout: float = 6.0, listen_timeout: float = 8.0):
    """Record one utterance from the default mic, transcribe via Google Web Speech (free),
    then call on_transcript(text, error).

    Runs the mic + network call in a background thread; returns immediately.
    on_transcript is invoked on that thread; the caller should marshal to UI as needed.
    """
    def _run():
        try:
            import speech_recognition as sr
        except ImportError as e:
            on_transcript("", f"speech_recognition not installed: {e}")
            return
        rec = sr.Recognizer()
        rec.dynamic_energy_threshold = True
        try:
            mic = sr.Microphone()
        except Exception as e:
            hint = ("voice input needs pyaudio: 'uv pip install pyaudio' "
                    "(or pip install pyaudio)") if "pyaudio" in str(e).lower() else str(e)
            on_transcript("", f"no microphone: {hint}")
            return
        try:
            with mic as source:
                rec.adjust_for_ambient_noise(source, duration=0.3)
                audio = rec.listen(source, timeout=listen_timeout,
                                   phrase_time_limit=phrase_timeout)
        except sr.WaitTimeoutError:
            on_transcript("", "no speech detected")
            return
        except Exception as e:
            on_transcript("", f"mic error: {e}")
            return
        try:
            text = rec.recognize_google(audio)
            on_transcript(text or "", None)
        except sr.UnknownValueError:
            on_transcript("", "couldn't understand audio")
        except sr.RequestError as e:
            on_transcript("", f"speech API error: {e}")
        except Exception as e:
            on_transcript("", f"transcribe failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
