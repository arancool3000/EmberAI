"""Generate the Ember app logo: a warm squircle mark with an amber->orange->red
gradient and a glowing flame (matching the site's ember-logo.svg).

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

# Flame outlines in SVG viewBox units (0..100), sampled from ember-logo.svg.
# Each entry is a cubic bezier segment: (p0, c1, c2, p3).
_OUTER = [
    ((50, 18),   (54.6, 30.4), (68.5, 36.4), (68.5, 57.2)),
    ((68.5, 57.2), (68.5, 71),   (60.2, 81),   (50, 81)),
    ((50, 81),   (39.8, 81),   (31.5, 71),   (31.5, 57.2)),
    ((31.5, 57.2), (31.5, 46.4), (39.2, 41.1), (42.3, 32.6)),
    ((42.3, 32.6), (43.1, 41.8), (46.9, 45.7), (48.5, 48.8)),
    ((48.5, 48.8), (50.0, 40.3), (47.7, 28.0), (50.0, 18.0)),
]
_CORE = [
    ((50, 46),    (53.1, 52.2), (59.2, 56),   (59.2, 64.5)),
    ((59.2, 64.5), (59.2, 72.2), (55.1, 77.6), (50, 77.6)),
    ((50, 77.6),  (44.9, 77.6), (40.8, 72.2), (40.8, 64.5)),
    ((40.8, 64.5), (40.8, 58.3), (45.1, 55.3), (46.9, 50.7)),
    ((46.9, 50.7), (47.4, 55.3), (49.1, 56.8), (50.0, 58.4)),
    ((50.0, 58.4), (50.9, 53.8), (50, 49.2),   (50, 46)),
]


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


def _vertical_gradient(size, top, bottom):
    """Top->bottom RGB gradient, built small then upscaled."""
    g = 256
    base = Image.new("RGB", (1, g))
    px = base.load()
    for y in range(g):
        px[0, y] = _lerp(top, bottom, y / (g - 1))
    return base.resize((size, size), Image.BICUBIC)


def _squircle_mask(size, radius_frac=0.235):
    """iOS-style rounded-square (squircle-ish) alpha mask."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int(size * radius_frac)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return m


def _bezier(seg, n):
    """Sample a cubic bezier segment into n points (skips the final point)."""
    (p0, p1, p2, p3) = seg
    pts = []
    for i in range(n):
        t = i / n
        u = 1 - t
        x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
        y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _flame_polygon(segments, size, per_seg=48):
    """Flatten a list of cubic segments (SVG units) into a scaled polygon."""
    pts = []
    for seg in segments:
        pts.extend(_bezier(seg, per_seg))
    k = size / 100.0
    return [(x * k, y * k) for (x, y) in pts]


def _poly_mask(size, pts):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    return m


def build():
    # 1. Warm gradient fill (amber -> orange -> red), masked to the squircle.
    grad = _diagonal_gradient(S, [(251, 191, 36), (249, 115, 22), (220, 38, 38)])
    mask = _squircle_mask(S)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    # 2. Soft top-left gloss highlight.
    gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([-S * 0.15, -S * 0.35, S * 0.95, S * 0.55], fill=(255, 255, 255, 42))
    gloss = gloss.filter(ImageFilter.GaussianBlur(S // 60))
    icon = Image.alpha_composite(icon, _apply_mask(gloss, mask))

    # Build the flame polygons (scaled to the supersampled canvas).
    outer_pts = _flame_polygon(_OUTER, S)
    core_pts = _flame_polygon(_CORE, S)
    outer_mask = _poly_mask(S, outer_pts)
    core_mask = _poly_mask(S, core_pts)

    # 3. Warm glow radiating from the flame.
    glow = _tint(outer_mask.point(lambda v: int(v * 0.55)), (255, 170, 60, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(S // 28))
    icon = Image.alpha_composite(icon, _apply_mask(glow, mask))

    # 4. Outer flame: cream -> amber vertical gradient, clipped to the flame shape.
    outer_fill = _vertical_gradient(S, (255, 247, 237), (253, 230, 141)).convert("RGBA")
    outer_fill.putalpha(outer_mask)
    # tiny drop shadow to lift the flame off the gradient
    sh = _tint(outer_mask, (90, 15, 0, 110)).filter(ImageFilter.GaussianBlur(S // 90))
    icon = Image.alpha_composite(icon, _apply_mask(sh, mask))
    icon = Image.alpha_composite(icon, outer_fill)

    # 5. Inner core: brighter cream -> deep amber.
    core_fill = _vertical_gradient(S, (255, 237, 213), (245, 158, 11)).convert("RGBA")
    core_fill.putalpha(core_mask)
    icon = Image.alpha_composite(icon, core_fill)

    # 6. Subtle inner border for definition.
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


def _tint(alpha_src, color):
    """Make a solid `color` layer shaped by an L/alpha source."""
    if alpha_src.mode != "L":
        alpha_src = alpha_src.split()[3]
    solid = Image.new("RGBA", alpha_src.size, color)
    solid.putalpha(alpha_src)
    return solid


if __name__ == "__main__":
    build()
