"""File & media tools: inspect a file, convert media formats (ffmpeg)."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


def file_info(path: str) -> dict:
    """Type, size, SHA-256, and (for images) dimensions."""
    try:
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        st = p.stat()
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        info = {"ok": True, "path": str(p), "size_bytes": st.st_size,
                "size_mb": round(st.st_size / 1048576, 3), "suffix": p.suffix.lower(),
                "sha256": h.hexdigest()}
        try:
            from PIL import Image
            with Image.open(p) as im:
                info["image"] = {"width": im.width, "height": im.height,
                                 "mode": im.mode, "format": im.format}
        except Exception:
            pass
        return info
    except Exception as e:
        return {"ok": False, "error": str(e)}


def media_convert(src: str, dst: str) -> dict:
    """Convert audio/video/images between formats using ffmpeg (must be installed)."""
    s = Path(src).expanduser()
    d = Path(dst).expanduser()
    if not s.exists():
        return {"ok": False, "error": f"source not found: {src}"}
    if not shutil.which("ffmpeg"):
        return {"ok": False, "error": "ffmpeg not installed (brew install ffmpeg)"}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-i", str(s), str(d)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and d.exists():
            return {"ok": True, "output": str(d), "size_mb": round(d.stat().st_size / 1048576, 2)}
        return {"ok": False, "error": (r.stderr or "ffmpeg failed")[-400:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
