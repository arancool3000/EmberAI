"""A small click-through cursor for Ember's independent input channel.

The pointer is intentionally quieter than the old rainbow overlay: it is cursor-sized,
has one warm Ember accent, never takes focus, and only appears while the agent is acting.
``human_mouse`` may call :meth:`request` from a worker thread; the Qt signal marshals each
position to the UI thread.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

SIZE = 48
HOTSPOT = QPoint(11, 10)


class EmberPointerOverlay(QWidget):
    """Focusless transparent overlay whose hotspot is the synthetic click point."""

    requested = pyqtSignal(int, int, str)

    def __init__(self):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        try:
            flags |= Qt.WindowType.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setObjectName("emberPointerOverlay")
        self.setFixedSize(SIZE, SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        except AttributeError:
            pass
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._enabled = True
        self._action = "move"
        self._click_phase = 0.0
        self._pulse = 0.0
        self._yielding = False
        self._parked = False

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.hide)
        self._tick = QTimer(self)
        self._tick.setInterval(16)
        self._tick.timeout.connect(self._animate)
        self.requested.connect(self._apply_request)
        self.hide()

    @property
    def hotspot(self) -> QPoint:
        return QPoint(HOTSPOT)

    def request(self, x: int, y: int, action: str = "move") -> None:
        """Thread-safe position/action update used by the input driver."""
        self.requested.emit(int(x), int(y), str(action or "move"))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._idle.stop()
            self._tick.stop()
            self.hide()

    def _apply_request(self, x: int, y: int, action: str) -> None:
        if not self._enabled:
            return
        self._action = action
        self._parked = action == "park"
        self._yielding = action == "yield"
        if action not in ("yield",):
            self._yielding = False
        if action in ("click", "double-click", "down", "up"):
            self._click_phase = 1.0

        # The arrow tip—not the middle of the overlay—is the exact click coordinate.
        self.move(x - HOTSPOT.x(), y - HOTSPOT.y())
        if not self.isVisible():
            self.show()
        # Do not explicitly promote this window: on macOS that can activate its owning app,
        # putting Ember itself in front of the window we are trying to operate. The topmost
        # window flag already keeps this tiny overlay visible.
        if not self._tick.isActive():
            self._tick.start()
        self._idle.stop()
        if not self._parked:
            self._idle.start(1050 if self._click_phase else 700)
        self.update()

    def _animate(self) -> None:
        self._pulse = (self._pulse + 0.035) % 1.0
        if self._click_phase > 0:
            self._click_phase = max(0.0, self._click_phase - 0.065)
        if not self.isVisible():
            self._tick.stop()
        self.update()

    @staticmethod
    def _arrow_path() -> QPainterPath:
        """Compact cursor silhouette with the click hotspot at its tip."""
        path = QPainterPath(QPointF(11, 10))
        path.lineTo(11.2, 31.6)
        path.cubicTo(11.2, 33.0, 12.9, 33.6, 13.8, 32.5)
        path.lineTo(18.1, 27.1)
        path.lineTo(22.4, 36.5)
        path.lineTo(27.0, 34.4)
        path.lineTo(22.6, 25.1)
        path.lineTo(29.3, 24.0)
        path.cubicTo(30.8, 23.7, 31.3, 21.8, 30.0, 21.0)
        path.lineTo(13.7, 9.0)
        path.cubicTo(12.5, 8.1, 11.0, 8.8, 11.0, 10.0)
        path.closeSubpath()
        return path

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._click_phase > 0:
            progress = 1.0 - self._click_phase
            radius = 5.0 + 10.0 * progress
            alpha = int(150 * self._click_phase)
            painter.setPen(QPen(QColor(255, 151, 94, alpha), 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(HOTSPOT), radius, radius)

        path = self._arrow_path()
        painter.save()
        painter.translate(0.8, 1.7)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 5.2, join=Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(0, 0, 0, 90))
        painter.drawPath(path)
        painter.restore()

        fill = QLinearGradient(QPointF(10, 9), QPointF(28, 35))
        if self._yielding:
            fill.setColorAt(0.0, QColor("#9ca3af"))
            fill.setColorAt(1.0, QColor("#596174"))
        else:
            fill.setColorAt(0.0, QColor("#ffd09f"))
            fill.setColorAt(0.42, QColor("#ff915f"))
            fill.setColorAt(1.0, QColor("#f25074"))
        painter.setPen(
            QPen(
                QColor(255, 248, 239, 240),
                1.25,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(QBrush(fill))
        painter.drawPath(path)

        # A tiny living ember distinguishes it from the user's ordinary cursor without
        # turning it into an oversized mascot.
        centre = QPointF(31.5, 32.0)
        glow = QRadialGradient(centre, 8.5)
        glow.setColorAt(0.0, QColor(255, 232, 185, 210))
        glow.setColorAt(0.45, QColor(255, 128, 79, 100))
        glow.setColorAt(1.0, QColor(255, 91, 89, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(centre, 8.5, 8.5)
        painter.setBrush(QColor("#fff3dc") if not self._yielding else QColor("#c8ced8"))
        painter.drawEllipse(centre, 2.15, 2.15)

        painter.end()
