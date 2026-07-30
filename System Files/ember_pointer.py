"""Ember's own pointer — a fluid, iridescent cursor that is unmistakably not yours.

The operating system owns one real input cursor. This click-through overlay gives Ember a
second, visibly distinct one so you can always tell which pointer is acting.

The look is liquid rather than flat: the fill is a flowing iridescent ramp whose stops
scroll continuously and whose axis is steered by a :class:`~ember_fx.FlowField`, so the
colour bands bend and swim across the shape like oil on water instead of spinning like a
pinwheel. A soft contact shadow lifts it off the desktop, a thin light rim catches the
"edge" of the form, and an inner sheen gives it volume.

Two shapes, matching what the pointer is doing: the arrow while travelling, and a pointing
hand at the moment of a click — the same vocabulary every desktop already uses.

``human_mouse`` calls :meth:`request` from an agent worker thread; the Qt signal marshals
the update onto the GUI thread. The geometry is built from pure paths so it stays crisp at
any DPI, and the whole widget is transparent to input — it can never eat your clicks.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPainterPath,
                         QPen, QRadialGradient)
from PyQt6.QtWidgets import QWidget

from ember_fx import IRIDESCENT, FlowField, flowing_stops

#: Canvas size. Generous enough for the glow and shadow to fall outside the shape.
_SIZE = 72
#: The click hotspot — the arrow's tip — in canvas coordinates.
_HOTSPOT = QPoint(18, 12)


class EmberPointerOverlay(QWidget):
    """A non-interactive fluid pointer that follows Ember's actions."""

    requested = pyqtSignal(int, int, str)
    _HOTSPOT = _HOTSPOT

    def __init__(self):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        # Available on the supported Qt builds. Keep the attribute fallback below too,
        # because some window managers ignore one but honour the other.
        try:
            flags |= Qt.WindowType.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setObjectName("emberPointerOverlay")
        self.setFixedSize(_SIZE, _SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._enabled = True
        self._click_flash = 0.0        # 1.0 at the instant of a click, decaying to 0
        self._yielding = False         # the user grabbed the mouse; show Ember backing off
        self._phase = 0.0              # scrolls the colour ramp
        self._t = 0.0                  # feeds the flow field
        self._flow = FlowField(size=16, seed=23)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._hide_pointer)
        self._spin = QTimer(self)
        self._spin.setInterval(16)     # 60fps — the flow has to look continuous
        self._spin.timeout.connect(self._animate)
        self.requested.connect(self._apply_request)
        self.hide()

    # -- driver interface --------------------------------------------------
    def request(self, x: int, y: int, action: str = "move") -> None:
        """Thread-safe entry point used by the mouse driver."""
        self.requested.emit(int(x), int(y), str(action or "move"))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._idle.stop()
            self._hide_pointer()

    def _apply_request(self, x: int, y: int, action: str) -> None:
        if not self._enabled:
            return
        self.move(x - self._HOTSPOT.x(), y - self._HOTSPOT.y())
        if action in ("click", "double-click", "down", "up"):
            self._click_flash = 1.0
        self._yielding = (action == "yield")
        self.update()
        if not self.isVisible():
            self.show()
            self.raise_()
            self._spin.start()
        # Long enough to make the owner of the action obvious, short enough not to leave a
        # second pointer hanging around or obscuring Ember's next verification screenshot.
        #
        # A "park" is the exception. In detached mode a move doesn't touch the system
        # cursor, so this overlay is the ONLY evidence of where Ember is pointing — and at
        # 900ms it had always vanished by the time the agent took its verification
        # screenshot, which read as the Ember pointer being invisible. A parked pointer
        # stays until the next action moves it.
        if action == "park":
            self._idle.stop()
            return
        self._idle.start(420 if self._yielding else 700 if self._click_flash else 900)

    def _hide_pointer(self) -> None:
        self._spin.stop()
        self._click_flash = 0.0
        self._yielding = False
        self.hide()

    def _animate(self) -> None:
        self._t += 0.016
        self._phase = (self._phase + 0.006) % 1.0
        if self._click_flash > 0.0:
            # Fast decay: the ring should read as an impact, not a pulse.
            self._click_flash = max(0.0, self._click_flash - 0.055)
        self.update()

    # -- geometry ----------------------------------------------------------
    @staticmethod
    def _arrow_path() -> QPainterPath:
        """A classic pointer silhouette with softened corners.

        Drawn by hand rather than from a stock cursor bitmap so it stays sharp at any DPI
        and so the tip sits exactly on the hotspot.
        """
        p = QPainterPath(QPointF(18, 12))
        p.lineTo(18, 47)
        p.quadTo(18.4, 49.2, 20.3, 48.0)
        p.lineTo(27.2, 41.6)
        p.lineTo(32.6, 53.4)
        p.quadTo(33.7, 55.4, 35.7, 54.4)
        p.lineTo(39.6, 52.6)
        p.quadTo(41.5, 51.5, 40.6, 49.5)
        p.lineTo(35.3, 38.0)
        p.lineTo(44.0, 37.4)
        p.quadTo(46.4, 37.1, 45.0, 35.2)
        p.lineTo(20.6, 11.6)
        p.quadTo(19.0, 10.2, 18.0, 12.0)
        p.closeSubpath()
        return p

    @staticmethod
    def _hand_path() -> QPainterPath:
        """A pointing hand, used at the moment of a click.

        The parts are boolean-*united* rather than merely added to one path: overlapping
        subpaths keep their own outlines, so the rim-light stroke would trace every knuckle
        separately and the hand would read as a pile of blobs instead of one silhouette.

        Laid out so the fingertip is centred on the hotspot — the click lands at the tip of
        the finger, which is also where the impact ring is drawn.
        """
        hx = float(_HOTSPOT.x())
        finger_w = 8.6
        parts = [
            # Index finger, centred on the hotspot's x and starting at its y.
            (QRectF(hx - finger_w / 2.0, 11.0, finger_w, 23.5), 4.3),
            # Palm.
            (QRectF(hx - 6.0, 27.0, 21.5, 21.5), 6.8),
            # Folded fingers — bumps along the palm's top edge, stepping down and back.
            (QRectF(hx + 3.4, 24.2, 7.2, 12.0), 3.6),
            (QRectF(hx + 9.0, 26.4, 6.8, 10.0), 3.4),
        ]
        path = QPainterPath()
        for rect, radius in parts:
            piece = QPainterPath()
            piece.addRoundedRect(rect, radius, radius)
            path = piece if path.isEmpty() else path.united(piece)
        return path.simplified()

    # -- painting ----------------------------------------------------------
    def _fluid_brush(self, rect: QRectF) -> QBrush:
        """An iridescent ramp whose axis is steered by the flow field.

        Rotating the gradient axis — rather than merely scrolling a conical gradient —
        is what makes the colour bands appear to bend through the shape like a liquid.
        """
        ang = self._flow.angle(self._t * 0.35, self._t * 0.22, self._t) * 0.5
        r = max(rect.width(), rect.height())
        cx, cy = rect.center().x(), rect.center().y()
        g = QLinearGradient(cx - math.cos(ang) * r, cy - math.sin(ang) * r,
                            cx + math.cos(ang) * r, cy + math.sin(ang) * r)
        for pos, (cr, cg, cb) in flowing_stops(IRIDESCENT, self._phase, stops=14):
            g.setColorAt(pos, QColor(cr, cg, cb))
        return QBrush(g)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        clicking = self._click_flash > 0.0
        path = self._hand_path() if clicking else self._arrow_path()
        rect = path.boundingRect()

        # ---- impact ring: expands and fades out from the hotspot ----
        if clicking:
            k = 1.0 - self._click_flash              # 0 -> 1 over the decay
            radius = 8.0 + 20.0 * k
            p.setPen(QPen(QColor(255, 255, 255, int(200 * (1.0 - k))), 2.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(self._HOTSPOT), radius, radius)

        # ---- contact shadow: lifts the pointer off whatever is behind it ----
        glow = QRadialGradient(rect.center(), rect.width() * 0.95)
        glow.setColorAt(0.0, QColor(120, 60, 220, 40 if self._yielding else 90))
        glow.setColorAt(1.0, QColor(120, 60, 220, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(rect.adjusted(-8, -6, 8, 10))

        shadow = QPainterPath(path)
        shadow.translate(1.6, 2.4)
        p.setBrush(QColor(10, 6, 24, 110))
        p.drawPath(shadow)

        # While yielding, Ember steps back visually as well as functionally, so the
        # handover reads as deliberate rather than as the pointer glitching out.
        if self._yielding:
            p.setOpacity(0.45)

        # ---- the fluid body ----
        p.setBrush(self._fluid_brush(rect))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)

        # ---- volume: a bright sheen down one side, dark falloff on the other ----
        sheen = QLinearGradient(rect.topLeft(), rect.bottomRight())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 120))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.72, QColor(0, 0, 0, 0))
        sheen.setColorAt(1.00, QColor(20, 4, 40, 90))
        p.setBrush(QBrush(sheen))
        p.drawPath(path)

        # ---- rim light: a hairline that catches the edge of the form ----
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 205), 1.3, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

        # ---- the hotspot itself, so the exact action point is never ambiguous ----
        p.setOpacity(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 240))
        p.drawEllipse(QPointF(self._HOTSPOT), 2.1, 2.1)
