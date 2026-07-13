"""Click-through on-screen pointer that shows where Ember is acting.

The operating system still owns one real input cursor.  This lightweight overlay gives
Ember a distinct visual pointer without stealing focus or intercepting the user's clicks.
``human_mouse`` can call :meth:`request` from an agent worker thread; the Qt signal safely
marshals the update onto the GUI thread.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class EmberPointerOverlay(QWidget):
    """A non-interactive branded pointer that briefly follows Ember's actions."""

    requested = pyqtSignal(int, int, str)
    _HOTSPOT = QPoint(24, 24)

    def __init__(self):
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        # Available on the supported Qt builds.  Keep the attribute fallback below too,
        # because some window managers ignore one but honour the other.
        try:
            flags |= Qt.WindowType.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setObjectName("emberPointerOverlay")
        self.setFixedSize(48, 48)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._enabled = True
        self._click_flash = False
        self._phase = 0.0
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._hide_pointer)
        self._spin = QTimer(self)
        self._spin.setInterval(45)
        self._spin.timeout.connect(self._animate)
        self.requested.connect(self._apply_request)
        self.hide()

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
        self._click_flash = action in ("click", "double-click", "down", "up")
        self.update()
        if not self.isVisible():
            self.show()
            self.raise_()
            self._spin.start()
        # Long enough to make the owner of the action obvious, short enough not to leave
        # a second pointer hanging around or obscuring Ember's next verification screenshot.
        self._idle.start(500 if self._click_flash else 700)
        if self._click_flash:
            QTimer.singleShot(190, self._clear_click_flash)

    def _clear_click_flash(self) -> None:
        self._click_flash = False
        self.update()

    def _hide_pointer(self) -> None:
        self._spin.stop()
        self.hide()

    def _animate(self) -> None:
        self._phase = (self._phase + 5.0) % 360.0
        self.update()

    @staticmethod
    def _star_path() -> QPainterPath:
        """Compact four-point star centred on the exact action hotspot."""
        p = QPainterPath(QPointF(24, 4))
        p.lineTo(28.5, 19.5)
        p.lineTo(44, 24)
        p.lineTo(28.5, 28.5)
        p.lineTo(24, 44)
        p.lineTo(19.5, 28.5)
        p.lineTo(4, 24)
        p.lineTo(19.5, 19.5)
        p.closeSubpath()
        return p

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._click_flash:
            p.setPen(QPen(QColor(255, 255, 255, 215), 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._HOTSPOT, 21, 21)

        path = self._star_path()
        # A soft dark halo keeps the rainbow readable over both pale and busy desktops.
        p.setPen(QPen(QColor(15, 11, 28, 80), 7, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        rainbow = QConicalGradient(QPointF(24, 24), self._phase)
        for pos, colour in (
            (0.00, "#ff3b63"), (0.16, "#ff9f1c"), (0.32, "#ffe66d"),
            (0.48, "#35e38b"), (0.64, "#26c6ff"), (0.80, "#7267ff"),
            (0.92, "#d94cff"), (1.00, "#ff3b63"),
        ):
            rainbow.setColorAt(pos, QColor(colour))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(QBrush(rainbow))
        p.drawPath(path)

        # Bright centre makes the click hotspot unambiguous without a long arrow tail.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(QPointF(self._HOTSPOT), 2.7, 2.7)
