"""Offscreen tests for the compact click-through agent cursor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

import ember_pointer


def _app():
    return QApplication.instance() or QApplication([])


def test_hotspot_lands_on_the_requested_coordinate():
    app = _app()
    pointer = ember_pointer.EmberPointerOverlay()
    try:
        pointer.request(200, 150, "click")
        app.processEvents()
        assert pointer.isVisible()
        assert pointer.x() + ember_pointer.HOTSPOT.x() == 200
        assert pointer.y() + ember_pointer.HOTSPOT.y() == 150
    finally:
        pointer.close()


def test_pointer_is_cursor_sized_and_can_be_disabled():
    app = _app()
    pointer = ember_pointer.EmberPointerOverlay()
    try:
        assert pointer.width() == pointer.height() == 48
        pointer.request(80, 80, "move")
        app.processEvents()
        pointer.set_enabled(False)
        assert not pointer.isVisible()
    finally:
        pointer.close()


def test_pointer_paints_every_action_state():
    app = _app()
    pointer = ember_pointer.EmberPointerOverlay()
    try:
        for action in ("move", "park", "click", "yield"):
            pointer.request(100, 100, action)
            app.processEvents()
            pointer.render(QPixmap(pointer.size()))
    finally:
        pointer.close()


def test_overlay_never_calls_raise_during_requests():
    # Raising a Qt tool window activated Ember on macOS, covering the intended target app.
    source = __import__("inspect").getsource(ember_pointer.EmberPointerOverlay._apply_request)
    assert "raise_(" not in source
