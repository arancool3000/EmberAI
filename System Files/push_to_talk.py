"""Push-to-talk: hold a hotkey to talk to Ember. Zero-latency — recording starts the instant
you press the key (no "Hey Ember" wake word to wait for, no false triggers) and stops the moment
you release it; the captured clip is then transcribed and submitted.

This module is the PURE coordinator / state machine. Every side effect — capturing audio,
transcribing, delivering the final text, reporting state — is an injected hook, so the whole
press → release → transcribe → submit flow is unit-tested with no microphone, no model and no
GUI. The OS key listening and the real recorder/transcriber are wired in by the UI.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"


class PushToTalk:
    """Coordinates one hold-to-talk cycle at a time.

    Hooks:
      start_record() -> None        begin capturing the mic
      stop_record()  -> audio|None  stop capturing; return a WAV path/bytes (or None if nothing)
      transcribe(audio) -> str      turn the clip into text
      on_text(text)  -> None        deliver the final transcript (e.g. send it to the agent)
      on_state(state)               optional: notified on every state change (for UI glow)
      on_error(msg)                 optional: notified on failure
    """

    def __init__(self, *, start_record: Callable[[], None],
                 stop_record: Callable[[], object],
                 transcribe: Callable[[object], str],
                 on_text: Callable[[str], None],
                 on_state: Optional[Callable[[str], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 run_async: bool = True, min_hold_ms: float = 120.0,
                 clock: Optional[Callable[[], float]] = None):
        self._start_record = start_record
        self._stop_record = stop_record
        self._transcribe = transcribe
        self._on_text = on_text
        self._on_state = on_state or (lambda s: None)
        self._on_error = on_error or (lambda e: None)
        self._run_async = run_async
        self._min_hold_ms = float(min_hold_ms)
        self._clock = clock or (lambda: time.monotonic() * 1000.0)
        self.state = IDLE
        self._press_at = 0.0
        self._lock = threading.RLock()

    # --- key events ---------------------------------------------------------------------
    def press(self) -> None:
        """Key down: start recording (ignored if a cycle is already underway)."""
        with self._lock:
            if self.state != IDLE:
                return
            self.state = RECORDING
            self._press_at = self._clock()
        try:
            self._start_record()
        except Exception as e:
            with self._lock:
                self.state = IDLE
            self._emit_state(IDLE)
            self._on_error(f"could not start recording: {e}")
            return
        self._emit_state(RECORDING)

    def release(self) -> None:
        """Key up: stop recording and transcribe (off-thread unless run_async=False)."""
        with self._lock:
            if self.state != RECORDING:
                return
            held = self._clock() - self._press_at
            self.state = TRANSCRIBING
        self._emit_state(TRANSCRIBING)

        def _work():
            try:
                audio = self._stop_record()
            except Exception as e:
                self._finish("", error=f"recording failed: {e}")
                return
            if held < self._min_hold_ms or not audio:
                # too short to be real speech (an accidental tap) — quietly reset
                self._finish("")
                return
            try:
                text = self._transcribe(audio) or ""
            except Exception as e:
                self._finish("", error=f"transcription failed: {e}")
                return
            self._finish(text)

        if self._run_async:
            threading.Thread(target=_work, daemon=True).start()
        else:
            _work()

    def cancel(self) -> None:
        """Abort an in-progress recording WITHOUT transcribing (e.g. the user hit Esc)."""
        with self._lock:
            if self.state != RECORDING:
                return
            self.state = IDLE
        try:
            self._stop_record()
        except Exception:
            pass
        self._emit_state(IDLE)

    # --- internals ----------------------------------------------------------------------
    def _finish(self, text: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.state = IDLE
        self._emit_state(IDLE)
        if error:
            self._on_error(error)
        elif (text or "").strip():
            self._on_text(text.strip())

    def _emit_state(self, state: str) -> None:
        try:
            self._on_state(state)
        except Exception:
            pass

    @property
    def is_active(self) -> bool:
        return self.state != IDLE


def pynput_key(name: str):
    """Resolve a key name ('f9', 'space', 'a', 'cmd') to a pynput Key/KeyCode for matching.
    Returns None if pynput is unavailable. Raises nothing the caller must handle beyond None."""
    try:
        from pynput import keyboard as pk
    except Exception:
        return None
    n = (name or "").strip().lower()
    if not n:
        return None
    # named/special keys live on pk.Key (f1..f20, space, enter, etc.)
    special = getattr(pk.Key, n, None)
    if special is not None:
        return special
    if len(n) == 1:
        try:
            return pk.KeyCode.from_char(n)
        except Exception:
            return None
    return None


# macOS virtual key codes for keys people pick for push-to-talk (NSEvent path; pynput's
# background listener crashes on macOS, so the UI uses an NSEvent monitor there instead).
MAC_KEYCODES = {
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "space": 49, "return": 36, "enter": 36, "tab": 48, "escape": 53, "right": 124,
    "left": 123, "down": 125, "up": 126, "rightcmd": 54, "rightoption": 61, "rightalt": 61,
}


def mac_keycode(name: str) -> int | None:
    return MAC_KEYCODES.get((name or "").strip().lower())
