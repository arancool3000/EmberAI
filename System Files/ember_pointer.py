"""Click-through on-screen pointer that shows where Ember is acting.

The operating system still owns one real input cursor.  This lightweight overlay gives
Ember a distinct visual pointer without stealing focus or intercepting the user's clicks.
``human_mouse`` can call :meth:`request` from an agent worker thread; the Qt signal safely
marshals the update onto the GUI thread.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget


class EmberPointerOverlay(QWidget):
    """A non-interactive branded pointer that briefly follows Ember's actions."""

    requested = pyqtSignal(int, int, str)
    _HOTSPOT = QPoint(14, 14)

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
        self.setFixedSize(104, 56)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._enabled = True
        self._click_flash = False
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.hide)
        self.requested.connect(self._apply_request)
        self.hide()

    def request(self, x: int, y: int, action: str = "move") -> None:
        """Thread-safe entry point used by the mouse driver."""
        self.requested.emit(int(x), int(y), str(action or "move"))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._idle.stop()
            self.hide()

    def _apply_request(self, x: int, y: int, action: str) -> None:
        if not self._enabled:
            return
        self.move(x - self._HOTSPOT.x(), y - self._HOTSPOT.y())
        self._click_flash = action in ("click", "double-click", "down", "up")
        self.update()
        if not self.isVisible():
            self.show()
            self.raise_()
        # Long enough to make the owner of the action obvious, short enough not to leave
        # a second pointer hanging around or obscuring Ember's next verification screenshot.
        self._idle.start(500 if self._click_flash else 700)
        if self._click_flash:
            QTimer.singleShot(190, self._clear_click_flash)

    def _clear_click_flash(self) -> None:
        self._click_flash = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._click_flash:
            p.setPen(QPen(QColor(255, 153, 64, 185), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._HOTSPOT, 11, 11)

        # A warm Ember-orange cursor with a dark edge so it remains readable on both
        # light and dark desktops.  The path's first point is the exact click hotspot.
        path = QPainterPath()
        path.moveTo(14, 14)
        path.lineTo(16, 40)
        path.lineTo(22, 33)
        path.lineTo(29, 46)
        path.lineTo(36, 42)
        path.lineTo(28, 30)
        path.lineTo(39, 28)
        path.closeSubpath()
        p.setPen(QPen(QColor(53, 30, 20, 235), 2))
        p.setBrush(QColor(255, 132, 54, 245))
        p.drawPath(path)

        # Compact owner badge: useful when the system cursor is also visible nearby.
        badge = QRect(40, 10, 58, 25)
        p.setPen(QPen(QColor(255, 183, 105, 220), 1))
        p.setBrush(QColor(31, 24, 25, 230))
        p.drawRoundedRect(badge, 9, 9)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(255, 190, 119))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, "EMBER")
