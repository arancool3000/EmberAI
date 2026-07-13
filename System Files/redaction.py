"""Redact secrets (API keys, passwords, tokens, PII) from text, data structures,
and screenshots, so credentials don't leak into logs, the persisted action log,
the audit trail, or screenshots sent to the cloud LLM.

`scrub_text` and `scrub_obj` are pure and side-effect free; `redact_image` masks
secret-looking OCR words on a screenshot when the caller supplies word boxes.
"""
from __future__ import annotations

import re

# Ordered: more specific patterns first (e.g. sk-ant- before sk-).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{20,}")
_URL_CREDS = re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")
_ASSIGN = re.compile(
    r"(?i)\b(pass(?:word|wd)?|pwd|secret|api[_-]?key|access[_-]?token|token|auth)\b"
    r"(\s*[:=]\s*)['\"]?[^\s'\"]{4,}")
_CC = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def scrub_text(s):
    """Return (redacted_text, num_secrets_found). Non-strings pass through unchanged."""
    if not isinstance(s, str) or not s:
        return s, 0
    count = 0

    out = s
    for label, pat in _PATTERNS:
        def repl(_m, _l=label):
            nonlocal count
            count += 1
            return f"[REDACTED:{_l}]"
        out = pat.sub(repl, out)

    def _bearer(_m):
        nonlocal count
        count += 1
        return f"{_m.group(1)} [REDACTED]"
    out = _BEARER.sub(_bearer, out)

    def _creds(_m):
        nonlocal count
        count += 1
        return f"{_m.group(1)}[REDACTED]@"
    out = _URL_CREDS.sub(_creds, out)

    def _assign(_m):
        nonlocal count
        count += 1
        return f"{_m.group(1)}{_m.group(2)}[REDACTED]"
    out = _ASSIGN.sub(_assign, out)

    def _cc(_m):
        nonlocal count
        if _luhn(_m.group(0)):
            count += 1
            return "[REDACTED:credit_card]"
        return _m.group(0)
    out = _CC.sub(_cc, out)
    return out, count


def contains_secret(s) -> bool:
    return scrub_text(s)[1] > 0


def scrub_obj(obj):
    """Recursively redact strings inside dicts / lists / tuples."""
    if isinstance(obj, str):
        return scrub_text(obj)[0]
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_obj(v) for v in obj)
    return obj


def _looks_like_secret_token(text: str) -> bool:
    """Heuristic for a high-entropy credential-ish token in OCR output."""
    t = (text or "").strip()
    if len(t) < 16 or " " in t:
        return False
    classes = sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    return classes >= 2 and bool(re.fullmatch(r"[A-Za-z0-9._\-+/=]{16,}", t))


def redact_image(image, words, fill="black"):
    """Black out OCR word-boxes whose text looks secret.

    `image`: a PIL.Image. `words`: iterable of dicts with a 'text' and a box as
    either 'box'/'bbox' = [x1, y1, x2, y2]. Returns the (mutated) image.
    """
    try:
        from PIL import ImageDraw
    except Exception:
        return image
    draw = ImageDraw.Draw(image)
    for w in words or []:
        text = w.get("text", "")
        if contains_secret(text) or _looks_like_secret_token(text):
            box = w.get("box") or w.get("bbox")
            if box and len(box) == 4:
                x1, y1, x2, y2 = box
                draw.rectangle([x1, y1, x2, y2], fill=fill)
    return image
