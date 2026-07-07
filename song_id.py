"""Ambient song identification for Ember — "what song is this?".

Records a short clip from the microphone and identifies the track by ACOUSTIC FINGERPRINT.
This is the Shazam-style capability a plain speech->speech voice model can't do: a live-voice
model only listens for *speech* and talks back, so it ignores music playing in the room. Ember
instead captures the audio and fingerprints it, then the (tool-enabled) voice assistant speaks
the result. Wired as an agent TOOL, so saying "what song is this?" in voice chat calls it.

Providers, tried in order:
  1. shazamio  — unofficial Shazam, NO API key needed. Preferred.
  2. AudD      — set AUDD_API_TOKEN (env) or config; small free tier.
If neither is usable it returns a clear, actionable message (which the assistant can offer to
fix by writing itself the missing capability — see self_extend.py).

Standard-library only at import time. pyaudio + shazamio/requests are imported lazily inside the
worker so importing this module stays light. `_RECORDER` and `_IDENTIFY` are module-level
injection points so tests run fully offline — no microphone, no network.
"""
from __future__ import annotations

import os
import tempfile
import wave

# Injection points for tests (and for swapping the backend):
#   _RECORDER(seconds:int, path:str) -> str   — record mic audio to `path`, return it
#   _IDENTIFY(wav_path:str) -> dict            — fingerprint+lookup, return a normalized dict
_RECORDER = None
_IDENTIFY = None

# Optional config (e.g. an AudD token) set by the UI; env vars also work.
_CONFIG: dict = {}

_SAMPLE_RATE = 44100
_CHANNELS = 1
_CHUNK = 4096
_MAX_SECONDS = 30
_MIN_SECONDS = 3


def set_config(**kw) -> None:
    """UI hook: e.g. set_config(audd_token='...'). Env AUDD_API_TOKEN also works."""
    _CONFIG.update({k: v for k, v in kw.items() if v is not None})


def _audd_token() -> str:
    return (_CONFIG.get("audd_token") or os.environ.get("AUDD_API_TOKEN")
            or os.environ.get("AUDD_TOKEN") or "").strip()


# ---------------------------------------------------------------------------
# Recording (pyaudio) — mic input; captures a song playing out loud in the room.
# ---------------------------------------------------------------------------
def _record_pyaudio(seconds: int, path: str) -> str:
    import pyaudio
    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream = pa.open(format=pyaudio.paInt16, channels=_CHANNELS, rate=_SAMPLE_RATE,
                         input=True, frames_per_buffer=_CHUNK)
        frames = []
        for _ in range(int(_SAMPLE_RATE / _CHUNK * seconds)):
            frames.append(stream.read(_CHUNK, exception_on_overflow=False))
        with wave.open(path, "wb") as wf:
            wf.setnchannels(_CHANNELS)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
        return path
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        finally:
            pa.terminate()


def record_ambient(seconds: int = 12, path: str | None = None) -> str:
    """Record `seconds` of mic audio to a WAV and return its path."""
    seconds = max(_MIN_SECONDS, min(_MAX_SECONDS, int(seconds)))
    if path is None:
        fd, path = tempfile.mkstemp(prefix="ember_song_", suffix=".wav")
        os.close(fd)
    rec = _RECORDER or _record_pyaudio
    return rec(seconds, path)


# ---------------------------------------------------------------------------
# Identification backends
# ---------------------------------------------------------------------------
def _run_async(coro):
    """Run an async coroutine from this (sync, non-loop) worker thread."""
    import asyncio
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _normalize_shazam(out: dict) -> dict:
    track = (out or {}).get("track") or {}
    if not track:
        return {"ok": True, "found": False}
    sub = track.get("subtitle") or ""            # artist
    sections = track.get("sections") or []
    album = ""
    for s in sections:
        for md in (s.get("metadata") or []):
            if (md.get("title") or "").lower() == "album":
                album = md.get("text") or ""
    return {
        "ok": True, "found": True, "provider": "shazam",
        "title": track.get("title") or "",
        "artist": sub,
        "album": album,
        "url": track.get("url") or (track.get("share") or {}).get("href") or "",
    }


def _identify_shazamio(wav_path: str) -> dict:
    from shazamio import Shazam
    shazam = Shazam()
    out = _run_async(shazam.recognize(wav_path))
    return _normalize_shazam(out)


def _identify_audd(wav_path: str, token: str) -> dict:
    import requests
    with open(wav_path, "rb") as f:
        r = requests.post("https://api.audd.io/",
                          data={"api_token": token, "return": "apple_music,spotify"},
                          files={"file": f}, timeout=25)
    j = r.json()
    res = j.get("result")
    if not res:
        return {"ok": True, "found": False}
    return {
        "ok": True, "found": True, "provider": "audd",
        "title": res.get("title") or "",
        "artist": res.get("artist") or "",
        "album": res.get("album") or "",
        "url": (res.get("song_link") or ""),
    }


def _identify(wav_path: str) -> dict:
    """Try shazamio (no key) first, then AudD (token). Returns a normalized dict, or a dict
    with ok=False + a hint if no backend is available."""
    if _IDENTIFY is not None:
        return _IDENTIFY(wav_path)
    # 1) shazamio — no key
    try:
        import shazamio  # noqa: F401  (probe availability)
        return _identify_shazamio(wav_path)
    except ImportError:
        pass
    except Exception as e:
        # shazamio present but the lookup failed — fall through to AudD if we can.
        if not _audd_token():
            return {"ok": False, "error": f"Shazam lookup failed: {e}"}
    # 2) AudD — token
    token = _audd_token()
    if token:
        try:
            return _identify_audd(wav_path, token)
        except Exception as e:
            return {"ok": False, "error": f"AudD lookup failed: {e}"}
    return {"ok": False, "no_backend": True,
            "error": "No song-ID backend available.",
            "hint": "Install the 'shazamio' package (no key needed) or set AUDD_API_TOKEN. "
                    "Ember can write itself this capability if you ask it to."}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
def identify_song(seconds: int = 12) -> dict:
    """Listen to the music playing right now and identify the song. Records ~`seconds` of audio
    from the microphone and fingerprints it. Play the song out loud near the mic first."""
    try:
        seconds = max(_MIN_SECONDS, min(_MAX_SECONDS, int(seconds)))
    except (TypeError, ValueError):
        seconds = 12
    wav = None
    try:
        wav = record_ambient(seconds)
    except Exception as e:
        return {"ok": False, "error": f"Couldn't record audio: {e}",
                "hint": "Check the microphone permission and that a mic is connected."}
    try:
        res = _identify(wav)
    finally:
        try:
            if wav and _RECORDER is None:   # only clean up files we created via the real recorder
                os.unlink(wav)
        except Exception:
            pass
    if not res.get("ok"):
        return res
    if not res.get("found"):
        return {"ok": True, "found": False,
                "message": "I listened but couldn't recognize the song. Try again with the "
                           "music louder / clearer, or a longer clip."}
    title, artist = res.get("title", ""), res.get("artist", "")
    nice = f"{title} — {artist}".strip(" —") or "an unrecognized track"
    return {"ok": True, "found": True, "title": title, "artist": artist,
            "album": res.get("album", ""), "url": res.get("url", ""),
            "provider": res.get("provider", ""),
            "message": f"That's “{title}” by {artist}." if (title and artist)
                       else f"That sounds like {nice}."}


TOOL_DECLARATIONS = [
    {
        "name": "identify_song",
        "description": ("Listen to the music playing right now and name the song (Shazam-style "
                        "acoustic fingerprint). Use this whenever the user asks what song/track "
                        "is playing, to name a tune, or 'what's this music'. Records a short clip "
                        "from the microphone, so the song should be audible in the room."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "seconds": {"type": "INTEGER",
                            "description": "how long to listen for (default 12, 3-30)"},
            },
            "required": [],
        },
    },
]

TOOL_DISPATCH = {"identify_song": identify_song}

# It records from the mic (a device interaction) but writes nothing the user must undo.
READONLY_TOOLS: set = set()
INTERACTION_TOOLS = {"identify_song"}
