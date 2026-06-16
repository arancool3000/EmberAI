"""Tests for ai_detect (text + image AI detection heuristics)."""
import tempfile
from pathlib import Path

import ai_detect

_AI = ("In today's fast-paced world, it is important to note that technology plays a crucial "
       "role in our lives. Moreover, it is worth noting that innovation underscores progress. "
       "Furthermore, the realm of computing continues to evolve. In conclusion, we must leverage "
       "these robust tools. Additionally, this seamless integration paves the way for success.")

_HUMAN = ("ok so i tried the new place downtown last night. honestly? kinda mid. the fries were "
          "great though, can't lie. we waited like 40 min for a table which sucked. my friend "
          "spilled his drink everywhere lol. i'd go back but only for those fries tbh.")


def test_text_ai_scores_higher_than_human():
    a = ai_detect.detect_text(_AI)
    h = ai_detect.detect_text(_HUMAN)
    assert a["ok"] and h["ok"], (a, h)
    assert a["ai_likelihood"] > h["ai_likelihood"], (a["ai_likelihood"], h["ai_likelihood"])
    assert a["ai_likelihood"] >= 55, a
    assert h["ai_likelihood"] <= 45, h


def test_text_too_short():
    assert ai_detect.detect_text("too short")["ok"] is False


def test_image_with_sd_metadata_flagged():
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    d = tempfile.mkdtemp(prefix="ai_img_")
    p = Path(d) / "gen.png"
    meta = PngInfo()
    meta.add_text("parameters", "masterpiece, 8k, Steps: 30, Sampler: Euler a, Model: SDXL")
    Image.new("RGB", (8, 8), (100, 120, 140)).save(p, pnginfo=meta)
    r = ai_detect.detect_image(str(p))
    assert r["ok"] and r["ai_likelihood"] >= 80, r


def test_image_plain_uncertain():
    from PIL import Image
    d = tempfile.mkdtemp(prefix="ai_img2_")
    p = Path(d) / "plain.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(p)
    r = ai_detect.detect_image(str(p))
    assert r["ok"] and 30 <= r["ai_likelihood"] <= 60, r


def test_image_missing():
    assert ai_detect.detect_image("/nope/missing.png")["ok"] is False


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
