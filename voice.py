"""Speech recognition (microphone in) and text-to-speech (assistant out)."""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable


# Speech-to-text routinely hears the assistant's name "Ember" as "amber"/"ambre"/etc. In a
# voice conversation that should always be the name, so normalise it.
_NAME_MISHEAR_RE = re.compile(r"\b(amber|ambre|ambr|ahmber|umber|embah?|embre?)\b", re.IGNORECASE)

# Phrases that end a hands-free voice conversation.
_STOP_CONVO = {
    "stop", "stop listening", "stop voice", "stop voice chat", "stop chat", "goodbye",
    "good bye", "bye", "bye bye", "thats all", "that's all", "that is all", "nevermind",
    "never mind", "cancel", "go away", "dismiss", "exit", "quit", "thanks ember", "thank you ember",
}


def fix_assistant_name(text: str) -> str:
    """Correct mis-transcriptions of 'Ember' (e.g. 'amber') back to the name."""
    if not text:
        return text
    return _NAME_MISHEAR_RE.sub("Ember", text)


def is_stop_phrase(text: str) -> bool:
    """True if `text` is a phrase that should end a hands-free voice conversation."""
    t = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    return t in _STOP_CONVO


_tts_engine = None
_tts_lock = threading.Lock()
_tts_thread: threading.Thread | None = None
_say_proc = None            # current macOS `say` / audio-player subprocess (stop_speaking kills it)
_mac_voice = None           # cached best macOS voice name ("" = system default, None = unprobed)
_TTS_CONFIG: dict = {}      # set by the UI: {tts_engine, gemini_api_key, gemini_tts_voice,
                            #                  soundtools_api_key, soundtools_url, soundtools_voice}


def _offline() -> bool:
    """True when Ember is in Offline Mode (best-effort; never raises)."""
    try:
        import offline
        return offline.is_offline()
    except Exception:
        return False


def set_tts_config(cfg: dict) -> None:
    """The UI passes the relevant settings so speak() can pick the engine (system/gemini/
    soundtools) without every call site threading settings through."""
    global _TTS_CONFIG
    _TTS_CONFIG = dict(cfg or {})


def _best_mac_voice() -> str:
    """Pick the highest-quality installed macOS voice. The default pyttsx3/NSSpeech voice
    sounds robotic; the modern Premium/Enhanced Siri-class voices are dramatically better."""
    global _mac_voice
    if _mac_voice is not None:
        return _mac_voice
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        _mac_voice = ""
        return _mac_voice
    # Best → acceptable. Premium/Enhanced are the natural-sounding neural voices.
    prefs = ["Ava (Premium)", "Zoe (Premium)", "Evan (Premium)", "Nathan (Premium)",
             "Joelle (Premium)", "Ava (Enhanced)", "Samantha (Enhanced)", "Allison (Enhanced)",
             "Tom (Enhanced)", "Samantha", "Ava", "Allison", "Tom", "Daniel", "Serena"]
    for p in prefs:
        if p in out:
            _mac_voice = p
            return _mac_voice
    _mac_voice = ""   # fall back to the user's system default voice
    return _mac_voice

# A SINGLE process-wide lock guarding the microphone. Both this module's
# listen_once() AND the always-on wake-word loop (wake_word.py) acquire it before
# opening an input stream, so the two never fight for the device. On macOS, two
# simultaneous CoreAudio input streams routinely fail or return silence — that was
# the root cause of "voice chat does nothing" / "Hey Ember never triggers" when the
# wake word and a voice turn both grabbed the mic at once.
MIC_LOCK = threading.RLock()


def mic_available() -> tuple[bool, str]:
    """Best-effort check that a microphone can actually be opened. Returns (ok, detail).
    detail is a short, user-facing reason when not ok (missing deps / permission / no device).

    Safe to call on the UI thread: it never blocks on MIC_LOCK. If the lock is already held
    (the wake-word loop is actively capturing), that itself proves the mic works -> ok."""
    try:
        import speech_recognition as sr
    except Exception as e:
        return False, f"SpeechRecognition not installed ({e}). Run: pip install SpeechRecognition"
    got = MIC_LOCK.acquire(timeout=0.1)
    if not got:
        return True, "ok"   # something is already using the mic -> it's available
    try:
        mic = sr.Microphone()
        with mic:
            pass
        return True, "ok"
    except Exception as e:
        msg = str(e).lower()
        if "pyaudio" in msg:
            return False, "PyAudio is missing. Run: pip install pyaudio"
        # The classic macOS symptom: a device exists but mic permission was never granted.
        return False, ("Could not open the microphone — grant microphone permission to the app "
                       "(macOS: System Settings → Privacy & Security → Microphone) and try again.")
    finally:
        MIC_LOCK.release()


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
    """Speak `text` aloud using the configured engine. Engines (tts_engine setting):
      • 'edge'       — Microsoft Edge neural TTS: very natural, FREE, NO API key, not
                       rate-limited (needs the `edge-tts` package)
      • 'gemini'     — Gemini TTS (very natural; needs a Gemini key; rate-limited)
      • 'soundtools' — a custom HTTP TTS endpoint URL (advanced; key optional)
      • 'system'/auto — native macOS `say` (premium voice) / pyttsx3 elsewhere (free, default)
    Any engine falls back to the system voice on error so speech never silently dies."""
    if not text or not text.strip():
        return
    engine = (_TTS_CONFIG.get("tts_engine") or "system").lower()
    # Offline Mode: the system voice is the only fully-local engine; the others call the network.
    if _offline() and engine in ("edge", "gemini", "soundtools"):
        engine = "system"
    try:
        if engine == "edge":
            if _edge_tts(text):
                return
        elif engine == "gemini" and (_TTS_CONFIG.get("gemini_api_key") or "").strip():
            if _gemini_tts(text):
                return
        elif engine == "soundtools" and (_TTS_CONFIG.get("soundtools_url") or "").strip():
            # soundtools.io has no public API key — this path is for ANY custom HTTP TTS
            # endpoint you point it at (auth header sent only if you provide a key).
            if _soundtools_tts(text):
                return
    except Exception:
        pass
    _system_tts(text)


def _system_tts(text: str):
    if sys.platform == "darwin":
        _mac_say(text)
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


def _play_audio_file(path: str):
    """Play an audio file via the OS player, tracked in _say_proc so stop_speaking() can cut it."""
    global _say_proc
    stop_speaking()
    try:
        if sys.platform == "darwin":
            cmd = ["afplay", path]
        elif sys.platform.startswith("win"):
            cmd = ["powershell", "-NoProfile", "-c",
                   f"(New-Object Media.SoundPlayer '{path}').PlaySync()"]
        else:
            cmd = ["aplay", path]
        _say_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _pcm_to_wav(pcm: bytes, path: str, rate: int = 24000):
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def _gemini_tts(text: str) -> bool:
    """Gemini TTS — natural neural voice. Returns True if it spoke."""
    try:
        from google import genai
        from google.genai import types
        key = "".join((_TTS_CONFIG.get("gemini_api_key") or "").split())
        voice = (_TTS_CONFIG.get("gemini_tts_voice") or "Kore").strip() or "Kore"
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=text[:1500],
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))),
        )
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        if not pcm:
            return False
        import tempfile
        out = str(Path(tempfile.gettempdir()) / f"ember_tts_{int(time.time() * 1000)}.wav")
        _pcm_to_wav(pcm, out)
        _play_audio_file(out)
        return True
    except Exception:
        return False


def _edge_tts(text: str) -> bool:
    """Microsoft Edge neural TTS — very natural, FREE, no API key, not rate-limited. Uses the
    `edge-tts` package (lazy import) to synthesize an MP3, then plays it. Returns True if spoken."""
    try:
        import asyncio
        import tempfile
        import edge_tts
        voice = (_TTS_CONFIG.get("edge_tts_voice") or "en-US-AriaNeural").strip() or "en-US-AriaNeural"
        out = str(Path(tempfile.gettempdir()) / f"ember_tts_{int(time.time() * 1000)}.mp3")

        async def _synth():
            await edge_tts.Communicate(text[:2500], voice).save(out)

        asyncio.run(_synth())
        if not Path(out).exists() or Path(out).stat().st_size == 0:
            return False
        _play_audio_file(out)
        return True
    except Exception:
        return False


def _soundtools_tts(text: str) -> bool:
    """A configurable HTTP TTS endpoint (point soundtools_url at any service). POSTs
    {text, voice}; sends a Bearer header ONLY if you've set a key (soundtools.io itself has
    no public key). Accepts raw audio bytes or JSON {audio_url|url}. Best-effort + optional."""
    try:
        import requests
        url = (_TTS_CONFIG.get("soundtools_url") or "").strip()
        if not url:
            return False
        key = (_TTS_CONFIG.get("soundtools_api_key") or "").strip()
        voice = (_TTS_CONFIG.get("soundtools_voice") or "").strip()
        payload = {"text": text[:2000]}
        if voice:
            payload["voice"] = voice
        headers = {"Accept": "audio/mpeg"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code != 200:
            return False
        ctype = r.headers.get("Content-Type", "")
        import tempfile
        if "application/json" in ctype:
            j = r.json()
            audio_url = j.get("audio_url") or j.get("url") or j.get("output")
            if not audio_url:
                return False
            r = requests.get(audio_url, timeout=30)
            if r.status_code != 200:
                return False
        ext = ".mp3" if "mpeg" in (ctype or r.headers.get("Content-Type", "")) else ".wav"
        out = str(Path(tempfile.gettempdir()) / f"ember_tts_{int(time.time() * 1000)}{ext}")
        Path(out).write_bytes(r.content)
        _play_audio_file(out)
        return True
    except Exception:
        return False


def _mac_say(text: str):
    """Speak via macOS `say` with the best voice, interruptible (tracked in _say_proc)."""
    global _say_proc
    stop_speaking()
    try:
        voice = _best_mac_voice()
        cmd = ["say", "-r", "190"]
        if voice:
            cmd += ["-v", voice]
        _say_proc = subprocess.Popen(cmd + [text],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def is_speaking() -> bool:
    """True while a TTS playback subprocess (say/afplay/etc.) is still running. Used by the
    conversational orb loop to wait for Ember to finish talking before it listens again, so
    the mic doesn't capture Ember's own voice."""
    proc = _say_proc
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def stop_speaking():
    global _tts_engine, _say_proc
    if _say_proc is not None:
        try:
            _say_proc.terminate()
        except Exception:
            pass
        _say_proc = None
    if _tts_engine:
        try:
            _tts_engine.stop()
        except Exception:
            pass


def listen_once(on_transcript: Callable[[str, str | None], None],
                phrase_timeout: "float | None" = 6.0, listen_timeout: float = 8.0):
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
            # Hold the shared mic lock for the whole capture so the wake-word loop
            # can't open a competing input stream underneath us.
            with MIC_LOCK:
                with mic as source:
                    rec.adjust_for_ambient_noise(source, duration=0.3)
                    # phrase_time_limit=None -> "Auto": the recognizer ends the phrase on a
                    # natural pause (pause_threshold) instead of a fixed cap.
                    audio = rec.listen(source, timeout=listen_timeout,
                                       phrase_time_limit=(phrase_timeout or None))
        except sr.WaitTimeoutError:
            on_transcript("", "no speech detected")
            return
        except Exception as e:
            on_transcript("", f"mic error: {e}")
            return
        # Offline Mode: transcribe locally with PocketSphinx (no network). Otherwise use the
        # free Google Web Speech endpoint, falling back to Sphinx if the network call fails.
        if _offline():
            try:
                text = rec.recognize_sphinx(audio)
                on_transcript(text or "", None if text else "couldn't understand audio")
            except Exception:
                on_transcript("", "offline speech recognition needs PocketSphinx "
                                  "(pip install pocketsphinx).")
            return
        try:
            text = rec.recognize_google(audio)
            on_transcript(text or "", None)
        except sr.UnknownValueError:
            on_transcript("", "couldn't understand audio")
        except sr.RequestError as e:
            # Network/endpoint failure — try the offline recogniser before giving up.
            try:
                text = rec.recognize_sphinx(audio)
                if text:
                    on_transcript(text, None)
                    return
            except Exception:
                pass
            on_transcript("", f"speech API error: {e}")
        except Exception as e:
            msg = str(e)
            # The bundled flac encoder being the wrong CPU type is a packaging problem, not a
            # transient mic error — give an actionable fix instead of a cryptic Errno.
            if "bad cpu type" in msg.lower() or ("flac" in msg.lower() and "errno" in msg.lower()):
                on_transcript("", "audio encoder (flac) is the wrong CPU type for this Mac. "
                                  "Fix: run `brew install flac`, then reopen Ember (a native flac "
                                  "on PATH replaces the broken bundled one).")
            else:
                on_transcript("", f"transcribe failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
