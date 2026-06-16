"""Ember AI-content detector — flags likely AI-generated TEXT and IMAGES.

Honest scope: this is a heuristic + provenance detector, not a trained classifier like
GPTZero. For text it combines burstiness (sentence-length variance), AI "tell" phrases,
and contraction usage into a likelihood. For images it reads generator metadata /
content-credentials and camera EXIF. Treat the score as a strong signal, not proof —
short, edited, or re-saved content is unreliable. An optional LLM cross-check
(detect_text_llm) adds the model's own judgment when a key is available.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

# Phrases that disproportionately show up in LLM prose.
_AI_PHRASES = [
    "as an ai", "as a large language model", "it's important to note",
    "it is important to note", "it's worth noting", "it is worth noting",
    "in conclusion", "in summary", "delve into", "delve", "tapestry", "navigating the",
    "in today's fast-paced world", "moreover", "furthermore", "a testament to",
    "underscores", "plays a crucial role", "plays a vital role", "plays a significant role",
    "when it comes to", "ever-evolving", "ever-changing", "first and foremost",
    "last but not least", "needless to say", "rich tapestry", "seamless", "leverage",
    "realm of", "the world of", "unlock the", "unleash", "elevate your", "robust",
    "it is essential", "in the realm of", "paving the way", "shed light on",
    "at the end of the day", "harness the power",
]

_AI_IMG_MARKERS = [
    "stable diffusion", "stablediffusion", "automatic1111", "comfyui", "midjourney",
    "dall-e", "dall·e", "dalle", "firefly", "adobe firefly", "imagen", "stability ai",
    "novelai", "invokeai", "sdxl", "flux", "latent diffusion", "text-to-image",
]


def detect_text(text: str) -> dict:
    """Heuristic likelihood (0-100) that TEXT is AI-generated."""
    t = (text or "").strip()
    words = re.findall(r"[A-Za-z']+", t)
    n = len(words)
    if n < 25:
        return {"ok": False, "error": "need at least ~25 words of text for a reliable signal"}
    sentences = [s for s in re.split(r"[.!?]+", t) if s.strip()]
    lens = [len(re.findall(r"[A-Za-z']+", s)) for s in sentences] or [n]
    mean = sum(lens) / len(lens)
    std = math.sqrt(sum((x - mean) ** 2 for x in lens) / len(lens))
    cv = (std / mean) if mean else 0.0                      # burstiness
    burstiness_ai = _clamp((0.65 - cv) / 0.65)             # uniform sentences -> AI

    low = " " + t.lower() + " "
    phrase_hits = sum(low.count(p) for p in _AI_PHRASES)
    phrase_ai = _clamp((phrase_hits / max(1.0, n / 100.0)) / 3.0)

    contractions = len(re.findall(r"\b\w+'(?:t|s|re|ve|ll|d|m)\b", t.lower()))
    contraction_rate = contractions / max(1.0, n / 100.0)
    contraction_ai = _clamp((1.3 - contraction_rate) / 1.3)  # few contractions -> AI

    uniq = len({w.lower() for w in words})
    ttr = uniq / max(1, n)

    score = 0.42 * burstiness_ai + 0.34 * phrase_ai + 0.24 * contraction_ai
    likelihood = int(round(score * 100))
    verdict = ("likely AI-generated" if likelihood >= 65
               else "likely human-written" if likelihood <= 35
               else "uncertain / mixed")
    return {"ok": True, "ai_likelihood": likelihood, "verdict": verdict,
            "signals": {"burstiness_cv": round(cv, 3), "ai_phrase_hits": phrase_hits,
                        "contractions": contractions, "type_token_ratio": round(ttr, 3),
                        "words": n, "sentences": len(sentences)},
            "note": "Heuristic signal, not proof. Short or edited text is unreliable."}


def detect_image(path: str) -> dict:
    """Likelihood (0-100) that an IMAGE is AI-generated, from metadata + EXIF provenance."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        from PIL import Image, ExifTags
    except Exception:
        return {"ok": False, "error": "Pillow not installed"}
    found, has_camera, software = [], False, ""
    try:
        with Image.open(p) as im:
            meta = {**(getattr(im, "text", {}) or {}), **(im.info or {})}
            blob = " ".join(str(v).lower() for v in meta.values() if isinstance(v, (str, bytes)))
            found += [m for m in _AI_IMG_MARKERS if m in blob]
            lower_keys = {k.lower() for k in meta}
            for k in ("parameters", "prompt", "workflow", "dream", "sd-metadata", "comment"):
                if k in lower_keys and k != "comment":
                    found.append(f"key:{k}")
            try:
                exif = im.getexif()
            except Exception:
                exif = None
            if exif:
                tagmap = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                if str(tagmap.get("Make", "")).strip() or str(tagmap.get("Model", "")).strip():
                    has_camera = True
                software = str(tagmap.get("Software", "") or "")
                if any(m in software.lower() for m in _AI_IMG_MARKERS):
                    found.append(f"software:{software}")
        head = p.read_bytes()[:300000].lower()
        if b"c2pa" in head or b"jumbf" in head:
            found.append("c2pa-content-credentials")
    except Exception as e:
        return {"ok": False, "error": str(e)}

    found = sorted(set(found))
    ai_meta = any(m in _AI_IMG_MARKERS for m in found) or any(
        f.startswith(("key:", "software:")) for f in found)
    if ai_meta:
        likelihood, verdict = 92, "likely AI-generated (generator metadata found)"
    elif "c2pa-content-credentials" in found:
        likelihood, verdict = 70, "has content-credentials — inspect provenance"
    elif has_camera:
        likelihood, verdict = 15, "likely a real photo (camera EXIF present)"
    else:
        likelihood, verdict = 50, "uncertain (no camera EXIF and no AI metadata)"
    return {"ok": True, "ai_likelihood": likelihood, "verdict": verdict,
            "signals": {"markers": found, "has_camera_exif": has_camera, "software": software},
            "note": "Metadata-based. Screenshots / re-saves / cropped images often strip these clues."}


def detect_text_llm(text: str, settings: dict) -> dict:
    """Optional cross-check: ask the configured model whether TEXT reads as AI-generated.
    Combined with detect_text's heuristic for a blended verdict."""
    base = detect_text(text)
    prompt = ("Classify whether the following text was AI-generated. Reply with ONLY a JSON "
              'object: {"ai_likelihood": <0-100 int>, "reason": "<one sentence>"}.\n\nTEXT:\n'
              + (text or "")[:8000])
    llm = _ask_model(prompt, settings or {})
    out = {"ok": True, "heuristic": base if base.get("ok") else None, "llm_raw": llm}
    m = re.search(r'"ai_likelihood"\s*:\s*(\d+)', llm or "")
    if m:
        llm_score = max(0, min(100, int(m.group(1))))
        heur = base.get("ai_likelihood", llm_score) if base.get("ok") else llm_score
        blended = int(round(0.5 * heur + 0.5 * llm_score))
        out.update(ai_likelihood=blended, llm_likelihood=llm_score,
                   verdict=("likely AI-generated" if blended >= 65
                            else "likely human-written" if blended <= 35 else "uncertain / mixed"))
    else:
        out.update(ai_likelihood=base.get("ai_likelihood"), verdict=base.get("verdict"))
    return out


def _ask_model(prompt: str, settings: dict) -> str:
    provider = (settings.get("provider") or "").strip().lower()
    model = (settings.get("model_id") or settings.get("gemini_model") or "").strip()
    if not provider:
        provider = "claude" if "claude" in model.lower() else "gemini"
    try:
        if provider == "claude":
            key = "".join((settings.get("anthropic_api_key") or "").split())
            if not key:
                return ""
            import anthropic
            c = anthropic.Anthropic(api_key=key)
            mdl = model if "claude" in model.lower() else (settings.get("anthropic_model") or "claude-opus-4-8")
            r = c.messages.create(model=mdl, max_tokens=200,
                                  messages=[{"role": "user", "content": prompt}])
            return "".join(getattr(b, "text", "") for b in (r.content or []))
        key = "".join((settings.get("gemini_api_key") or "").split())
        if not key:
            return ""
        from google import genai
        c = genai.Client(api_key=key)
        mdl = model if model and "claude" not in model.lower() else "gemini-3.1-flash-lite"
        return getattr(c.models.generate_content(model=mdl, contents=prompt), "text", "") or ""
    except Exception:
        return ""


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
