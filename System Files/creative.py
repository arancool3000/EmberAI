"""Creative AI tools (use your Gemini key from Settings):
- generate_image    : text -> image (Imagen, if your key has access)
- describe_image    : vision Q&A on any image file
- transcribe_audio  : audio file -> transcript + summary
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _settings() -> dict:
    import json
    from app_data import data_dir
    d = data_dir()
    p = d / "settings.json"
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _gemini_key() -> str:
    return "".join((_settings().get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").split())


def _dest(output: str, default: str) -> Path:
    base = Path.home() / "Desktop"
    if not base.exists():
        base = Path.home()
    p = Path(output).expanduser() if output else base / default
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _mime(p: Path, kind: str) -> str:
    ext = p.suffix.lower().lstrip(".")
    if kind == "image":
        return {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
    return {"mp3": "audio/mp3", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg",
            "flac": "audio/flac", "aac": "audio/aac"}.get(ext, "audio/mpeg")


def generate_image(prompt: str, output: str = "") -> dict:
    """Generate an image from a text prompt (needs a Gemini key with image-model access)."""
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    key = _gemini_key()
    if not key:
        return {"ok": False, "error": "Add a Gemini API key in Ember Settings to generate images."}
    try:
        from google import genai
        client = genai.Client(api_key=key)
        out = _dest(output, "ember_image.png")
        resp = client.models.generate_images(model="imagen-3.0-generate-002", prompt=prompt,
                                              config={"number_of_images": 1})
        imgs = getattr(resp, "generated_images", None) or []
        if not imgs:
            return {"ok": False, "error": "no image returned (your key may lack image-model access)"}
        data = imgs[0].image.image_bytes
        out.write_bytes(data)
        return {"ok": True, "output": str(out)}
    except Exception as e:
        return {"ok": False, "error": f"image generation failed: {e}"}


def describe_image(path: str, question: str = "") -> dict:
    """Vision Q&A: describe an image or answer a question about it."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    key = _gemini_key()
    if not key:
        return {"ok": False, "error": "Add a Gemini API key in Ember Settings."}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[types.Part.from_bytes(data=p.read_bytes(), mime_type=_mime(p, "image")),
                      question.strip() or "Describe this image in detail."])
        return {"ok": True, "answer": (getattr(resp, "text", "") or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def transcribe_audio(path: str) -> dict:
    """Transcribe an audio file and summarize it."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    key = _gemini_key()
    if not key:
        return {"ok": False, "error": "Add a Gemini API key in Ember Settings."}
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[types.Part.from_bytes(data=p.read_bytes(), mime_type=_mime(p, "audio")),
                      "Transcribe this audio verbatim, then add a 2-sentence summary."])
        return {"ok": True, "transcript": (getattr(resp, "text", "") or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
