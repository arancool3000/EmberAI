"""Realtime microphone level metering — so the Siri glow + floating orb actually
*move with your voice* instead of just breathing on a timer.

The hard constraint on macOS is that two simultaneous CoreAudio input streams fail
(this is the same reason the wake word and a voice turn share `voice.MIC_LOCK`).
So we can't run a separate "meter" stream alongside speech recognition. Instead this
module owns ONE mic stream for a voice turn: it reads the audio frame-by-frame,
publishes a smoothed 0..1 level as it goes (which the glow/orb poll via `get_level`),
does simple energy VAD to find the end of the utterance, and then hands the finished
audio to speech_recognition — one stream doing both jobs.

Everything heavy (pyaudio / speech_recognition) is imported lazily, and the capture
path is behind two injection points (`_STREAM_FACTORY`, `_RECOGNIZER`) so the level
math and VAD are unit-testable with scripted frames and never touch real audio.
"""
from __future__ import annotations

import math
import sys
import threading
import time
from array import array
from typing import Callable, Optional

# Audio format for capture (mono 16-bit). 16 kHz is plenty for speech + cheap to meter.
RATE = 16000
WIDTH = 2            # bytes/sample (16-bit)
CHUNK = 1024         # samples per read (~64 ms at 16 kHz)

# Perceptual normalisation reference: an RMS at/above this reads as "full" (level 1.0).
# Normal speech sits a few hundred → few thousand RMS on 16-bit audio.
_RMS_FULL = 4000.0

# Injection points for tests (None -> real implementation).
#   _STREAM_FACTORY() -> object with .read(n_samples)->bytes and .close()
#   _RECOGNIZER(raw_bytes, rate, width) -> transcript str
_STREAM_FACTORY: Optional[Callable[[], object]] = None
_RECOGNIZER: Optional[Callable[[bytes, int, int], str]] = None

# Published level state (polled by the UI/animations on the main thread).
_LOCK = threading.Lock()
_level = 0.0          # smoothed 0..1
_active = False       # True only while a metered capture is running


# ---------------------------------------------------------------------------
# Pure, testable signal helpers
# ---------------------------------------------------------------------------

def rms_of_frame(frame: bytes) -> float:
    """Root-mean-square amplitude of a 16-bit little-endian mono PCM frame (0..32768)."""
    if not frame:
        return 0.0
    samples = array("h")
    # Drop a trailing odd byte rather than crash on a short read.
    samples.frombytes(frame[: len(frame) - (len(frame) % 2)])
    if not samples:
        return 0.0
    total = 0.0
    for s in samples:
        total += float(s) * float(s)
    return math.sqrt(total / len(samples))


def normalize_level(rms: float, full: float = _RMS_FULL) -> float:
    """Map an RMS amplitude to a perceptual 0..1 level (sqrt curve so quiet speech is
    still visibly lively, and it saturates gracefully on loud input)."""
    if rms <= 0.0 or full <= 0.0:
        return 0.0
    return max(0.0, min(1.0, math.sqrt(rms / full)))


# ---------------------------------------------------------------------------
# Published level (what the glow/orb read)
# ---------------------------------------------------------------------------

def get_level() -> Optional[float]:
    """Most recent smoothed mic level (0..1) while a metered turn is active, else None.

    Returning None when idle lets the animations fall back to their own behaviour
    (synthetic 'speaking' envelope / gentle breathing) instead of being pinned at 0."""
    with _LOCK:
        return _level if _active else None


def is_active() -> bool:
    with _LOCK:
        return _active


def _publish(level: float, active: bool = True) -> None:
    global _level, _active
    with _LOCK:
        _level = max(0.0, min(1.0, level))
        _active = active


def _deactivate() -> None:
    global _active
    with _LOCK:
        _active = False


# ---------------------------------------------------------------------------
# Real mic stream (used unless _STREAM_FACTORY is injected)
# ---------------------------------------------------------------------------

class _PyAudioStream:
    """Thin wrapper over a pyaudio input stream exposing read()/close()."""

    def __init__(self):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=1, rate=RATE,
            input=True, frames_per_buffer=CHUNK)

    def read(self, n: int) -> bytes:
        return self._stream.read(n, exception_on_overflow=False)

    def close(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass


class _SoundDeviceStream:
    """Portable fallback whose wheels work without making users install PyAudio manually."""

    def __init__(self):
        import sounddevice as sd
        self._stream = sd.RawInputStream(
            samplerate=RATE, blocksize=CHUNK, channels=1, dtype="int16")
        self._stream.start()

    def read(self, n: int) -> bytes:
        data, _overflowed = self._stream.read(n)
        return bytes(data)

    def close(self) -> None:
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


def open_input_stream():
    """Open the best installed microphone backend, falling back when one is broken/missing."""
    errors = []
    for backend in (_PyAudioStream, _SoundDeviceStream):
        try:
            return backend()
        except Exception as exc:
            errors.append(f"{backend.__name__.removeprefix('_').removesuffix('Stream')}: {exc}")
    raise RuntimeError("; ".join(errors) or "no microphone backend is installed")


def probe_microphone() -> tuple[bool, str]:
    """Actually open and close an input stream so settings can report a truthful status."""
    stream = None
    try:
        stream = open_input_stream()
        return True, type(stream).__name__.strip("_")
    except Exception as exc:
        return False, str(exc)
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def available() -> bool:
    """True if the metered-capture path can run (an input backend + speech recognition),
    or if test hooks are installed."""
    if _STREAM_FACTORY is not None and _RECOGNIZER is not None:
        return True
    try:
        import speech_recognition  # noqa: F401
        try:
            import pyaudio  # noqa: F401
        except Exception:
            import sounddevice  # noqa: F401
        return True
    except Exception:
        return False


def _default_recognizer(raw: bytes, rate: int, width: int) -> str:
    import speech_recognition as sr
    rec = sr.Recognizer()
    audio = sr.AudioData(raw, rate, width)
    return rec.recognize_google(audio) or ""


# ---------------------------------------------------------------------------
# Metered capture: read frames, publish levels, VAD, then recognise
# ---------------------------------------------------------------------------

def _capture_and_recognize(on_transcript: Callable[[str, str], None],
                           phrase_timeout: float, listen_timeout: float,
                           on_level: Optional[Callable[[float], None]]) -> None:
    """Own the mic for one utterance. Publishes levels live, ends on a silence tail or
    the phrase cap, then recognises. Always calls on_transcript(text, err) exactly once."""
    factory = _STREAM_FACTORY or open_input_stream
    recognizer = _RECOGNIZER or _default_recognizer
    try:
        from voice import MIC_LOCK
    except Exception:
        MIC_LOCK = threading.RLock()

    frame_secs = CHUNK / float(RATE)
    silence_tail = 0.9                      # end the phrase after this much sub-threshold audio
    smooth = 0.0
    stream = None
    err = ""
    text = ""
    collected: list[bytes] = []
    try:
        with MIC_LOCK:
            try:
                stream = factory()
            except Exception as e:
                msg = str(e).lower()
                if "pyaudio" in msg:
                    on_transcript("", "PyAudio is missing. Run: pip install pyaudio")
                else:
                    on_transcript("", f"mic error: {e}")
                return
            _publish(0.0, active=True)

            # Calibrate a noise floor over the first few frames.
            floor_frames = max(1, int(0.3 / frame_secs))
            floor_vals: list[float] = []
            peak = 0.0                       # loudest frame seen — distinguishes a dead/muted mic
            for _ in range(floor_frames):    # from merely-quiet speech when nothing gets captured
                try:
                    fr = stream.read(CHUNK)
                except Exception:
                    fr = b""
                r = rms_of_frame(fr)
                floor_vals.append(r)
                peak = max(peak, r)
            # Noise floor = MEDIAN of the calibration frames, NOT the max: one loud blip (or the
            # user already starting to talk) shouldn't inflate the floor and then swallow the rest
            # of the phrase.
            floor_vals.sort()
            floor = floor_vals[len(floor_vals) // 2] if floor_vals else 0.0
            # Speech must clear the floor by a margin AND an absolute minimum. 180 (was 350): 350
            # was high enough that quiet / low-gain mics never cleared it, so every phrase came
            # back "no speech".
            threshold = max(floor * 1.8, 180.0)

            started = False
            speech_secs = 0.0
            silence_secs = 0.0
            waited = 0.0
            max_wait = max(2.0, float(listen_timeout))
            # phrase_timeout None/0 -> "Auto": no hard cap; the silence tail ends the turn when
            # you stop talking. Keep a generous safety ceiling so noise can't record forever.
            max_phrase = max(1.0, float(phrase_timeout)) if phrase_timeout else 60.0

            while True:
                try:
                    fr = stream.read(CHUNK)
                except StopIteration:
                    break
                except Exception:
                    break
                if not fr:
                    # Injected stream exhausted -> stop.
                    break
                rms = rms_of_frame(fr)
                peak = max(peak, rms)
                lvl = normalize_level(rms)
                # Snappy attack, smoother release -> reads as "alive" but not jittery.
                smooth = lvl if lvl > smooth else smooth * 0.6 + lvl * 0.4
                _publish(smooth, active=True)
                if on_level:
                    try:
                        on_level(smooth)
                    except Exception:
                        pass

                loud = rms >= threshold
                if not started:
                    waited += frame_secs
                    if loud:
                        started = True
                        collected.append(fr)
                        speech_secs += frame_secs
                    elif waited >= max_wait:
                        break   # gave up waiting for speech to begin
                else:
                    collected.append(fr)
                    speech_secs += frame_secs
                    silence_secs = 0.0 if loud else silence_secs + frame_secs
                    if silence_secs >= silence_tail:
                        break   # end of utterance
                    if speech_secs >= max_phrase:
                        break   # phrase length cap
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        _publish(0.0, active=False)
        _deactivate()

    raw = b"".join(collected)
    if not raw:
        if not err:
            if peak < 25.0:
                # The mic delivered (near-)silence the whole time — almost always a permission or
                # wrong-input-device problem, not the user being quiet. Say so, actionably.
                if sys.platform == "darwin":
                    err = ("no speech detected — Ember's microphone received almost no audio. "
                           "Grant mic access in System Settings ▸ Privacy & Security ▸ "
                           "Microphone, and check the correct input device is selected.")
                else:
                    err = ("no speech detected — Ember's microphone received almost no audio. "
                           "Check that microphone permission is granted and the right input "
                           "device is selected.")
            else:
                err = "no speech detected — the audio was too quiet; try speaking a bit louder."
        on_transcript("", err)
        return
    try:
        text = recognizer(raw, RATE, WIDTH) or ""
    except Exception as e:
        msg = str(e).lower()
        if "unknownvalue" in type(e).__name__.lower() or "unknown value" in msg:
            on_transcript("", "couldn't understand audio")
            return
        on_transcript("", f"transcribe failed: {e}")
        return
    on_transcript(text, "")


def listen_metered(on_transcript: Callable[[str, str], None],
                   phrase_timeout: "float | None" = 8.0, listen_timeout: float = 10.0,
                   on_level: Optional[Callable[[float], None]] = None) -> bool:
    """Drop-in, level-publishing replacement for voice.listen_once.

    Returns True if the metered capture started (on a background thread; on_transcript is
    invoked there, exactly once), or False if the metered path is unavailable — in which
    case the caller should fall back to voice.listen_once. While it runs, get_level()
    returns the live 0..1 mic level so the glow/orb can react to the user's voice."""
    if not available():
        return False
    t = threading.Thread(
        target=_capture_and_recognize,
        args=(on_transcript, phrase_timeout, listen_timeout, on_level),
        name="ember-audio-meter", daemon=True)
    t.start()
    return True
