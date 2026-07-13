"""Speech-to-text for push-to-talk: transcribe a recorded clip with a LOCAL Whisper model
when one is installed (fast, private, offline), falling back to cloud STT (Gemini) and finally
the free Google Web Speech endpoint.

The engine-selection logic is a PURE function (`pick_stt_engine`) so it's unit-tested with no
audio, no models and no network. The actual transcribers are best-effort and never raise.
"""
from __future__ import annotations

import os

# Order we *prefer* engines in "auto" mode: local-first (fast + private), then cloud, then the
# free web endpoint. Offline mode drops every networked engine.
_DEFAULT_ORDER = ["whisper", "gemini", "google"]
_OFFLINE_ORDER = ["whisper", "sphinx"]


def pick_stt_engine(prefer: str, *, whisper_ok: bool, has_gemini_key: bool,
                    offline: bool, sr_ok: bool) -> list:
    """Return the ordered list of STT engines to try given what's available.
    `prefer` is auto|whisper|gemini|google. Availability gates each engine so the caller can
    just walk the list. Pure + deterministic."""
    pref = (prefer or "auto").lower().strip()
    head = {"whisper": ["whisper"], "gemini": ["gemini"], "google": ["google"]}.get(pref, [])
    base = _OFFLINE_ORDER if offline else _DEFAULT_ORDER
    out: list = []
    for e in head + base:
        if e in out:
            continue
        if e == "whisper" and not whisper_ok:
            continue
        if e == "gemini" and (offline or not has_gemini_key):
            continue
        if e == "google" and (offline or not sr_ok):
            continue
        if e == "sphinx" and not sr_ok:
            continue
        out.append(e)
    return out


# --- availability probes (cached; never raise) ------------------------------------------
_whisper_kind: str | None = None   # None=unprobed, ""=none, "faster"/"openai"


def whisper_backend() -> str:
    """Which local Whisper is importable: 'faster' (faster-whisper), 'openai' (whisper), or ''."""
    global _whisper_kind
    if _whisper_kind is not None:
        return _whisper_kind
    for mod, kind in (("faster_whisper", "faster"), ("whisper", "openai")):
        try:
            __import__(mod)
            _whisper_kind = kind
            return _whisper_kind
        except Exception:
            continue
    _whisper_kind = ""
    return _whisper_kind


def whisper_available() -> bool:
    return bool(whisper_backend())


def _sr_available() -> bool:
    try:
        import speech_recognition  # noqa: F401
        return True
    except Exception:
        return False


# --- transcribers (best-effort) ----------------------------------------------------------
_whisper_model_cache: dict = {}


def _transcribe_whisper(wav_path: str, model_name: str = "base") -> str:
    kind = whisper_backend()
    if not kind:
        return ""
    try:
        if kind == "faster":
            from faster_whisper import WhisperModel
            m = _whisper_model_cache.get(("faster", model_name))
            if m is None:
                m = WhisperModel(model_name, device="cpu", compute_type="int8")
                _whisper_model_cache[("faster", model_name)] = m
            segments, _info = m.transcribe(wav_path, beam_size=1)
            return " ".join(s.text for s in segments).strip()
        else:
            import whisper
            m = _whisper_model_cache.get(("openai", model_name))
            if m is None:
                m = whisper.load_model(model_name)
                _whisper_model_cache[("openai", model_name)] = m
            return (m.transcribe(wav_path).get("text") or "").strip()
    except Exception:
        return ""


def _transcribe_gemini(wav_path: str, api_key: str) -> str:
    if not api_key:
        return ""
    try:
        from google import genai
        client = genai.Client(api_key="".join(api_key.split()))
        with open(wav_path, "rb") as f:
            data = f.read()
        # Inline the audio (clips are short); ask for a bare transcript.
        from google.genai import types
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                "Transcribe this audio to plain text. Return ONLY the words spoken, nothing else.",
                types.Part.from_bytes(data=data, mime_type="audio/wav"),
            ],
        )
        return (getattr(resp, "text", "") or "").strip()
    except Exception:
        return ""


def _transcribe_google(wav_path: str, offline: bool = False) -> str:
    try:
        import speech_recognition as sr
        rec = sr.Recognizer()
        with sr.AudioFile(wav_path) as src:
            audio = rec.record(src)
        if offline:
            try:
                return (rec.recognize_sphinx(audio) or "").strip()
            except Exception:
                return ""
        try:
            return (rec.recognize_google(audio) or "").strip()
        except Exception:
            try:
                return (rec.recognize_sphinx(audio) or "").strip()
            except Exception:
                return ""
    except Exception:
        return ""


def transcribe_audio(wav_path: str, *, prefer: str = "auto", gemini_key: str = "",
                     offline: bool = False, whisper_model: str = "base") -> dict:
    """Transcribe a WAV file, walking the available engines in order. Returns
    {ok, text, engine, tried}. Never raises."""
    if not wav_path or not os.path.exists(wav_path):
        return {"ok": False, "text": "", "engine": "", "error": "no audio"}
    engines = pick_stt_engine(prefer, whisper_ok=whisper_available(),
                              has_gemini_key=bool(gemini_key), offline=offline,
                              sr_ok=_sr_available())
    tried = []
    for e in engines:
        tried.append(e)
        if e == "whisper":
            text = _transcribe_whisper(wav_path, whisper_model)
        elif e == "gemini":
            text = _transcribe_gemini(wav_path, gemini_key)
        elif e in ("google", "sphinx"):
            text = _transcribe_google(wav_path, offline=(e == "sphinx" or offline))
        else:
            text = ""
        if text:
            return {"ok": True, "text": text, "engine": e, "tried": tried}
    return {"ok": False, "text": "", "engine": "", "tried": tried,
            "error": ("no speech-to-text engine available — install faster-whisper for offline "
                      "voice, or add a Gemini API key" if not engines else "no speech recognised")}
