"""Siri-style "Golden Gate" glow — a flowing, breathing band of light that sweeps
around Ember's window edge while it's listening, thinking, or speaking.

A frameless, click-through QWidget overlay sized to cover the window. Its paintEvent
strokes a rounded-rect just inside the edges with a conical gradient (a warm-leaning
rainbow: gold → orange → red → magenta → violet → blue → teal) whose angle rotates
over time, drawn in several soft layers so it blooms like a glow. Overall intensity
breathes with a sine, and each state (listen / think / speak) tunes the speed and
brightness. A ~60fps QTimer drives it; everything is wrapped so a paint hiccup can
never take down the app.
"""
from __future__ import annotations

import math
import sys

from PyQt6.QtCore import Qt, QRectF, QTimer, QPointF
from PyQt6.QtGui import (QColor, QPainter, QPen, QBrush, QConicalGradient, QPainterPath,
                         QRadialGradient, QLinearGradient)
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout


# Vibrant spectral flow in the spirit of Apple's 2019 "wonderful things" / "By
# innovation only" event — saturated, liquid, alive — but kept ember-warm at the
# seam (gold first AND last) so the conical loop is seamless.
_PALETTE_HEX = ["#ffd23f", "#ff8c1a", "#ff5e3a", "#ff2d55", "#ff375f",
                "#bf5af2", "#5e5ce6", "#0a84ff", "#32d0c6", "#ffd23f"]

# Per-state tuning: (rotation speed, breath speed, min alpha, max alpha, stroke px)
# Snappier + brighter than before so the band reads as energetic, not sleepy.
_STATES = {
    "listening": (0.022, 3.6, 0.50, 1.00, 3.4),
    "thinking":  (0.010, 1.8, 0.34, 0.70, 2.8),
    "speaking":  (0.016, 5.4, 0.52, 1.00, 3.8),
}

# How fast the whole glow fades in / out (per ~60fps frame). ~0.09 -> ≈190ms.
_FADE_STEP = 0.09


class SiriGlow(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._palette = [QColor(h) for h in _PALETTE_HEX]
        self._phase = 0.0          # rotation phase (0..1)
        self._t = 0.0              # time accumulator for breathing
        self._active = False
        self._intensity = 0.0      # current global opacity envelope (0..1) — drives the fade
        self._target = 0.0         # where the envelope is heading (1 = on, 0 = fading out)
        self._corner = 22
        self._inset = 5
        self._layers = 5
        self._state = "listening"
        self._level = 0.0          # smoothed audio-reactive amplitude (0..1)
        self._level_provider = None  # optional () -> 0..1|None pulled each frame (live mic)
        self._speed, self._breath, self._min_a, self._max_a, self._stroke = _STATES["listening"]
        self._timer = QTimer(self)
        self._timer.setInterval(16)   # ~60fps
        self._timer.timeout.connect(self._tick)

    # -- lifecycle -------------------------------------------------------------
    def cover(self):
        """Resize to fully cover the parent and sit on top."""
        p = self.parent()
        if p is not None:
            self.setGeometry(0, 0, p.width(), p.height())
        self.raise_()

    def set_state(self, state: str):
        cfg = _STATES.get(state)
        if cfg:
            self._state = state
            self._speed, self._breath, self._min_a, self._max_a, self._stroke = cfg

    def set_level_provider(self, fn):
        """Install a callable () -> 0..1 (or None) sampled every frame so the band swells
        and brightens with live sound (the user's voice while listening)."""
        self._level_provider = fn

    def set_level(self, v):
        """Push an audio level (0..1) directly (alternative to a provider)."""
        try:
            self._level = max(0.0, min(1.0, float(v)))
        except Exception:
            pass

    def _update_level(self):
        """Pull the live level (or synthesise a speech-like one while speaking) and ease
        toward it — snappy on the way up, smooth on the way down."""
        target = None
        if self._level_provider is not None:
            try:
                target = self._level_provider()
            except Exception:
                target = None
        if target is None and self._state == "speaking":
            # No live mic while Ember talks -> a syllable-cadence envelope so the band
            # still visibly moves "with its voice".
            env = (0.45 + 0.30 * math.sin(self._t * 7.1)
                   + 0.18 * math.sin(self._t * 11.7 + 1.3)
                   + 0.07 * math.sin(self._t * 19.3))
            target = max(0.0, min(1.0, env))
        if target is None:
            target = 0.0
        target = max(0.0, min(1.0, float(target)))
        if target > self._level:
            self._level += (target - self._level) * 0.55
        else:
            self._level += (target - self._level) * 0.16

    def start(self, state: str = "listening"):
        self.set_state(state)
        self.cover()
        self._active = True
        self._target = 1.0          # fade up to full
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        # Don't pop off — fade out, then hide when the envelope reaches zero (in _tick).
        self._target = 0.0

    def _tick(self):
        # Ease the opacity envelope toward its target so the glow fades in/out.
        if self._intensity < self._target:
            self._intensity = min(self._target, self._intensity + _FADE_STEP)
        elif self._intensity > self._target:
            self._intensity = max(self._target, self._intensity - _FADE_STEP)
        # Fully faded out -> stop animating and hide to save CPU.
        if self._target == 0.0 and self._intensity <= 0.0:
            self._active = False
            if self._timer.isActive():
                self._timer.stop()
            self.hide()
            return
        self._t += 0.016
        self._update_level()
        # Louder sound -> the band spins a touch faster, so it reads as energetic.
        self._phase = (self._phase + self._speed * (1.0 + 0.8 * self._level)) % 1.0
        try:
            self.update()
        except Exception:
            pass

    # -- painting --------------------------------------------------------------
    def _ring_gradient(self, cx, cy, angle_deg, alpha):
        g = QConicalGradient(cx, cy, angle_deg % 360)
        n = len(self._palette)
        a = int(max(0, min(255, alpha)))
        for i, base in enumerate(self._palette):
            c = QColor(base)
            c.setAlpha(a)
            g.setColorAt(i / (n - 1) if n > 1 else 0.0, c)
        return g

    def paintEvent(self, _ev):
        if not self._active or self._intensity <= 0.0:
            return
        try:
            w, h = self.width(), self.height()
            if w < 24 or h < 24:
                return
            cx, cy = w / 2.0, h / 2.0
            angle = self._phase * 360.0
            # Audio level deepens the breath so the band visibly pulses with the voice.
            breathe = 0.5 + 0.5 * math.sin(self._t * self._breath)
            breathe = min(1.0, breathe * (1.0 + 0.45 * self._level) + 0.30 * self._level)
            # The global intensity envelope scales everything so the band fades in/out.
            base_alpha = (self._min_a + (self._max_a - self._min_a) * breathe) * 255.0 * self._intensity
            if base_alpha < 1.0:
                return
            # Louder -> a thicker, brighter band.
            stroke = self._stroke * (1.0 + 0.7 * self._level)
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            inset = self._inset
            rect = QRectF(inset, inset, w - 2 * inset, h - 2 * inset)
            path = QPainterPath()
            path.addRoundedRect(rect, self._corner, self._corner)
            # Widest + faintest layer first, brightest thin core last -> soft bloom.
            for layer in range(self._layers, 0, -1):
                width = stroke * (0.7 + layer)
                alpha = base_alpha * (0.85 / layer)
                pen = QPen(QBrush(self._ring_gradient(cx, cy, angle + layer * 6, alpha)), width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            p.end()
        except Exception:
            pass


class ThinkingDots(QWidget):
    """A little cluster of glowing dots that pulse and gently rearrange (drift past each
    other) while Ember is thinking — an iOS-style 'alive' indicator. Self-animating at
    ~60fps; call start()/stop()."""

    def __init__(self, parent: QWidget = None, n: int = 4, dot: int = 7, gap: int = 15):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._n = n
        self._dot = dot          # base radius
        self._gap = gap          # spacing between dot centers
        self._t = 0.0
        self._amp = gap * 0.55   # how far dots drift (enough to cross / rearrange)
        self._palette = [QColor(h) for h in ("#ffd23f", "#ff5e3a", "#ff2d55", "#bf5af2")]
        pad = 14
        self.setFixedSize(int((n - 1) * gap + 2 * dot + 2 * self._amp + pad),
                          int(2 * dot + pad))
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._t += 0.045
        try:
            self.update()
        except Exception:
            pass

    def paintEvent(self, _ev):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            cy = self.height() / 2.0
            left = self._dot + self._amp + 4
            for i in range(self._n):
                base_x = left + i * self._gap
                # Drift left/right out of phase so neighbours slide past each other.
                x = base_x + self._amp * math.sin(self._t * 1.6 + i * 1.5)
                # Staggered size + brightness pulse — a travelling wave across the dots.
                pulse = 0.5 + 0.5 * math.sin(self._t * 3.2 - i * 0.9)
                r = self._dot * (0.62 + 0.5 * pulse)
                col = self._palette[i % len(self._palette)]
                # Glow: a few concentric fills with falloff alpha.
                for ring in (2.4, 1.6, 1.0):
                    c = QColor(col)
                    c.setAlpha(int((40 if ring > 1.5 else 235) * (0.45 + 0.55 * pulse)))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QBrush(c))
                    rr = r * ring
                    p.drawEllipse(QRectF(x - rr, cy - rr, 2 * rr, 2 * rr))
            p.end()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Floating Siri-style orb (a small, FOCUS-PRESERVING listener that appears on
# "Hey Ember" instead of yanking the whole window forward over your current app).
# ---------------------------------------------------------------------------

class _Orb(QWidget):
    """The animated sphere itself: a dark glossy ball with a flowing iridescent light
    streak (the macOS-15 'Siri' look), breathing with the active state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(150, 150)
        self._t = 0.0
        # Tasteful iridescent palette (Apple-Intelligence-like: blue→indigo→purple→pink→
        # rose→teal). No fiery yellow/orange — keeps it premium, not a clown rainbow.
        self._palette = [QColor(h) for h in ("#0a84ff", "#5e5ce6", "#bf5af2", "#ff2d55",
                                             "#ff6ac1", "#36d2c3")]
        self._speed, self._breath = 0.020, 3.2     # tuned per state
        self._state = "listening"
        self._level = 0.0          # smoothed audio-reactive amplitude (0..1)
        self._level_provider = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state):
        self._state = state
        self._speed, self._breath = {
            "listening": (0.024, 3.6), "thinking": (0.012, 1.8), "speaking": (0.020, 5.2),
        }.get(state, (0.020, 3.2))

    def set_level_provider(self, fn):
        self._level_provider = fn

    def set_level(self, v):
        try:
            self._level = max(0.0, min(1.0, float(v)))
        except Exception:
            pass

    def _update_level(self):
        target = None
        if self._level_provider is not None:
            try:
                target = self._level_provider()
            except Exception:
                target = None
        if target is None and self._state == "speaking":
            env = (0.45 + 0.30 * math.sin(self._t * 7.1)
                   + 0.18 * math.sin(self._t * 11.7 + 1.3)
                   + 0.07 * math.sin(self._t * 19.3))
            target = max(0.0, min(1.0, env))
        if target is None:
            target = 0.0
        target = max(0.0, min(1.0, float(target)))
        if target > self._level:
            self._level += (target - self._level) * 0.55
        else:
            self._level += (target - self._level) * 0.16

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def _tick(self):
        self._t += 0.016
        self._update_level()
        try:
            self.update()
        except Exception:
            pass

    def paintEvent(self, _ev):
        try:
            w, h = self.width(), self.height()
            lvl = self._level
            cx, cy = w / 2.0, h / 2.0
            base = min(w, h) / 2.0 - 6
            pal = self._palette
            n = len(pal)
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            breathe = 0.5 + 0.5 * math.sin(self._t * self._breath)

            def col(i, a):
                c = QColor(pal[int(i) % n])
                c.setAlpha(max(0, min(255, int(a))))
                return c

            drift = self._t * 0.18             # slow colour rotation through the palette
            # CONSTANT sizes — the orb never grows/shrinks (no "bouncing"); all life comes
            # from flowing light, a rotating signature arc, and brightness that tracks the voice.
            R = base * 0.62
            glow_r = base * 1.0
            circle = QRectF(cx - R, cy - R, 2 * R, 2 * R)

            # ---- 1. Outer glow bloom (brightness pulses; size fixed) ----
            halo = QRadialGradient(QPointF(cx, cy), glow_r)
            a_edge = min(210.0, 90 + 80 * breathe + 70 * lvl)
            halo.setColorAt(0.0, col(drift, a_edge * 0.30))
            halo.setColorAt(max(0.05, (R / glow_r) * 0.78), col(drift, a_edge))
            halo.setColorAt(0.93, col(drift + 2, 30))
            halo.setColorAt(1.0, col(drift + 2, 0))
            p.setBrush(QBrush(halo))
            p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, 2 * glow_r, 2 * glow_r))

            # ---- 2. Luminous liquid-light core ----
            p.setClipPath(self._circle_path(cx, cy, R))
            depth = QRadialGradient(QPointF(cx, cy), R)
            depth.setColorAt(0.0, QColor(44, 34, 82))
            depth.setColorAt(1.0, QColor(10, 10, 20))
            p.setBrush(QBrush(depth))
            p.drawEllipse(circle)
            ang = (self._t * (30.0 + 50.0 * lvl)) % 360.0    # iridescence rotates, no resize
            swirl = QConicalGradient(cx, cy, ang)
            for i in range(n + 1):
                swirl.setColorAt(min(1.0, i / n), col(drift + i, 110 + 70 * breathe + 40 * lvl))
            p.setBrush(QBrush(swirl))
            p.drawEllipse(circle)
            # soft blobs drift in POSITION only (constant size) -> liquid motion, never bouncing
            for j in range(3):
                ph = self._t * (0.5 + 0.2 * j) + j * 2.1
                bx = cx + math.cos(ph) * R * 0.40
                by = cy + math.sin(ph * 1.27) * R * 0.40
                blob = QRadialGradient(QPointF(bx, by), R * 0.62)
                blob.setColorAt(0.0, col(drift + j * 2 + 1, 140 + 55 * lvl))
                blob.setColorAt(1.0, col(drift + j * 2 + 1, 0))
                p.setBrush(QBrush(blob))
                p.drawEllipse(circle)
            hx = cx + math.cos(self._t * 0.8) * R * 0.14
            hy = cy + math.sin(self._t * 1.0) * R * 0.14
            bloom = QRadialGradient(QPointF(hx, hy), R * 0.9)
            bloom.setColorAt(0.0, QColor(255, 255, 255, int(min(235, 130 + 70 * breathe + 55 * lvl))))
            bloom.setColorAt(0.45, QColor(255, 255, 255, 34))
            bloom.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(bloom))
            p.drawEllipse(circle)
            p.setClipping(False)

            # ---- 3. Signature: a bright light-arc that orbits the rim (its unique identity) ----
            arc_r = R * 0.93
            arc_rect = QRectF(cx - arc_r, cy - arc_r, 2 * arc_r, 2 * arc_r)
            arc_col = QColor(pal[int(drift + 1) % n])
            arc_col.setAlpha(int(min(255, 165 + 80 * lvl)))
            apen = QPen(arc_col, 2.6)
            apen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(apen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            start = (-(self._t * 120.0)) % 360.0
            p.drawArc(arc_rect, int(start * 16), int(72 * 16))   # a 72° comet sweeping round

            # ---- 4. Glass sheen + crisp rim ----
            hi = QRadialGradient(QPointF(cx - R * 0.34, cy - R * 0.42), R * 0.8)
            hi.setColorAt(0.0, QColor(255, 255, 255, 140))
            hi.setColorAt(0.5, QColor(255, 255, 255, 26))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(hi))
            p.drawEllipse(circle)
            p.setPen(QPen(QColor(255, 255, 255, int(50 + 50 * breathe)), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle)
            p.end()
        except Exception:
            pass

    def _circle_path(self, cx, cy, r):
        path = QPainterPath()
        path.addEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        return path


class SiriOrb(QWidget):
    """A floating, FOCUS-PRESERVING Siri orb window. It appears over whatever app you're
    using (it never steals focus or switches apps), shows the orb + a caption, and is
    click-through. Driven by the host: popup(state) / set_caption(text) / dismiss()."""

    def __init__(self):
        super().__init__(None)
        # NB: deliberately NOT Qt.Tool — on macOS a Tool window auto-hides whenever the app
        # isn't frontmost, which is exactly when the orb needs to be visible (you're in
        # another app). Frameless + stays-on-top + does-not-accept-focus keeps it floating
        # over other apps without stealing focus.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # keep current app focused
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)
        self._orb = _Orb(self)
        lay.addWidget(self._orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self._caption = QLabel("")
        self._caption.setWordWrap(True)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._caption.setFixedWidth(300)
        self._caption.setStyleSheet(
            "color:#f2f3f8; font:14px -apple-system,'Segoe UI',sans-serif;"
            "background:rgba(18,19,26,205); border:1px solid rgba(255,255,255,28);"
            "border-radius:14px; padding:9px 13px;")
        self._caption.setVisible(False)
        lay.addWidget(self._caption, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setFixedWidth(332)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

    def popup(self, state: str = "listening"):
        self._dismiss_timer.stop()
        self._orb.set_state(state)
        self._orb.start()
        self._reposition()
        self.show()
        self.raise_()          # on top, but WA_ShowWithoutActivating means focus stays put
        self._elevate()        # float over ALL apps / spaces / fullscreen (macOS)

    def _elevate(self):
        """macOS: raise the orb above every app and make it visible on all Spaces + over
        fullscreen apps, so it's a true system-wide overlay (best-effort)."""
        if sys.platform != "darwin":
            return
        try:
            import objc
            from ctypes import c_void_p
            win = objc.objc_object(c_void_p=int(self.winId())).window()
            if win is None:
                return
            win.setLevel_(25)   # NSStatusWindowLevel — above normal app windows
            # canJoinAllSpaces | stationary | fullScreenAuxiliary
            win.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))
        except Exception:
            pass

    def set_state(self, state: str):
        self._orb.set_state(state)

    def set_level_provider(self, fn):
        """Feed the orb a live audio level so it pulses with the user's voice."""
        self._orb.set_level_provider(fn)

    def set_level(self, v):
        self._orb.set_level(v)

    def set_caption(self, text: str):
        text = (text or "").strip()
        if not text:
            self._caption.setVisible(False)
        else:
            self._caption.setText(text[:400])
            self._caption.setVisible(True)
        self._reposition()

    def dismiss_after(self, ms: int):
        self._dismiss_timer.start(max(500, int(ms)))

    def dismiss(self):
        self._orb.stop()
        self.hide()
        self._caption.setVisible(False)

    def _reposition(self):
        """Sit near the bottom-centre of the primary screen (like the macOS Siri orb).

        The window is anchored by a FIXED top Y, not by total height — so when the caption
        grows/shrinks the window expands DOWNWARD and the orb itself stays put instead of
        jumping around (that vertical re-centring was the 'bouncing' you saw)."""
        try:
            from PyQt6.QtWidgets import QApplication
            self.adjustSize()
            scr = QApplication.primaryScreen().availableGeometry()
            x = scr.x() + (scr.width() - self.width()) // 2
            y = scr.y() + scr.height() - 330   # fixed anchor; reserves room for the caption
            self.move(x, y)
        except Exception:
            pass
