"""Headless regression tests for Ember's independent input negotiation."""
import os

import detached_input as di


def test_mode_negotiation_is_honest_about_fallbacks():
    assert di.select_mode("detached", {"detached": True, "restore": True})[0] == "detached"
    mode, note = di.select_mode("detached", {"detached": False, "restore": True})
    assert mode == "restore" and "Borrow" in note
    assert di.select_mode("restore", {"restore": False})[0] == "shared"


def test_mode_aliases_are_normalized():
    assert di.normalize_mode("own") == "detached"
    assert di.normalize_mode("borrow") == "restore"
    assert di.normalize_mode("real") == "shared"
    assert di.normalize_mode("nonsense") == "detached"


def test_virtual_bounds_include_negative_monitors():
    monitors = [(0, 0, 1920, 1080), (-1280, -200, 1280, 800)]
    assert di.virtual_bounds(monitors) == (-1280, -200, 1920, 1080)


def test_win32_coordinates_are_packed_as_signed_words():
    assert di.make_lparam(100, 200) == (200 << 16) | 100
    assert di.make_lparam(-1, -1) == 0xFFFFFFFF


def test_cursor_guard_always_returns_the_users_pointer():
    writes = []
    try:
        with di.CursorGuard(lambda: (40, 60), lambda x, y: writes.append((x, y))):
            raise RuntimeError("action failed")
    except RuntimeError:
        pass
    assert writes == [(40, 60)]


def test_mac_target_lookup_skips_embers_pointer_overlay():
    """The old cursor selected its own topmost overlay and clicked Ember instead."""
    class Quartz:
        kCGWindowListOptionOnScreenOnly = 1
        kCGWindowListExcludeDesktopElements = 2
        kCGNullWindowID = 0

        @staticmethod
        def CGWindowListCopyWindowInfo(*_args):
            return [
                {"kCGWindowBounds": {"X": 90, "Y": 90, "Width": 48, "Height": 48},
                 "kCGWindowOwnerPID": os.getpid()},
                {"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1200, "Height": 800},
                 "kCGWindowOwnerPID": 4242},
            ]

    backend = di.MacBackend()
    backend._api = Quartz
    assert backend._pid_at(110, 110) == 4242


def test_unknown_platform_never_claims_independent_input():
    backend = di.backend_for("Plan9")
    assert backend.available() is False
    assert backend.click(10, 20) is False
