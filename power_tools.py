"""Power tools across categories:
- read_document   : extract text from PDF/txt/csv/docx/xlsx (so Ember can chat with your docs)
- scan_secrets    : flag API keys / tokens / PII in text before you share it (privacy)
- secure_delete   : overwrite-then-delete a file (file shredder)  [DESTRUCTIVE]
- unit_convert    : length / mass / data / temperature conversions
"""
from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path

_TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".html", ".xml", ".yaml", ".yml", ".ini"}


def read_document(path: str, max_chars: int = 20000) -> dict:
    """Extract plain text from a document so it can be summarized / queried."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            import PyPDF2
            with open(p, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
        elif ext in _TEXT_EXT:
            text = p.read_text(errors="replace")
        elif ext == ".docx":
            import zipfile
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            xml = xml.replace("</w:p>", "\n")
            text = _html.unescape(re.sub(r"<[^>]+>", "", xml))
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    rows.append("\t".join("" if c is None else str(c) for c in row))
            text = "\n".join(rows)
        else:
            return {"ok": False, "error": f"unsupported type: {ext or '(none)'}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    text = (text or "").strip()
    cap = max(1000, int(max_chars or 20000))
    return {"ok": True, "path": str(p), "chars": len(text),
            "text": text[:cap], "truncated": len(text) > cap}


_SECRET_PATTERNS = [
    ("AWS access key id", r"AKIA[0-9A-Z]{16}"),
    ("Google API key", r"AIza[0-9A-Za-z\-_]{35}"),
    ("Slack token", r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    ("GitHub token", r"gh[pousr]_[0-9A-Za-z]{30,}"),
    ("OpenAI-style key", r"sk-[A-Za-z0-9]{20,}"),
    ("Private key block", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("JWT", r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    ("Email address", r"[\w.+-]+@[\w-]+\.[\w.-]{2,}"),
    ("Credit-card-like number", r"\b(?:\d[ -]?){13,16}\b"),
]


def scan_secrets(text: str) -> dict:
    """Flag likely secrets / PII in text (counts only — never echoes the values)."""
    t = text or ""
    found = []
    for name, pat in _SECRET_PATTERNS:
        n = len(re.findall(pat, t))
        if n:
            found.append({"type": name, "count": n})
    return {"ok": True, "has_secrets": bool(found), "found": found,
            "note": "Heuristic scan; review before sharing. Values are not shown."}


def secure_delete(path: str, passes: int = 1) -> dict:
    """Overwrite a file with random data, then delete it (file shredder). DESTRUCTIVE."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    passes = max(1, min(7, int(passes or 1)))
    try:
        size = p.stat().st_size
        with open(p, "r+b", buffering=0) as f:
            for _ in range(passes):
                f.seek(0)
                f.write(os.urandom(size) if size else b"")
                f.flush()
                os.fsync(f.fileno())
        p.unlink()
        return {"ok": True, "shredded": str(p), "passes": passes, "bytes": size}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_UNITS = {
    "mm": ("len", 0.001), "cm": ("len", 0.01), "m": ("len", 1.0), "km": ("len", 1000.0),
    "in": ("len", 0.0254), "ft": ("len", 0.3048), "yd": ("len", 0.9144), "mi": ("len", 1609.344),
    "mg": ("mass", 0.001), "g": ("mass", 1.0), "kg": ("mass", 1000.0),
    "oz": ("mass", 28.349523), "lb": ("mass", 453.59237),
    "b": ("data", 1.0), "kb": ("data", 1024.0), "mb": ("data", 1024.0 ** 2),
    "gb": ("data", 1024.0 ** 3), "tb": ("data", 1024.0 ** 4),
}
_TEMP = {"c": "c", "celsius": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}


def unit_convert(value, from_unit: str, to_unit: str) -> dict:
    """Convert between length / mass / data-size / temperature units."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return {"ok": False, "error": "value must be a number"}
    f = (from_unit or "").strip().lower()
    t = (to_unit or "").strip().lower()
    if f in _TEMP or t in _TEMP:
        if f not in _TEMP or t not in _TEMP:
            return {"ok": False, "error": "mix of temperature and non-temperature units"}
        f, t = _TEMP[f], _TEMP[t]
        c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[f]
        out = {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[t]
        return {"ok": True, "value": v, "from": f, "to": t, "result": round(out, 4)}
    if f not in _UNITS:
        return {"ok": False, "error": f"unknown unit: {from_unit}"}
    if t not in _UNITS:
        return {"ok": False, "error": f"unknown unit: {to_unit}"}
    if _UNITS[f][0] != _UNITS[t][0]:
        return {"ok": False, "error": f"incompatible units: {f} vs {t}"}
    result = (v * _UNITS[f][1]) / _UNITS[t][1]
    return {"ok": True, "value": v, "from": f, "to": t, "result": round(result, 6)}
