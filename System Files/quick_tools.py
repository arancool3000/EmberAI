"""Small, high-value utility tools for Ember: strong passwords, QR codes, and a
privacy-minded image metadata stripper."""
from __future__ import annotations

import secrets
import string
from pathlib import Path


def password_generate(length: int = 20, symbols: bool = True) -> dict:
    """Generate a strong random password (guarantees a mix of character classes)."""
    length = max(8, min(128, int(length or 20)))
    pool = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{};:,.?" if symbols else "")
    pw = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase),
          secrets.choice(string.digits)]
    if symbols:
        pw.append(secrets.choice("!@#$%^&*()-_=+"))
    pw += [secrets.choice(pool) for _ in range(length - len(pw))]
    rng = secrets.SystemRandom()
    rng.shuffle(pw)
    return {"ok": True, "password": "".join(pw), "length": length}


def qr_make(text: str, output: str = "") -> dict:
    """Make a QR-code PNG for any text/URL/Wi-Fi string."""
    if not text:
        return {"ok": False, "error": "text required"}
    try:
        import qrcode
    except Exception:
        return {"ok": False, "error": "qrcode not installed"}
    base = Path.home() / "Desktop"
    if not base.exists():
        base = Path.home()
    out = Path(output).expanduser() if output else base / "ember_qr.png"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(text).save(str(out))
        return {"ok": True, "output": str(out)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def strip_metadata(path: str, output: str = "") -> dict:
    """Remove EXIF / text metadata from an image (GPS location, camera info, AI-generator
    tags) before sharing it. Writes a cleaned copy."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        from PIL import Image
    except Exception:
        return {"ok": False, "error": "Pillow not installed"}
    out = Path(output).expanduser() if output else p.with_name(p.stem + "_clean" + p.suffix)
    try:
        with Image.open(p) as im:
            clean = Image.new(im.mode, im.size)
            clean.putdata(list(im.getdata()))
            clean.save(str(out))
        return {"ok": True, "output": str(out), "note": "EXIF + embedded metadata removed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
