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


def _real_capture_factory():
    """Persistent-stream mic capture. Returns None if the speech stack is unavailable
    (so the loop degrades to a no-op, not a crash)."""
    try:
        import speech_recognition as sr
    except Exception:
        return None
    try:
        return _MicCapture(sr)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def _record(text: str) -> None:
    global _detections
    with _LOCK:
        _detections += 1
        _events.append({"time": _now_iso(), "heard": (text or "")[:80]})


def _loop(stop: "threading.Event") -> None:
    capture = _CAPTURE or _real_capture_factory()
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
    global _thread, _stop_event, _running, _on_wake, _paused
    if on_wake is not None:
        _on_wake = on_wake
    with _LOCK:
        if _running and _thread is not None and _thread.is_alive():
            return {"ok": True, "running": True, "message": "wake word already listening"}
        _paused = False
        _stop_event = threading.Event()
        stop = _stop_event
        _thread = threading.Thread(target=_loop, args=(stop,), name="ember-wake-word", daemon=True)
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
