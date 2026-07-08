"""Microphone loudness metering as an Ember tool.

`sound_level` records a short mic sample and reports how loud it is — a perceptual
level (0..1), dBFS (mean & peak), and a plain word (quiet/moderate/loud). It answers
questions like "how loud is my keyboard", "how noisy is this room", or "is the fan
loud". It reuses audio_level's pure signal helpers and its INJECTABLE stream factory
(`audio_level._STREAM_FACTORY`), so the math is unit-testable with scripted frames and
never has to touch a real microphone.

dBFS is decibels relative to full scale (0 dBFS = the loudest a 16-bit sample can be),
so real-world values are <= 0. That's an honest, calibration-free measure of *relative*
loudness — a mic without lab calibration can't report true sound-pressure dB (SPL). If
you have calibrated yours, pass `spl_offset_db` to also get an estimated dB SPL.
"""
from __future__ import annotations

import math
import threading

import audio_level


def _dbfs(rms: float) -> float:
    """16-bit RMS -> dBFS, reference = full scale (32768). Floors at -120 for silence."""
    if rms <= 0:
        return -120.0
    return round(max(-120.0, 20.0 * math.log10(rms / 32768.0)), 1)


def _descriptor(dbfs_peak: float) -> str:
    """Map a peak dBFS reading to a human word."""
    if dbfs_peak < -55:
        return "near silence"
    if dbfs_peak < -40:
        return "very quiet"
    if dbfs_peak < -28:
        return "quiet"
    if dbfs_peak < -18:
        return "moderate"
    if dbfs_peak < -9:
        return "loud"
    return "very loud"


def measure_sound_level(seconds: float = 1.5, spl_offset_db=None, **_ignored) -> dict:
    """Record ~`seconds` of mic audio and report loudness. Read-only, no side effects."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 1.5
    seconds = max(0.2, min(10.0, seconds))

    factory = audio_level._STREAM_FACTORY or audio_level._PyAudioStream
    try:
        from voice import MIC_LOCK  # share the mic lock so we don't clash with the wake word
    except Exception:
        MIC_LOCK = threading.RLock()

    frame_secs = audio_level.CHUNK / float(audio_level.RATE)
    n_frames = max(1, int(round(seconds / frame_secs)))

    rms_values: list[float] = []
    stream = None
    try:
        with MIC_LOCK:
            try:
                stream = factory()
            except Exception as e:
                msg = str(e).lower()
                if "pyaudio" in msg:
                    return {"ok": False, "error": "PyAudio is missing. Run: pip install pyaudio"}
                return {"ok": False, "error": f"mic error: {e}"}
            for _ in range(n_frames):
                try:
                    frame = stream.read(audio_level.CHUNK)
                except Exception:
                    frame = b""
                rms_values.append(audio_level.rms_of_frame(frame))
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    if not rms_values:
        return {"ok": False, "error": "no audio captured"}

    mean_rms = sum(rms_values) / len(rms_values)
    peak_rms = max(rms_values)
    dbfs_mean = _dbfs(mean_rms)
    dbfs_peak = _dbfs(peak_rms)

    # Dead / muted mic guard: essentially zero signal across the whole sample.
    if peak_rms < 1.0:
        return {
            "ok": True, "seconds": round(seconds, 2), "level": 0.0,
            "dbfs_mean": dbfs_mean, "dbfs_peak": dbfs_peak,
            "descriptor": "silent",
            "note": "No signal detected — is the mic muted or is permission granted?",
        }

    out = {
        "ok": True,
        "seconds": round(seconds, 2),
        "samples": len(rms_values),
        "level": round(audio_level.normalize_level(mean_rms), 3),       # perceptual 0..1
        "level_peak": round(audio_level.normalize_level(peak_rms), 3),
        "dbfs_mean": dbfs_mean,
        "dbfs_peak": dbfs_peak,
        "descriptor": _descriptor(dbfs_peak),
        "note": "dBFS is relative to full scale (0 = max); not calibrated SPL.",
    }
    if spl_offset_db is not None:
        try:
            off = float(spl_offset_db)
            out["est_db_spl_mean"] = round(dbfs_mean + off, 1)
            out["est_db_spl_peak"] = round(dbfs_peak + off, 1)
            out["spl_note"] = "Estimated SPL = dBFS + your calibration offset."
        except (TypeError, ValueError):
            pass
    return out


TOOL_DECLARATIONS = [
    {
        "name": "sound_level",
        "description": (
            "Measure how loud the microphone input is right now. Records a short sample and "
            "returns a perceptual level (0..1), dBFS (mean and peak), and a word like "
            "quiet/moderate/loud. Use it for questions like 'how loud is my keyboard', 'how "
            "noisy is this room', or 'is the fan loud'. dBFS is relative to full scale "
            "(0 = loudest), so values are <= 0; it is NOT calibrated sound-pressure dB (SPL) "
            "unless you pass spl_offset_db from a calibrated mic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "seconds": {"type": "NUMBER", "description": "Seconds to sample (default 1.5, clamped 0.2–10)."},
                "spl_offset_db": {"type": "NUMBER", "description": "Optional mic calibration offset; if set, also returns an estimated dB SPL."},
            },
            "required": [],
        },
    },
]

TOOL_DISPATCH = {"sound_level": measure_sound_level}

# Purely a read-only sensor: no side effects, safe to run without a confirmation prompt.
READONLY_TOOLS = {"sound_level"}
