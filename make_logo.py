"""Generate the Ember app logo: a modern squircle mark with a premium indigo->cyan
gradient, a custom geometric 'A', and a thin orbital ring (the 'holds the world' nod).

Outputs icon.png (1024) and a multi-size icon.ico. Pure-PIL, no extra deps.
Run:  python3 make_logo.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
SS = 4  # supersample factor for crisp anti-aliasing
OUT = 1024
S = OUT * SS


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _diagonal_gradient(size, stops):
    """3-stop diagonal (top-left -> bottom-right) gradient, built small then upscaled."""
    g = 256
    base = Image.new("RGB", (g, g))
    px = base.load()
    (c0, c1, c2) = stops
    for y in range(g):
        for x in range(g):
            t = (x + y) / (2 * (g - 1))  # 0 at TL, 1 at BR
            if t < 0.5:
                col = _lerp(c0, c1, t / 0.5)
            else:
                col = _lerp(c1, c2, (t - 0.5) / 0.5)
            px[x, y] = col
    return base.resize((size, size), Image.BICUBIC)


def _squircle_mask(size, radius_frac=0.235):
    """iOS-style rounded-square (squircle-ish) alpha mask."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int(size * radius_frac)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return m


def _draw_A(layer, cx, top, bot, half_w, stroke):
    """Custom geometric capital A on an RGBA layer (white), with a triangular cut-out."""
    d = ImageDraw.Draw(layer)
    bl = (cx - half_w, bot)
    br = (cx + half_w, bot)
    apex = (cx, top)
    # Outer triangle (solid white).
    d.polygon([apex, bl, br], fill=(255, 255, 255, 255))
    # Inner triangle cut-out (replace -> transparent), inset by stroke width.
    inset_top = top + int(stroke * 1.55)
    inner_half = half_w - stroke
    ibl = (cx - inner_half, bot - int(stroke * 0.0))
    ibr = (cx + inner_half, bot - int(stroke * 0.0))
    iapex = (cx, inset_top)
    d.polygon([iapex, ibl, ibr], fill=(0, 0, 0, 0))
    # Crossbar (re-add white), positioned in the lower third.
    bar_y = top + int((bot - top) * 0.66)
    bar_h = int(stroke * 0.92)
    # Width of crossbar follows the inner triangle edges at bar_y.
    frac = (bar_y - inset_top) / max(1, (bot - inset_top))
    half_at = inner_half * frac
    d.rectangle([cx - half_at, bar_y - bar_h // 2, cx + half_at, bar_y + bar_h // 2],
                fill=(255, 255, 255, 255))


def build():
    # 1. Gradient fill, masked to the squircle.
    grad = _diagonal_gradient(S, [(58, 32, 142), (124, 58, 237), (24, 200, 230)])
    mask = _squircle_mask(S)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    # 2. Soft top-left gloss highlight.
    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([-S * 0.15, -S * 0.35, S * 0.95, S * 0.55], fill=(255, 255, 255, 46))
    gloss = gloss.filter(ImageFilter.GaussianBlur(S // 60))
    icon = Image.alpha_composite(icon, _apply_mask(gloss, mask))

    # 3. Orbital ring behind the letter (thin rotated ellipse, the 'world' nod).
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    cx, cy = S // 2, int(S * 0.52)
    rw, rh = int(S * 0.40), int(S * 0.165)
    rd.ellipse([cx - rw, cy - rh, cx + rw, cy + rh], outline=(255, 255, 255, 70),
               width=max(2, S // 150))
    # brighter cyan accent arc on the lower-right of the ring
    rd.arc([cx - rw, cy - rh, cx + rw, cy + rh], start=10, end=150,
           fill=(120, 240, 255, 180), width=max(3, S // 120))
    ring = ring.rotate(-20, center=(cx, cy), resample=Image.BICUBIC)
    icon = Image.alpha_composite(icon, _apply_mask(ring, mask))

    # 4. The geometric A.
    a_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _draw_A(a_layer, cx=S // 2, top=int(S * 0.255), bot=int(S * 0.745),
            half_w=int(S * 0.205), stroke=int(S * 0.072))
    # tiny shadow for the A to lift it off the gradient
    sh = a_layer.filter(ImageFilter.GaussianBlur(S // 110))
    icon = Image.alpha_composite(icon, _tint(sh, (10, 8, 30, 120)))
    icon = Image.alpha_composite(icon, a_layer)

    # 5. Subtle inner border for definition.
    bd = ImageDraw.Draw(icon)
    r = int(S * 0.235)
    bd.rounded_rectangle([S // 200, S // 200, S - S // 200, S - S // 200], radius=r,
                         outline=(255, 255, 255, 38), width=max(2, S // 240))

    # Downsample for anti-aliasing.
    final = icon.resize((OUT, OUT), Image.LANCZOS)
    final.save(HERE / "icon.png")
    # Multi-size ICO for Windows.
    final.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                         (64, 64), (128, 128), (256, 256)])
    # Small preview for quick viewing.
    final.resize((256, 256), Image.LANCZOS).save(HERE / "logo_preview.png")
    print("wrote icon.png (1024), icon.ico (multi-size), logo_preview.png (256)")


def _apply_mask(rgba, mask):
    out = rgba.copy()
    a = out.split()[3]
    out.putalpha(Image.composite(a, Image.new("L", a.size, 0), mask))
    return out


def _tint(rgba, color):
    """Recolor a layer's opaque pixels to `color` (keeps its alpha shape)."""
    alpha = rgba.split()[3]
    solid = Image.new("RGBA", rgba.size, color)
    solid.putalpha(alpha)
    return solid


if __name__ == "__main__":
    build()
