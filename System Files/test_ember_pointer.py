"""Off-screen smoke tests for Ember's click-through pointer overlay."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import ember_pointer
from ember_pointer import EmberPointerOverlay


def _app():
    return QApplication.instance() or QApplication([])


def test_overlay_uses_exact_hotspot_and_can_be_disabled():
    app = _app()
    pointer = EmberPointerOverlay()
    try:
        pointer.request(200, 150, "click")
        app.processEvents()
        assert pointer.isVisible()
        # The widget is positioned so its hotspot — the arrow tip — lands on the target.
        hx, hy = pointer._HOTSPOT.x(), pointer._HOTSPOT.y()
        assert (pointer.x(), pointer.y()) == (200 - hx, 150 - hy)
        assert pointer.width() == pointer.height() == ember_pointer._SIZE

        pointer.set_enabled(False)
        app.processEvents()
        assert not pointer.isVisible()
    finally:
        pointer.close()


def test_hotspot_sits_inside_the_canvas():
    hx, hy = ember_pointer._HOTSPOT.x(), ember_pointer._HOTSPOT.y()
    assert 0 <= hx < ember_pointer._SIZE and 0 <= hy < ember_pointer._SIZE


def test_click_raises_then_decays_the_impact_flash():
    app = _app()
    pointer = EmberPointerOverlay()
    try:
        pointer.request(100, 100, "click")
        app.processEvents()
        assert pointer._click_flash > 0.0
        for _ in range(40):
            pointer._animate()
        assert pointer._click_flash == 0.0      # the ring is an impact, not a loop
    finally:
        pointer.close()


def test_yield_state_dims_the_pointer_and_clears_on_the_next_move():
    app = _app()
    pointer = EmberPointerOverlay()
    try:
        pointer.request(120, 120, "yield")
        app.processEvents()
        assert pointer._yielding is True
        pointer.request(130, 130, "move")
        app.processEvents()
        assert pointer._yielding is False
    finally:
        pointer.close()


def test_both_shapes_are_closed_non_empty_paths():
    # A degenerate path would paint nothing and the pointer would silently vanish.
    for path in (EmberPointerOverlay._arrow_path(), EmberPointerOverlay._hand_path()):
        assert not path.isEmpty()
        r = path.boundingRect()
        assert r.width() > 8 and r.height() > 8
        assert 0 <= r.left() and r.right() <= ember_pointer._SIZE


def test_arrow_tip_coincides_with_the_hotspot():
    r = EmberPointerOverlay._arrow_path().boundingRect()
    hx, hy = ember_pointer._HOTSPOT.x(), ember_pointer._HOTSPOT.y()
    # The tip is the top-left extreme of the arrow; the hotspot must sit on it, or every
    # click lands offset from where the pointer appears to be aiming.
    assert abs(r.left() - hx) <= 2.5, (r.left(), hx)
    assert abs(r.top() - hy) <= 2.5, (r.top(), hy)


def test_painting_does_not_raise_in_either_state():
    app = _app()
    pointer = EmberPointerOverlay()
    try:
        from PyQt6.QtGui import QPixmap
        for action in ("move", "click", "yield"):
            pointer.request(80, 80, action)
            app.processEvents()
            pointer.render(QPixmap(pointer.size()))    # exercises paintEvent for real
    finally:
        pointer.close()


def _run():
    failures = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in sorted(names):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - failures}/{len(names)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
