"""Ember's visual effects engine — fluid gradients, real fire, and live-typing text.

Three things live here because they share one idea: motion that comes from a *field*
rather than from keyframes. Nothing here loads an asset; every frame is computed, so it
scales to any size and never looks like a stretched PNG.

``FlowField``
    Cheap value-noise with smooth interpolation, sampled over time. This is what makes
    Ember's pointer look like liquid metal instead of a spinning rainbow, and what gives
    the fire its wind.

``FireBuffer``
    A real flame simulation — heat propagates upward from a hot source row, decaying and
    drifting sideways, exactly the way the classic Doom fire effect works. On a small grid
    scaled up with smoothing this reads as actual fire, not a flame-shaped icon.

``StreamingText``
    Text that fades in from the point it is being typed, one arriving chunk at a time,
    instead of snapping into place. Characters land at full brightness within a couple of
    hundred milliseconds, so it feels live rather than laggy.

The simulation and timing maths are pure and import-light so they are unit-tested without
a display (``test_ember_fx.py``); the Qt widgets at the bottom import PyQt6 lazily.
"""
from __future__ import annotations

import colorsys
import math
import random
import time
from typing import Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

#: Ember's signature iridescent ramp — violet through magenta to warm gold. Used by the
#: pointer and the themed chrome so the app reads as one object.
IRIDESCENT = ("#7b2ff7", "#b34cff", "#ff4fd8", "#ff5f6d", "#ffa14a", "#ffd86f")

#: The cooler companion ramp, for surfaces that must not shout (settings, panels).
IRIDESCENT_COOL = ("#3a1c71", "#6a3de8", "#b04ce6", "#ff5f9e", "#ff9a5a", "#ffd66b")


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def smoothstep(t: float) -> float:
    """Cubic ease with zero derivative at both ends, clamped to [0, 1]."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3.0 - 2.0 * t)


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    v = str(value).lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def mix_rgb(c1: Sequence[int], c2: Sequence[int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


def ramp_at(palette: Sequence[str], pos: float) -> Tuple[int, int, int]:
    """Sample a colour ramp at ``pos``, wrapping so the ramp is seamless when animated."""
    cols = [hex_to_rgb(c) for c in palette]
    n = len(cols)
    if n == 1:
        return cols[0]
    p = (float(pos) % 1.0) * n
    i = int(p) % n
    return mix_rgb(cols[i], cols[(i + 1) % n], p - int(p))


def flowing_stops(palette: Sequence[str], phase: float,
                  stops: int = 12) -> List[Tuple[float, Tuple[int, int, int]]]:
    """Gradient stops for a ramp scrolled by ``phase``.

    The first and last stop resolve to the same colour, so a gradient built from these can
    be animated forever without a visible seam where the ramp wraps.
    """
    stops = max(2, int(stops))
    out = []
    for i in range(stops):
        t = i / (stops - 1)
        out.append((t, ramp_at(palette, phase + t)))
    return out


# ---------------------------------------------------------------------------
# Flow field
# ---------------------------------------------------------------------------

class FlowField:
    """Smoothly interpolated value noise — the 'liquid' in Ember's liquid look.

    A real Perlin implementation would be overkill: what the pointer and the orb need is a
    field that is continuous in x, y, and t, and cheap enough to sample a few hundred times
    per frame at 60fps. Bilinear-interpolated lattice noise with a smoothstep fade gives
    exactly that.
    """

    def __init__(self, size: int = 32, seed: int = 7):
        self.size = max(4, int(size))
        rng = random.Random(seed)
        n = self.size
        # Two lattices, cross-faded over time, so the field evolves instead of merely
        # scrolling — a scrolling field reads as a moving texture, not as flow.
        self._a = [[rng.random() for _ in range(n)] for _ in range(n)]
        self._b = [[rng.random() for _ in range(n)] for _ in range(n)]

    def _sample(self, grid, x: float, y: float) -> float:
        n = self.size
        x0, y0 = int(math.floor(x)) % n, int(math.floor(y)) % n
        x1, y1 = (x0 + 1) % n, (y0 + 1) % n
        fx, fy = smoothstep(x - math.floor(x)), smoothstep(y - math.floor(y))
        top = lerp(grid[y0][x0], grid[y0][x1], fx)
        bot = lerp(grid[y1][x0], grid[y1][x1], fx)
        return lerp(top, bot, fy)

    def at(self, x: float, y: float, t: float = 0.0) -> float:
        """Field value in [0, 1] at position (x, y) and time t."""
        blend = 0.5 + 0.5 * math.sin(t * 0.6)
        return lerp(self._sample(self._a, x, y), self._sample(self._b, x, y), blend)

    def angle(self, x: float, y: float, t: float = 0.0) -> float:
        """Field value expressed as a direction in radians — used to steer flow."""
        return self.at(x, y, t) * math.tau


# ---------------------------------------------------------------------------
# Fire
# ---------------------------------------------------------------------------

#: Black → deep red → orange → yellow → white-hot. Indexed by heat 0..HEAT_MAX.
FIRE_PALETTE = ("#000000", "#1f0700", "#3f0f00", "#5f1700", "#7f2600", "#9f3300",
                "#bf4300", "#d75400", "#e76b00", "#ef8200", "#f79b00", "#ffb414",
                "#ffc93c", "#ffdc66", "#ffee9a", "#fffbe0")
HEAT_MAX = len(FIRE_PALETTE) - 1


class FireBuffer:
    """A small heat grid that behaves like fire.

    Every step, each cell takes the heat of the cell below it minus a random decay, and
    drifts sideways by a random amount plus the wind. Heat is injected along the bottom
    row. Run at 96x64 and scaled up smoothly this is indistinguishable from real flames
    and costs a fraction of a millisecond per frame.
    """

    def __init__(self, width: int = 96, height: int = 64, seed: int = 11):
        self.w, self.h = max(8, int(width)), max(8, int(height))
        self._rng = random.Random(seed)
        self.cells = [0] * (self.w * self.h)
        self.wind = 0.0
        self.intensity = 1.0
        self.ignite()

    def ignite(self) -> None:
        """Light the source row along the bottom."""
        base = (self.h - 1) * self.w
        for x in range(self.w):
            self.cells[base + x] = HEAT_MAX

    def douse(self) -> None:
        base = (self.h - 1) * self.w
        for x in range(self.w):
            self.cells[base + x] = 0

    def step(self) -> None:
        """Advance one frame: propagate heat upward with decay and lateral drift."""
        w, h, cells, rnd = self.w, self.h, self.cells, self._rng.random
        # Slow, wandering wind so the flames lean and recover instead of shimmering in place.
        self.wind = max(-1.5, min(1.5, self.wind + (rnd() - 0.5) * 0.25))
        wind = self.wind
        decay_bias = 1.6 / max(0.15, float(self.intensity))
        for y in range(h - 1, 0, -1):
            row_below = y * w
            row_here = row_below - w
            for x in range(w):
                heat = cells[row_below + x]
                if heat <= 0:
                    cells[row_here + x] = 0
                    continue
                decay = int(rnd() * decay_bias)
                drift = int(round((rnd() - 0.5) * 3.0 + wind))
                dst = x + drift
                if dst < 0 or dst >= w:
                    dst = x                      # reflect at the edges rather than wrap,
                                                 # so flames don't tunnel across the frame
                cells[row_here + dst] = max(0, heat - decay)

    def heat_at(self, x: int, y: int) -> int:
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.cells[y * self.w + x]
        return 0

    def set_intensity(self, value: float) -> None:
        """0 = embers, 1 = a normal fire, >1 = a roaring one."""
        self.intensity = max(0.05, min(3.0, float(value)))


# ---------------------------------------------------------------------------
# Live-typing text
# ---------------------------------------------------------------------------

class StreamingText:
    """Fades arriving text in from the point it is being typed.

    The model delivers chunks; each chunk is stamped on arrival and rendered at an opacity
    that ramps from 0 to 1 over :attr:`fade` seconds. Text that has finished fading is
    merged into a single settled run so the markup stays small no matter how long the
    reply gets — a thousand-chunk answer must not become a thousand spans.
    """

    def __init__(self, fade: float = 0.28, now: Optional[float] = None):
        self.fade = max(0.01, float(fade))
        self._settled = ""
        self._pending: List[Tuple[str, float]] = []
        self._now = now if now is not None else time.monotonic()

    # -- input ------------------------------------------------------------
    def append(self, chunk: str, now: Optional[float] = None) -> None:
        if not chunk:
            return
        self._pending.append((str(chunk), self._clock(now)))

    def finish(self, now: Optional[float] = None) -> None:
        """Settle everything immediately (stream ended, or the user scrolled away)."""
        self._settled += "".join(c for c, _ in self._pending)
        self._pending.clear()

    def reset(self) -> None:
        self._settled = ""
        self._pending.clear()

    def _clock(self, now: Optional[float]) -> float:
        if now is not None:
            self._now = float(now)
        return self._now

    # -- state ------------------------------------------------------------
    @property
    def text(self) -> str:
        """The full text, regardless of what has finished fading."""
        return self._settled + "".join(c for c, _ in self._pending)

    def active(self, now: Optional[float] = None) -> bool:
        """True while any chunk is still fading (i.e. a repaint is still needed)."""
        return bool(self._pending) and not self._all_done(self._clock(now))

    def _all_done(self, now: float) -> bool:
        return all(now - ts >= self.fade for _, ts in self._pending)

    def opacities(self, now: Optional[float] = None) -> List[Tuple[str, float]]:
        """``(chunk, opacity)`` for each pending chunk, oldest first."""
        t = self._clock(now)
        return [(c, smoothstep((t - ts) / self.fade)) for c, ts in self._pending]

    def compact(self, now: Optional[float] = None) -> None:
        """Merge fully-faded chunks into the settled run."""
        t = self._clock(now)
        keep = []
        for chunk, ts in self._pending:
            if t - ts >= self.fade:
                self._settled += chunk
            else:
                keep.append((chunk, ts))
        self._pending = keep

    # -- output -----------------------------------------------------------
    def render_html(self, now: Optional[float] = None, *,
                    colour: str = "#e8e8ef", escape=None) -> str:
        """HTML where still-arriving text is dimmed toward transparent.

        Only the tail is wrapped in spans; everything settled is emitted as plain text, so
        the document a long reply produces stays flat.
        """
        self.compact(now)
        esc = escape or (lambda s: s)
        out = [esc(self._settled)]
        for chunk, op in self.opacities(now):
            # rgba on the text colour rather than CSS opacity: Qt's rich text engine
            # honours the former on inline spans and ignores the latter.
            r, g, b = hex_to_rgb(colour)
            out.append(f'<span style="color:rgba({r},{g},{b},{op:.3f})">{esc(chunk)}</span>')
        return "".join(out)


# ---------------------------------------------------------------------------
# Qt widgets (imported lazily; everything above works without a display)
# ---------------------------------------------------------------------------

def _qt():
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import QWidget
    return Qt, QTimer, QColor, QImage, QPainter, QPixmap, QWidget


class FlameBackground:
    """Animated fire, painted behind any widget.

    Built as a mixin-free helper rather than a QWidget subclass so it can back a dialog, a
    panel, or the whole window without forcing a particular widget hierarchy. Call
    :meth:`paint` from the host's ``paintEvent``.
    """

    def __init__(self, width: int = 96, height: int = 64, *, intensity: float = 1.0,
                 seed: int = 11):
        self.fire = FireBuffer(width, height, seed=seed)
        self.fire.set_intensity(intensity)
        self._img = None

    def step(self) -> None:
        self.fire.step()

    def set_intensity(self, value: float) -> None:
        self.fire.set_intensity(value)

    def _image(self):
        from PyQt6.QtGui import QImage
        w, h = self.fire.w, self.fire.h
        if self._img is None:
            self._img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img = self._img
        pal = [hex_to_rgb(c) for c in FIRE_PALETTE]
        cells = self.fire.cells
        for y in range(h):
            row = y * w
            for x in range(w):
                heat = cells[row + x]
                r, g, b = pal[heat]
                # Cool cells fade out rather than painting black, so the fire can sit over
                # whatever the surface behind it is instead of punching a hole in it.
                a = 0 if heat == 0 else min(255, 40 + heat * 15)
                img.setPixel(x, y, (a << 24) | (r << 16) | (g << 8) | b)
        return img

    def paint(self, painter, rect) -> None:
        """Draw the current frame stretched across ``rect`` with smooth upscaling."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPainter, QPixmap
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        pix = QPixmap.fromImage(self._image()).scaled(
            rect.width(), rect.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap(rect, pix)
        painter.restore()
