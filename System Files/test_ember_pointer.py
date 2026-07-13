"""Off-screen smoke tests for Ember's click-through pointer overlay."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ember_pointer import EmberPointerOverlay


def test_overlay_uses_exact_hotspot_and_can_be_disabled():
    app = QApplication.instance() or QApplication([])
    pointer = EmberPointerOverlay()
    try:
        pointer.request(200, 150, "click")
        app.processEvents()
        assert pointer.isVisible()
        assert (pointer.x(), pointer.y()) == (176, 126)
        assert pointer.width() == pointer.height() == 48

        pointer.set_enabled(False)
        app.processEvents()
        assert not pointer.isVisible()
    finally:
        pointer.close()
