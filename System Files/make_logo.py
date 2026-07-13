"""Generate the Ember app logo: a warm "deep ember" squircle with a four-point
AI spark (replaces the old flame mark).

Palette is the toned-down "C2 / deep ember" gradient (amber -> orange -> red,
with the bright yellow pulled back). Outputs icon.png (1024), a multi-size
icon.ico, and logo_preview.png (256). Pure-PIL, no extra deps.
Run:  python3 make_logo.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
SS = 4              # supersample factor for crisp anti-aliasing
OUT = 1024
S = OUT * SS

# "Deep ember" diagonal gradient (top-left -> bottom-right).
GRADIENT = [(228, 145, 55), (220, 88, 38), (176, 38, 40)]
SPARK = (255, 247, 237, 255)        # cream main spark
SPARK_SMALL = (255, 237, 213, 235)  # slightly softer secondary spark


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _diagonal_gradient(size, stops):
    """3-stop diagonal (top-left -> bottom-right) gradient, built small then upscaled."""
    g = 256
    base = Image.new("RGB", (g, g))
    px = base.load()
    c0, c1, c2 = stops
    for y in range(g):
        for x in range(g):
            t = (x + y) / (2 * (g - 1))
            px[x, y] = _lerp(c0, c1, t / 0.5) if t < 0.5 else _lerp(c1, c2, (t - 0.5) / 0.5)
    return base.resize((size, size), Image.BICUBIC)


def _squircle_mask(size, radius_frac=0.235):
    """iOS-style rounded-square alpha mask."""
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * radius_frac), fill=255)
    return m


def _apply_mask(rgba, mask):
    out = rgba.copy()
    a = out.split()[3]
    out.putalpha(Image.composite(a, Image.new("L", a.size, 0), mask))
    return out


def _star4(cx, cy, R, r):
    """A four-point concave star (AI "spark"): outer points N/E/S/W, concave on diagonals."""
    pts = []
    for k in range(4):
        a = math.radians(90 * k) - math.pi / 2
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
        a2 = math.radians(90 * k + 45) - math.pi / 2
        pts.append((cx + r * math.cos(a2), cy + r * math.sin(a2)))
    return pts


# macOS app-icon grid: the rounded-square artwork occupies ~80% of the canvas with
# transparent padding around it (≈824/1024). Filling the whole canvas (as before) makes
# Ember render ~10-20% larger than every neighbouring Dock icon. We render the design into
# an inner square and composite it, centered, onto a transparent full-size canvas so Ember
# matches the system grid.
MARGIN_FRAC = 0.10


def build():
    D = int(round(S * (1 - 2 * MARGIN_FRAC)))   # supersampled design size (~80% of the canvas)
    off = (S - D) // 2                            # transparent margin on each side

    # 1. Warm gradient fill, masked to the squircle (rendered at the inner design size).
    grad = _diagonal_gradient(D, GRADIENT)
    mask = _squircle_mask(D)
    design = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    design.paste(grad, (0, 0), mask)

    # 2. Soft top-left gloss highlight.
    gloss = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse([-D * 0.15, -D * 0.35, D * 0.95, D * 0.55], fill=(255, 255, 255, 34))
    gloss = gloss.filter(ImageFilter.GaussianBlur(D // 60))
    design = Image.alpha_composite(design, _apply_mask(gloss, mask))

    # 3. Subtle drop shadow under the spark so it lifts off the gradient.
    sh = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(_star4(D // 2, int(D * 0.5 + D * 0.012), int(D * 0.27), int(D * 0.085)),
                               fill=(70, 12, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(D // 90))
    design = Image.alpha_composite(design, _apply_mask(sh, mask))

    # 4. The AI spark: a large four-point star + a small companion spark.
    d = ImageDraw.Draw(design)
    d.polygon(_star4(D // 2, int(D * 0.5), int(D * 0.27), int(D * 0.085)), fill=SPARK)
    d.polygon(_star4(int(D * 0.72), int(D * 0.29), int(D * 0.085), int(D * 0.028)), fill=SPARK_SMALL)

    # 5. Subtle inner border for definition.
    r = int(D * 0.235)
    ImageDraw.Draw(design).rounded_rectangle(
        [D // 200, D // 200, D - D // 200, D - D // 200], radius=r,
        outline=(255, 255, 255, 34), width=max(2, D // 240))

    # 6. Composite the design, centered, onto a full transparent canvas (the macOS padding).
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(design, (off, off), design)

    # Downsample for anti-aliasing and write the assets.
    final = icon.resize((OUT, OUT), Image.LANCZOS)
    final.save(HERE / "icon.png")
    final.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                         (64, 64), (128, 128), (256, 256)])
    final.resize((256, 256), Image.LANCZOS).save(HERE / "logo_preview.png")
    print("wrote icon.png (1024), icon.ico (multi-size), logo_preview.png (256)")


if __name__ == "__main__":
    build()
