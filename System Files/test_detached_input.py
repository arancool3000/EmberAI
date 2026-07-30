"""Tests for the detached-input layer (detached_input.py) and its wiring into human_mouse.

Pure logic only — no display, no pyautogui, no native event APIs. Runnable:
    pytest test_detached_input.py
    python test_detached_input.py
"""
import detached_input as di
import human_mouse as hm


# --- mode negotiation ---------------------------------------------------------

def test_normalize_mode_accepts_aliases_and_junk():
    assert di.normalize_mode("Detached") == "detached"
    assert di.normalize_mode("independent") == "detached"
    assert di.normalize_mode("legacy") == "shared"
    assert di.normalize_mode("borrow") == "restore"
    assert di.normalize_mode(None) == di.DEFAULT_MODE
    assert di.normalize_mode("nonsense") == di.DEFAULT_MODE


def test_detached_is_used_when_a_backend_exists():
    mode, why = di.select_mode("detached", {"detached": True, "restore": True})
    assert mode == "detached" and why


def test_detached_falls_back_to_restore_without_a_backend():
    mode, why = di.select_mode("detached", {"detached": False, "restore": True})
    assert mode == "restore"
    assert "borrow" in why.lower()          # the downgrade is explained, not silent


def test_falls_all_the_way_back_to_shared():
    mode, _ = di.select_mode("detached", {"detached": False, "restore": False})
    assert mode == "shared"
    mode, _ = di.select_mode("restore", {"restore": False})
    assert mode == "shared"


def test_shared_is_honoured_even_when_detached_is_possible():
    # Someone who asked to watch Ember drive the real cursor must keep getting that.
    mode, _ = di.select_mode("shared", {"detached": True, "restore": True})
    assert mode == "shared"


# --- multi-monitor geometry ---------------------------------------------------

def test_virtual_bounds_spans_every_monitor():
    mons = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]
    assert di.virtual_bounds(mons) == (0, 0, 4480, 1440)


def test_virtual_bounds_handles_negative_origins():
    # A second display placed to the LEFT of / above the primary sits at negative coords.
    mons = [(0, 0, 1920, 1080), (-1280, -200, 1280, 800)]
    assert di.virtual_bounds(mons) == (-1280, -200, 1920, 1080)


def test_virtual_bounds_of_nothing_is_none():
    assert di.virtual_bounds([]) is None
    assert di.virtual_bounds(None) is None


def test_clamp_keeps_second_monitor_targets_intact():
    bounds = (0, 0, 4480, 1440)
    assert di.clamp_to_bounds(3000, 900, bounds) == (3000, 900)      # not dragged back
    assert di.clamp_to_bounds(99999, 99999, bounds) == (4479, 1439)


def test_clamp_allows_negative_coordinates_in_range():
    bounds = (-1280, -200, 1920, 1080)
    assert di.clamp_to_bounds(-900, -100, bounds) == (-900, -100)
    assert di.clamp_to_bounds(-99999, -99999, bounds) == (-1280, -200)


def test_clamp_accepts_legacy_width_height():
    assert di.clamp_to_bounds(5000, 5000, (1920, 1080)) == (1919, 1079)
    assert di.clamp_to_bounds(10, 10, None) == (10, 10)


def test_clamp_survives_a_degenerate_rect():
    # A failed probe can report a zero-size display; it must not invert the clamp.
    assert di.clamp_to_bounds(50, 50, (0, 0, 0, 0)) == (0, 0)


# --- human_mouse uses the full virtual desktop --------------------------------

def test_human_mouse_path_respects_a_second_monitor():
    # Regression: paths used to be clamped to the primary display, so a click on a
    # second monitor traced along the screen edge instead of crossing to it.
    bounds = (0, 0, 4480, 1440)
    path = hm.humanized_path((100, 100), (3400, 1200), screen=bounds)
    assert path[-1] == (3400, 1200)
    assert max(x for x, _ in path) > 1920


def test_human_mouse_path_respects_negative_monitor_origins():
    bounds = (-1280, -200, 1920, 1080)
    path = hm.humanized_path((500, 500), (-900, -100), screen=bounds)
    assert path[-1] == (-900, -100)
    assert min(x for x, _ in path) < 0


# --- Win32 lparam packing -----------------------------------------------------

def test_lparam_packs_x_low_y_high():
    assert di.make_lparam(0, 0) == 0
    assert di.make_lparam(5, 0) == 5
    assert di.make_lparam(0, 3) == 3 << 16
    assert di.make_lparam(100, 200) == (200 << 16) | 100


def test_lparam_handles_negative_client_coordinates():
    # A point above/left of a window's client origin is legitimately negative and must
    # be packed as a 16-bit two's-complement value, not overflow into the other half.
    packed = di.make_lparam(-1, -1)
    assert packed == 0xFFFFFFFF
    assert (packed & 0xFFFF) == 0xFFFF and (packed >> 16) == 0xFFFF


# --- takeover detection -------------------------------------------------------

def test_takeover_detected_when_cursor_jumps_away():
    assert di.detect_takeover((100, 100), (400, 400)) is True
    assert di.detect_takeover((100, 100), (102, 103)) is False


def test_monitor_needs_consecutive_strikes():
    m = di.TakeoverMonitor(tolerance=10, strikes=2)
    m.expect(100, 100)
    assert m.check((400, 400)) is False      # one stray sample is noise
    m.expect(100, 100)
    assert m.check((400, 400)) is True       # two in a row means a human has the mouse


def test_a_good_sample_resets_the_strike_count():
    m = di.TakeoverMonitor(tolerance=10, strikes=2)
    m.expect(100, 100)
    m.check((400, 400))
    m.expect(100, 100)
    m.check((100, 101))                      # back on track — not a takeover
    m.expect(100, 100)
    assert m.check((400, 400)) is False


def test_monitor_is_inert_without_readings():
    m = di.TakeoverMonitor()
    assert m.check(None) is False
    m.expect(10, 10)
    assert m.check(None) is False


def test_monitor_latches_and_resets():
    m = di.TakeoverMonitor(tolerance=5, strikes=1)
    m.expect(0, 0)
    assert m.check((500, 500)) is True
    assert m.check((0, 0)) is True           # stays latched for the rest of the move
    m.reset()
    assert m.taken_over is False


# --- cursor borrowing ---------------------------------------------------------

def test_cursor_guard_puts_the_pointer_back():
    written = []
    with di.CursorGuard(lambda: (640, 480), lambda x, y: written.append((x, y))):
        written.append(("acted", 0))
    assert written == [("acted", 0), (640, 480)]


def test_cursor_guard_disabled_is_a_no_op():
    written = []
    with di.CursorGuard(lambda: (1, 2), lambda x, y: written.append((x, y)), enabled=False):
        pass
    assert written == []


def test_cursor_guard_restores_even_when_the_action_raises():
    written = []
    try:
        with di.CursorGuard(lambda: (7, 8), lambda x, y: written.append((x, y))):
            raise RuntimeError("tool blew up")
    except RuntimeError:
        pass
    assert written == [(7, 8)]               # the user's pointer still comes home


def test_cursor_guard_survives_an_unreadable_cursor():
    def broken():
        raise OSError("no display")
    with di.CursorGuard(broken, lambda x, y: None):
        pass                                 # must not raise


def test_cursor_guard_ignores_a_failing_restore():
    def broken_write(x, y):
        raise OSError("display went away")
    with di.CursorGuard(lambda: (1, 1), broken_write):
        pass                                 # a cosmetic restore never breaks a tool


# --- capability probing is safe everywhere ------------------------------------

def test_capabilities_never_raises_and_reports_a_backend():
    caps = di.capabilities()
    assert set(("detached", "restore", "backend")) <= set(caps)
    assert isinstance(caps["detached"], bool)


def test_capabilities_marks_restore_unavailable_without_a_cursor():
    caps = di.capabilities(lambda: None)
    assert caps["restore"] is False


def test_unknown_platform_gets_an_inert_backend():
    b = di.backend_for("Plan9")
    assert b.available() is False
    assert b.click(1, 1) is False            # returns False rather than exploding


# --- human_mouse option plumbing ---------------------------------------------

def test_mode_option_roundtrips():
    before = hm.get_options().get("mode")
    try:
        hm.set_options(mode="shared")
        assert hm.get_options()["mode"] == "shared"
        assert hm.effective_mode()[0] == "shared"
        hm.set_options(mode="restore")
        assert hm.get_options()["mode"] == "restore"
    finally:
        hm.set_options(mode=before)


def test_yield_flag_defaults_false():
    assert hm.yielded_to_human() in (True, False)


def test_shared_mode_still_drives_the_real_cursor():
    """The historical behaviour has to remain reachable for people who want it."""
    moves = []

    class FakePG:
        PAUSE = 0.0
        def __init__(self): self.pos = (0, 0)
        def position(self): return self.pos
        def size(self): return (1920, 1080)
        def moveTo(self, x, y, duration=0, _pause=True):
            self.pos = (x, y); moves.append((x, y))

    before = hm.get_options().get("mode")
    saved = hm._pg
    fake = FakePG()
    try:
        hm.set_options(mode="shared")
        hm._pg = lambda: fake
        assert hm.move(800, 600, duration=0) is True
        assert moves and moves[-1] == (800, 600)
        assert hm.yielded_to_human() is False
    finally:
        hm._pg = saved
        hm.set_options(mode=before)


def test_move_yields_when_the_user_grabs_the_mouse():
    """A cursor that stops tracking Ember's writes means a human has the mouse."""
    class StubbornPG:
        """Ignores Ember entirely — exactly what a hand on the mouse looks like."""
        PAUSE = 0.0
        def position(self): return (5, 5)
        def size(self): return (1920, 1080)
        def moveTo(self, x, y, duration=0, _pause=True): pass

    before = hm.get_options().get("mode")
    saved = hm._pg
    try:
        hm.set_options(mode="shared", yield_to_human=True)
        hm._pg = lambda: StubbornPG()
        assert hm.move(1500, 900, duration=0) is False
        assert hm.yielded_to_human() is True       # yielded, not merely failed
    finally:
        hm._pg = saved
        hm.set_options(mode=before)


class _RecordingPG:
    """Minimal pyautogui stand-in that remembers where it was told to go."""
    PAUSE = 0.0
    def __init__(self): self.pos = (0, 0); self.moves = []
    def position(self): return self.pos
    def size(self): return (1920, 1080)
    def moveTo(self, x, y, duration=0, _pause=True):
        self.pos = (x, y); self.moves.append((x, y))


class _force_detached:
    """Report a working detached backend regardless of the host.

    CI and this container have no per-window event backend, so the mode would otherwise
    resolve to 'restore' and these tests would silently exercise the wrong branch.
    """
    def __enter__(self):
        self._real = di.capabilities
        di.capabilities = lambda *a, **k: {"detached": True, "restore": True,
                                           "backend": "stub"}
        return self

    def __exit__(self, *exc):
        di.capabilities = self._real
        return False


def test_explicit_move_still_moves_the_real_cursor_in_detached_mode():
    """Regression: 'move the mouse to the corner' silently did nothing.

    Detached mode exists to stop Ember hijacking the cursor while it works. It must not
    swallow a direct instruction about the physical pointer — the tool returned ok, the
    cursor never moved, and the agent looped screenshotting trying to work out why.
    """
    fake = _RecordingPG()
    before = hm.get_options().get("mode")
    saved = hm._pg
    try:
        hm.set_options(mode="detached")
        hm._pg = lambda: fake
        with _force_detached():
            assert hm.move(1400, 900, duration=0, real=True) is True
        assert fake.moves and fake.moves[-1] == (1400, 900)
    finally:
        hm._pg = saved
        hm.set_options(mode=before)


def test_internal_travel_still_leaves_the_cursor_alone():
    # The other half of the contract: the move that precedes a click is exactly what
    # detached mode is supposed to skip.
    fake = _RecordingPG()
    before = hm.get_options().get("mode")
    saved = hm._pg
    seen = []
    try:
        hm.set_options(mode="detached")
        hm._pg = lambda: fake
        hm.set_pointer_hook(lambda x, y, action: seen.append((x, y, action)))
        with _force_detached():
            assert hm.move(1400, 900, duration=0) is True
        assert fake.moves == []                      # physical cursor untouched
        assert (1400, 900, "park") in seen           # but Ember's own pointer is shown
    finally:
        hm._pg = saved
        hm.set_pointer_hook(None)
        hm.set_options(mode=before)


def test_yield_can_be_switched_off():
    class StubbornPG:
        PAUSE = 0.0
        def position(self): return (5, 5)
        def size(self): return (1920, 1080)
        def moveTo(self, x, y, duration=0, _pause=True): pass

    before = hm.get_options()
    saved = hm._pg
    try:
        hm.set_options(mode="shared", yield_to_human=False)
        hm._pg = lambda: StubbornPG()
        assert hm.move(1500, 900, duration=0) is True   # pushes through regardless
        assert hm.yielded_to_human() is False
    finally:
        hm._pg = saved
        hm.set_options(mode=before.get("mode"),
                       yield_to_human=before.get("yield_to_human", True))


def _run():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:
                failures += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - failures}/{total} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
