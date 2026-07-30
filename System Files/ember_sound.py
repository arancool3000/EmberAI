"""Ember's sound design — synthesised cues with real envelopes, not two-tone beeps.

Every cue is generated as PCM at call time rather than shipped as an asset, which keeps the
repo free of binaries and lets a cue be re-voiced by changing numbers instead of re-cutting
a file.

What makes these sound finished rather than like system beeps:

* **Envelopes.** Each partial gets an attack/decay/sustain/release curve, so notes bloom and
  fade. A raw gated sine is what produces the "beep" quality, and the click it makes at the
  gate edges is most of why beeps sound cheap.
* **Chords, not single tones.** Cues are built from intervals — a major sixth for *on*, a
  falling fourth for *off* — so they read as musical events.
* **Detuned partials.** Each note is stacked with slightly detuned copies, which produces
  the slow chorus shimmer that makes a synth sound wide instead of thin.
* **A soft-knee limiter and fade-out.** The buffer is normalised with headroom and always
  ends at exactly zero, so playback can never click on the final sample.

Everything up to the byte buffer is pure Python with no dependencies, so it is unit-tested
headlessly (``test_ember_sound.py``). Playback shells out to whatever the platform provides
and always runs off the GUI thread.
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

SAMPLE_RATE = 44100

#: Equal-tempered semitone offsets from A4, so cues can be written as musical intervals.
A4 = 440.0


def note(semitones_from_a4: float) -> float:
    """Frequency of a note given in semitones relative to A4."""
    return A4 * (2.0 ** (float(semitones_from_a4) / 12.0))


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

class ADSR:
    """Attack / decay / sustain / release, in seconds (sustain is a level, 0..1).

    The release is measured back from the end of the note, so a cue's total length is
    whatever the caller asks for and the envelope fits inside it.
    """

    def __init__(self, attack: float = 0.01, decay: float = 0.08,
                 sustain: float = 0.6, release: float = 0.25):
        self.attack = max(0.0005, float(attack))
        self.decay = max(0.0, float(decay))
        self.sustain = max(0.0, min(1.0, float(sustain)))
        self.release = max(0.001, float(release))

    def at(self, t: float, duration: float) -> float:
        """Envelope level at time ``t`` within a note of length ``duration``."""
        if t < 0.0 or t > duration:
            return 0.0
        rel_start = max(self.attack, duration - self.release)
        if t < self.attack:
            return t / self.attack
        if t < self.attack + self.decay and t < rel_start:
            k = (t - self.attack) / max(1e-6, self.decay)
            return 1.0 + (self.sustain - 1.0) * k
        if t < rel_start:
            return self.sustain
        # Release: fall from wherever the envelope currently is to silence, with a curve
        # rather than a straight line so the tail sounds like it decays instead of ducking.
        k = (t - rel_start) / max(1e-6, duration - rel_start)
        return self.sustain * ((1.0 - k) ** 2.2)


# ---------------------------------------------------------------------------
# Synthesis (pure)
# ---------------------------------------------------------------------------

class Voice:
    """One note: a frequency, an envelope, and an optional pitch glide."""

    def __init__(self, freq: float, duration: float, *, env: Optional[ADSR] = None,
                 gain: float = 1.0, start: float = 0.0, glide_to: Optional[float] = None,
                 detune: float = 0.0, harmonics: Sequence[float] = (1.0, 0.32, 0.14)):
        self.freq = float(freq)
        self.duration = max(0.01, float(duration))
        self.env = env or ADSR()
        self.gain = float(gain)
        self.start = max(0.0, float(start))
        self.glide_to = None if glide_to is None else float(glide_to)
        self.detune = float(detune)
        self.harmonics = tuple(harmonics)

    def render(self, rate: int = SAMPLE_RATE) -> List[float]:
        """Mono float samples for this voice alone, starting at t=0 of the voice."""
        n = int(self.duration * rate)
        out = [0.0] * n
        # Integrated phase, so a glide stays continuous instead of stepping in frequency
        # (recomputing phase = 2*pi*f(t)*t per sample produces audible zipper artefacts).
        phases = [0.0] * len(self.harmonics)
        for i in range(n):
            t = i / rate
            k = t / self.duration
            f = self.freq if self.glide_to is None else \
                self.freq + (self.glide_to - self.freq) * (k * k * (3 - 2 * k))
            if self.detune:
                f *= 1.0 + self.detune
            amp = self.env.at(t, self.duration) * self.gain
            s = 0.0
            for h, (mult, weight) in enumerate(zip(
                    range(1, len(self.harmonics) + 1), self.harmonics)):
                phases[h] += 2.0 * math.pi * f * mult / rate
                s += math.sin(phases[h]) * weight
            out[i] = s * amp
        return out


def mix(voices: Iterable[Voice], rate: int = SAMPLE_RATE) -> List[float]:
    """Sum voices onto one timeline, honouring each voice's start offset."""
    voices = list(voices)
    if not voices:
        return []
    total = int(max(v.start + v.duration for v in voices) * rate) + 1
    buf = [0.0] * total
    for v in voices:
        offset = int(v.start * rate)
        for i, s in enumerate(v.render(rate)):
            j = offset + i
            if 0 <= j < total:
                buf[j] += s
    return buf


def limit(buf: Sequence[float], ceiling: float = 0.82) -> List[float]:
    """Normalise to ``ceiling`` with a soft knee, then guarantee a silent final sample.

    The tanh knee keeps transients from clipping into distortion, and the tail fade is what
    stops the click that an abruptly-truncated buffer makes on every platform.
    """
    if not buf:
        return []
    peak = max(abs(s) for s in buf) or 1.0
    scale = ceiling / peak
    out = [math.tanh(s * scale * 1.15) * 0.92 for s in buf]
    # Fade the last 4ms to exactly zero.
    tail = min(len(out), int(0.004 * SAMPLE_RATE))
    for i in range(tail):
        out[len(out) - tail + i] *= (1.0 - i / max(1, tail - 1))
    out[-1] = 0.0
    return out


def to_wav_bytes(buf: Sequence[float], rate: int = SAMPLE_RATE) -> bytes:
    """16-bit mono WAV container around ``buf``."""
    import io
    frames = b"".join(struct.pack("<h", max(-32768, min(32767, int(s * 32767))))
                      for s in buf)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(frames)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# The cue library
# ---------------------------------------------------------------------------

def _pad(freqs: Sequence[float], duration: float, *, env: ADSR,
         spread: float = 0.0035, gain: float = 1.0,
         stagger: float = 0.0) -> List[Voice]:
    """A chord where each note is tripled and slightly detuned.

    The detuned copies beat against each other slowly, which is what gives a synth pad its
    width — a single oscillator per note always sounds thin and electronic.
    """
    voices = []
    for i, f in enumerate(freqs):
        start = i * stagger
        for d in (-spread, 0.0, spread):
            voices.append(Voice(f, duration, env=env, gain=gain / 3.0,
                                start=start, detune=d))
    return voices


def cue(name: str) -> List[float]:
    """Render a named cue to float samples. Unknown names fall back to ``tick``."""
    name = str(name or "").lower()

    if name in ("voice_on", "on", "listen_start"):
        # A major sixth blooming upward — open, welcoming, resolves rather than alerts.
        env = ADSR(attack=0.05, decay=0.30, sustain=0.42, release=0.55)
        return limit(mix(_pad([note(-9), note(-2), note(3), note(7)], 0.95,
                              env=env, stagger=0.045)))

    if name in ("voice_off", "off", "listen_stop"):
        # The same chord falling a fourth and closing — the mirror of the on-cue.
        env = ADSR(attack=0.02, decay=0.22, sustain=0.30, release=0.60)
        voices = _pad([note(2), note(-3), note(-10)], 0.80, env=env, stagger=0.035)
        # A gentle downward glide under it seals the "powering down" feel.
        voices.append(Voice(note(-14), 0.80, env=ADSR(0.02, 0.2, 0.35, 0.5),
                            gain=0.55, glide_to=note(-21)))
        return limit(mix(voices))

    if name == "thinking":
        # Two soft, wide fifths — present but not attention-grabbing, since this can
        # repeat while the model works.
        env = ADSR(attack=0.09, decay=0.25, sustain=0.25, release=0.45)
        return limit(mix(_pad([note(-5), note(2)], 0.7, env=env, gain=0.7)))

    if name in ("done", "success", "complete"):
        # A rising arpeggio landing on the octave.
        env = ADSR(attack=0.012, decay=0.14, sustain=0.35, release=0.34)
        return limit(mix(_pad([note(0), note(4), note(7), note(12)], 0.55,
                              env=env, stagger=0.075)))

    if name in ("error", "fail"):
        # A minor second — dissonant on purpose — resolving downward so it reads as a
        # problem rather than as an alarm.
        env = ADSR(attack=0.008, decay=0.16, sustain=0.28, release=0.42)
        voices = _pad([note(-2), note(-1)], 0.62, env=env)
        voices.append(Voice(note(-13), 0.62, env=ADSR(0.01, 0.2, 0.3, 0.4),
                            gain=0.6, glide_to=note(-18)))
        return limit(mix(voices))

    if name in ("notify", "message"):
        env = ADSR(attack=0.01, decay=0.12, sustain=0.30, release=0.32)
        return limit(mix(_pad([note(7), note(12)], 0.5, env=env, stagger=0.09)))

    # tick: the smallest possible confirmation, for UI affordances.
    env = ADSR(attack=0.002, decay=0.05, sustain=0.0, release=0.06)
    return limit(mix([Voice(note(12), 0.10, env=env, gain=0.5)]))


CUES = ("voice_on", "voice_off", "thinking", "done", "error", "notify", "tick")


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

_enabled = True
_cache: dict = {}


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def is_enabled() -> bool:
    return _enabled


def _cue_file(name: str) -> Optional[Path]:
    """Render a cue to a temp WAV once and reuse it."""
    if name in _cache:
        return _cache[name]
    try:
        data = to_wav_bytes(cue(name))
        d = Path(tempfile.gettempdir()) / "ember-sfx"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.wav"
        path.write_bytes(data)
        _cache[name] = path
        return path
    except Exception:
        _cache[name] = None
        return None


def _play_file(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["afplay", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            for player in ("paplay", "aplay", "ffplay"):
                exe = __import__("shutil").which(player)
                if exe:
                    args = [exe, "-nodisp", "-autoexit", str(path)] \
                        if player == "ffplay" else [exe, str(path)]
                    subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    break
    except Exception:
        pass          # a missing audio device must never break the action that cued it


def play(name: str) -> bool:
    """Play a cue asynchronously. Returns False when sound is off or unavailable.

    Rendering happens on a worker thread: the first play of a cue synthesises about a
    second of audio, and that must not land on the GUI thread mid-animation.
    """
    if not _enabled:
        return False
    if os.environ.get("EMBER_SILENT"):
        return False

    def _work():
        path = _cue_file(name)
        if path:
            _play_file(path)

    threading.Thread(target=_work, daemon=True).start()
    return True


def prewarm(names: Sequence[str] = CUES) -> None:
    """Render cues ahead of time so the first real one is instant."""
    def _work():
        for n in names:
            _cue_file(n)
    threading.Thread(target=_work, daemon=True).start()
