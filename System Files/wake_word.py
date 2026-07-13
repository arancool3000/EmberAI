"""Always-on "Hey Ember" wake-word listener.

A background daemon keeps the microphone open and, whenever it hears the wake
phrase ("hey ember" and close variants), fires a callback — the UI uses that to
start a voice turn and light up the Siri-style glow. It runs forever: it restarts
itself on any mic/transcription hiccup and only pauses while a command is actually
being captured (so it isn't fighting the command recogniser for the mic).

Design (mirrors the other Ember daemons):
  * one daemon thread + stop event, bounded detection log behind a lock;
  * detection is a pure, unit-testable function (detect_wake) using rapidfuzz so
    common mishearings ("hey amber", "a ember", "okay ember") still trigger;
  * the actual mic capture is behind a single `_CAPTURE` injection point, so tests
    feed scripted transcripts and never touch audio;
  * offline PocketSphinx is preferred for the always-listening loop (cheap, private)
    with Google Web Speech as a fallback — both already used elsewhere.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime

# ---------------------------------------------------------------------------
# Wake phrases + detection
# ---------------------------------------------------------------------------

# The canonical phrase plus the ways speech-to-text commonly mangles "ember". Every entry is a
# GREETING + ember-ish bigram on purpose: a bare "ember" (or it embedded in "remember"/"december")
# must NOT wake. (The old "a ember" entry was too loose — fuzz.partial_ratio matched the "ember a…"
# in "ember alone word here" at 83 and woke on unrelated speech.)
_WAKE_PHRASES = (
    "hey ember", "hi ember", "hello ember", "okay ember", "ok ember", "yo ember",
    "hey amber", "hey ambre", "hey umber",
)
# A precise regex catch for "<greeting> <ember-ish>" so a clean transcript always wins.
_WAKE_RE = re.compile(
    r"\b(?:hey|hi|hello|ok|okay|yo|hay)\s+(?:ember|embers|amber|ambre|umber|embder|ember's|emba)\b",
    re.IGNORECASE,
)

_DEFAULT_THRESHOLD = 82


def _normalize(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_wake(text: str, threshold: int = _DEFAULT_THRESHOLD) -> bool:
    """True if `text` contains the wake phrase (fuzzily). Pure + offline."""
    norm = _normalize(text)
    if not norm:
        return False
    if _WAKE_RE.search(norm):
        return True
    try:
        from rapidfuzz import fuzz
        for phrase in _WAKE_PHRASES:
            if fuzz.partial_ratio(phrase, norm) >= threshold:
                return True
    except Exception:
        # No rapidfuzz -> fall back to a plain containment check on the variants.
        for phrase in _WAKE_PHRASES:
            if phrase in norm:
                return True
    return False


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_PHRASE_LIMIT = 2.5       # max seconds of audio per wake-listen chunk
_LISTEN_TIMEOUT = 3.0     # short chunks so the loop yields the mic quickly to a voice turn
_COOLDOWN = 1.2           # pause after a hit so one "hey ember" fires once
_EVENTS_MAXLEN = 60

# Injection point for tests: callable() -> transcript str ("" / None = heard nothing).
# Default None -> real microphone capture.
_CAPTURE = None

_LOCK = threading.Lock()
_thread: "threading.Thread | None" = None
_stop_event: "threading.Event | None" = None
_running = False
_paused = False
_detections = 0
_events: "deque[dict]" = deque(maxlen=_EVENTS_MAXLEN)
_on_wake = None
_last_heard = ""          # most recent non-empty transcript (diagnostic: is the mic hearing anything?)
_heard_count = 0          # how many non-empty transcripts we've captured
_last_error = ""          # why the mic couldn't start (shown to the user), "" when healthy


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Real microphone capture (used unless _CAPTURE is injected)
# ---------------------------------------------------------------------------

def _recognize(rec, audio) -> str:
    # Prefer offline PocketSphinx for the always-on loop; fall back to Google.
    try:
        return rec.recognize_sphinx(audio) or ""
    except Exception:
        pass
    try:
        return rec.recognize_google(audio) or ""
    except Exception:
        return ""


class _MicCapture:
    """A capture() that keeps ONE mic stream open across listens, so macOS's orange
    "mic in use" indicator stays steady instead of FLASHING on every ~3s chunk (which
    happens if you open/close the stream each time). The stream is released on pause()
    (via release()) so an active voice/dictation turn can take the mic, and reopened on
    resume. Shares voice.MIC_LOCK so it never fights a voice turn for the device."""

    def __init__(self, sr):
        self._sr = sr
        self._rec = sr.Recognizer()
        self._rec.dynamic_energy_threshold = True
        self._mic = sr.Microphone()
        self._source = None
        try:
            from voice import MIC_LOCK
        except Exception:
            MIC_LOCK = threading.RLock()
        self._lock = MIC_LOCK

    def _ensure_open(self):
        if self._source is None:
            self._source = self._mic.__enter__()
            self._rec.adjust_for_ambient_noise(self._source, duration=0.3)

    def release(self):
        """Close the mic stream (called when paused) so it stops showing as in-use and
        another consumer can open it."""
        if self._source is not None:
            try:
                self._mic.__exit__(None, None, None)
            except Exception:
                pass
            self._source = None

    def __call__(self) -> str:
        try:
            with self._lock:
                if _paused:
                    self.release()
                    return ""
                self._ensure_open()
                try:
                    audio = self._rec.listen(self._source, timeout=_LISTEN_TIMEOUT,
                                             phrase_time_limit=_PHRASE_LIMIT)
                except self._sr.WaitTimeoutError:
                    return ""
                except Exception:
                    self.release()
                    time.sleep(0.4)
                    return ""
            return _recognize(self._rec, audio)
        except Exception:
            return ""


class _SoundDeviceCapture:
    """Wake-word capture WITHOUT PyAudio. Reads raw frames from the portable input stream
    (audio_level.open_input_stream → sounddevice when PyAudio is absent), runs light energy VAD to
    grab one short utterance, then hands the PCM to speech_recognition via AudioData. This lets
    'Hey Ember' work on a normal Ember install (sounddevice only) with no manual PyAudio build.

    Mirrors _MicCapture's contract: keeps ONE stream open across listens (steady mic indicator),
    releases it on pause() via release(), and shares voice.MIC_LOCK so it never fights a voice
    turn for the device."""

    def __init__(self, sr):
        import audio_level
        self._sr = sr
        self._al = audio_level
        self._rec = sr.Recognizer()
        self._stream = None
        self._threshold = None
        try:
            from voice import MIC_LOCK
        except Exception:
            MIC_LOCK = threading.RLock()
        self._lock = MIC_LOCK
        # Open once, now, so a missing-backend / denied-permission failure surfaces synchronously
        # in _real_capture_factory instead of dying quietly on the daemon thread.
        self._ensure_open()

    def _ensure_open(self):
        if self._stream is None:
            self._stream = self._al.open_input_stream()
            self._threshold = None   # recalibrate the noise floor on the fresh stream

    def release(self):
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _calibrate(self):
        """Estimate a noise floor from a few frames so a loud room isn't read as constant speech.
        Median (not max) so one blip doesn't inflate it; a floor keeps quiet mics usable."""
        vals = []
        for _ in range(4):
            try:
                fr = self._stream.read(self._al.CHUNK)
            except Exception:
                break
            if fr:
                vals.append(self._al.rms_of_frame(fr))
        vals.sort()
        floor = vals[len(vals) // 2] if vals else 0.0
        self._threshold = max(floor * 1.8, 200.0)

    def _read_phrase(self) -> bytes:
        """Read one short utterance: wait (briefly) for speech to start, then collect until a
        short silence tail or the phrase cap. Returns raw PCM (b'' if nothing was heard)."""
        al = self._al
        chunk = al.CHUNK
        frame_secs = chunk / float(al.RATE)
        wait_frames = max(1, int(_LISTEN_TIMEOUT / frame_secs))
        phrase_frames = max(1, int(_PHRASE_LIMIT / frame_secs))
        silence_tail = 0.5
        if self._threshold is None:
            self._calibrate()
        threshold = self._threshold or 200.0
        collected: list[bytes] = []
        started = False
        waited = 0
        silence = 0.0
        while True:
            try:
                fr = self._stream.read(chunk)
            except Exception:
                break
            if not fr:
                break
            rms = al.rms_of_frame(fr)
            if not started:
                waited += 1
                if rms >= threshold:
                    started = True
                    collected.append(fr)
                elif waited >= wait_frames:
                    break   # gave up waiting for speech to begin this chunk
            else:
                collected.append(fr)
                silence = 0.0 if rms >= threshold else silence + frame_secs
                if silence >= silence_tail or len(collected) >= phrase_frames:
                    break
        return b"".join(collected)

    def __call__(self) -> str:
        try:
            with self._lock:
                if _paused:
                    self.release()
                    return ""
                self._ensure_open()
                raw = self._read_phrase()
            if not raw:
                return ""
            audio = self._sr.AudioData(raw, self._al.RATE, self._al.WIDTH)
            return _recognize(self._rec, audio)
        except Exception:
            self.release()
            time.sleep(0.3)
            return ""


def _real_capture_factory():
    """Persistent-stream mic capture. Returns None if the speech stack is unavailable (so the
    loop degrades to a no-op, not a crash). On failure it records a human-readable reason in the
    module `_last_error` so start()/the UI can tell the user WHAT to fix — the difference between
    'Hey Ember silently does nothing' and 'install a mic backend' / 'grant mic permission'.

    Backend order: PyAudio-backed sr.Microphone first (steady OS mic indicator), then the
    portable sounddevice capture so wake word works out of the box without a PyAudio build."""
    global _last_error
    try:
        import speech_recognition as sr
    except Exception:
        _last_error = ("Voice input isn’t installed yet. Install it with: "
                       "pip install SpeechRecognition sounddevice")
        return None
    # 1) Preferred: PyAudio-backed persistent stream.
    pa_error = ""
    try:
        cap = _MicCapture(sr)
        # Open the stream once, now, so a missing-pyaudio / denied-permission / no-device
        # failure surfaces here (synchronously) instead of dying quietly on the daemon thread.
        cap._ensure_open()
        _last_error = ""
        return cap
    except Exception as e:
        pa_error = str(e)
    # 2) Fallback: portable sounddevice capture (no PyAudio needed).
    try:
        cap = _SoundDeviceCapture(sr)
        _last_error = ""
        return cap
    except Exception as e:
        combined = f"{pa_error}; {e}".lower()
        if "permission" in combined or "denied" in combined or "-50" in combined:
            _last_error = ("Ember doesn’t have Microphone permission. Grant it in System "
                           "Settings ▸ Privacy & Security ▸ Microphone, then reopen Ember.")
        elif ("no default input" in combined or "no device" in combined
              or "no microphone" in combined or "invalid" in combined):
            _last_error = "No microphone was found. Plug one in (or check your input device) and retry."
        elif "pyaudio" in combined or "sounddevice" in combined or "portaudio" in combined:
            _last_error = ("Microphone support isn’t installed. Install it with: "
                           "pip install sounddevice  (PyAudio also works; on macOS run "
                           "brew install portaudio first for PyAudio).")
        else:
            _last_error = f"Microphone couldn’t start: {e}"
        return None


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def _record(text: str) -> None:
    global _detections
    with _LOCK:
        _detections += 1
        _events.append({"time": _now_iso(), "heard": (text or "")[:80]})


def _loop(stop: "threading.Event", capture=None) -> None:
    capture = capture or _CAPTURE or _real_capture_factory()
    if capture is None:
        with _LOCK:
            globals()["_running"] = False
        return
    while not stop.is_set():
        if _paused:
            # Release the held mic stream so a voice turn can take the device + the OS
            # "mic in use" indicator goes off while we're not actively listening.
            rel = getattr(capture, "release", None)
            if rel:
                try:
                    rel()
                except Exception:
                    pass
            stop.wait(0.3)
            continue
        try:
            text = capture()
        except Exception:
            stop.wait(0.5)
            continue
        if text:
            # Record that the mic produced *something* (separate from wake hits) so a
            # diagnostic can distinguish "mic is dead/denied" from "just no wake phrase".
            global _last_heard, _heard_count
            with _LOCK:
                _last_heard = text[:80]
                _heard_count += 1
        if text and detect_wake(text):
            _record(text)
            cb = _on_wake
            if cb:
                try:
                    cb()
                except Exception:
                    pass
            stop.wait(_COOLDOWN)  # don't re-trigger on the tail of the same phrase
    # Loop is stopping — release the mic stream so it doesn't linger as "in use".
    rel = getattr(capture, "release", None)
    if rel:
        try:
            rel()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start(on_wake=None) -> dict:
    """Start always-on wake-word listening. Idempotent. `on_wake` is called (on the
    daemon thread) each time the wake phrase is heard."""
    global _thread, _stop_event, _running, _on_wake, _paused, _last_error
    if on_wake is not None:
        _on_wake = on_wake
    with _LOCK:
        if _running and _thread is not None and _thread.is_alive():
            return {"ok": True, "running": True, "message": "wake word already listening"}
    # Open the mic SYNCHRONOUSLY before we claim to be running, so is_running()/this return
    # value reflect reality. Previously the thread discovered a missing mic a moment later and
    # the caller had already been told "running", so "Hey Ember" died silently.
    capture = _CAPTURE or _real_capture_factory()
    if capture is None:
        with _LOCK:
            _running = False
        return {"ok": False, "running": False, "error": _last_error or "microphone unavailable"}
    with _LOCK:
        _paused = False
        _last_error = ""
        _stop_event = threading.Event()
        stop = _stop_event
        _thread = threading.Thread(target=_loop, args=(stop, capture),
                                   name="ember-wake-word", daemon=True)
        _running = True
        _thread.start()
    return {"ok": True, "running": True, "message": "listening for 'hey ember'"}


def stop() -> dict:
    global _thread, _stop_event, _running
    with _LOCK:
        running = _running
        ev = _stop_event
        th = _thread
        _running = False
        _stop_event = None
        _thread = None
    if not running or th is None:
        return {"ok": True, "message": "wake word was not running"}
    if ev is not None:
        ev.set()
    th.join(timeout=4.0)
    return {"ok": True, "message": "wake word stopped"}


def pause() -> None:
    """Temporarily stop reacting (e.g. while a command is being captured) without
    tearing down the thread — keeps 'listening forever' intact."""
    global _paused
    _paused = True


def resume() -> None:
    global _paused
    _paused = False


def is_running() -> bool:
    with _LOCK:
        return bool(_running and _thread is not None and _thread.is_alive())


def is_paused() -> bool:
    return _paused


def last_error() -> str:
    """The most recent reason the mic couldn't start (''/empty when healthy)."""
    return _last_error


def status() -> dict:
    with _LOCK:
        running = bool(_running and _thread is not None and _thread.is_alive())
        last = _events[-1] if _events else None
        n = _detections
        heard = _heard_count
        last_heard = _last_heard
    return {"ok": True, "running": running, "paused": _paused,
            "detections": n, "last": last,
            "heard_count": heard, "last_heard": last_heard}
