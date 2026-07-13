"""Tests for quick_tools (password / QR / metadata stripper)."""
import tempfile
from pathlib import Path

import quick_tools


def test_password_length_and_classes():
    r = quick_tools.password_generate(16)
    assert r["ok"] and len(r["password"]) == 16
    pw = r["password"]
    assert any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)


def test_password_clamped():
    assert quick_tools.password_generate(2)["length"] == 8


def test_qr_make():
    try:
        import qrcode  # noqa: F401
    except Exception:
        return  # qrcode not installed in this env -> skip (it's in requirements.txt)
    d = tempfile.mkdtemp(prefix="qr_")
    out = Path(d) / "q.png"
    r = quick_tools.qr_make("https://ember.example", str(out))
    assert r["ok"] and out.exists() and out.stat().st_size > 0, r


def test_strip_metadata_removes_ai_tags():
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    import ai_detect
    d = tempfile.mkdtemp(prefix="strip_")
    src = Path(d) / "gen.png"
    meta = PngInfo()
    meta.add_text("parameters", "masterpiece, Model: SDXL")
    Image.new("RGB", (8, 8), (10, 80, 160)).save(src, pnginfo=meta)
    assert ai_detect.detect_image(str(src))["ai_likelihood"] >= 80  # flagged before
    r = quick_tools.strip_metadata(str(src))
    assert r["ok"], r
    after = ai_detect.detect_image(r["output"])
    assert after["ai_likelihood"] < 80, after  # metadata gone -> no longer flagged by tags


def _run():
    import types
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
