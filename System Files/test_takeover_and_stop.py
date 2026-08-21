"""Tests for two reported failures.

1. "it won't control my mouse as apparently i am moving it"

   Ember refused to move the cursor, blaming the user. The takeover monitor compared the
   cursor only against where Ember last *wrote* it, so a cursor that never moved — because
   Ember had no permission to move it, the usual macOS Accessibility case — looked identical
   to one being dragged away by a hand. The user was told to let go of a mouse they were not
   touching, and the real fix (a permission) was never mentioned.

2. "i cannot interrupt ember in voice mode"

   The Stop button cancelled the agent turn but never stopped speech, and left an orb
   conversation running. Since the mic is deliberately closed while Ember talks, there was no
   voice route either — so nothing could interrupt it.
"""
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("EMBER_SAFE_MODE", "1")
os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_takeover_"))

import pytest

import human_mouse as hm


# --- 1. taken over vs stalled -----------------------------------------------------
def _feed(monitor, expected_points, actual_points):
    """Walk the monitor through a move: expect a point, then observe one."""
    for exp, act in zip(expected_points, actual_points):
        monitor.expect(*exp)
        monitor.check(act)
    return monitor


def test_a_frozen_cursor_is_not_called_a_takeover():
    """The reported bug. Ember writes points; the cursor never moves at all."""
    m = hm.TakeoverMonitor()
    frozen = (500, 500)
    _feed(m, [(100, 100), (150, 150), (200, 200), (250, 250)], [frozen] * 4)
    assert m.taken_over is False, "a stationary cursor is not a human grabbing the mouse"
    assert m.stalled is True


def test_a_moving_cursor_is_a_takeover():
    """A hand really is on the mouse: off-target AND travelling."""
    m = hm.TakeoverMonitor()
    _feed(m, [(100, 100), (150, 150), (200, 200), (250, 250)],
          [(600, 600), (700, 640), (800, 690), (900, 740)])
    assert m.taken_over is True
    assert m.stalled is False


def test_a_cursor_that_follows_along_is_neither():
    """The normal case: the cursor is where Ember put it."""
    m = hm.TakeoverMonitor()
    pts = [(100, 100), (150, 150), (200, 200), (250, 250)]
    _feed(m, pts, pts)
    assert m.taken_over is False and m.stalled is False


def test_one_stray_sample_is_ignored():
    """Pointer noise must not abort a move; that is what `strikes` is for."""
    m = hm.TakeoverMonitor()
    _feed(m, [(100, 100), (150, 150), (200, 200)],
          [(100, 100), (400, 400), (200, 200)])
    assert m.taken_over is False and m.stalled is False


def test_sub_pixel_jitter_does_not_count_as_movement():
    """A cursor drifting a pixel is stationary — otherwise a stalled pointer with a noisy
    reading would be misread as a takeover all over again."""
    m = hm.TakeoverMonitor()
    _feed(m, [(100, 100), (150, 150), (200, 200), (250, 250)],
          [(500, 500), (501, 500), (500, 501), (501, 501)])
    assert m.taken_over is False
    assert m.stalled is True


def test_the_two_states_are_mutually_exclusive():
    for actuals in ([(500, 500)] * 5, [(600 + i * 90, 600) for i in range(5)]):
        m = hm.TakeoverMonitor()
        _feed(m, [(100 + i * 50, 100) for i in range(5)], actuals)
        assert not (m.taken_over and m.stalled)


def test_reset_clears_both():
    m = hm.TakeoverMonitor()
    _feed(m, [(100, 100)] * 4, [(900, 900)] * 4)
    m.reset()
    assert m.taken_over is False and m.stalled is False


# --- what the tool reports --------------------------------------------------------
class _StuckPG:
    """pyautogui that accepts moves and never actually moves the cursor — exactly what an
    unprivileged process sees on macOS."""

    PAUSE = 0.0

    def __init__(self, at=(500, 500)):
        self._pos = at

    def position(self):
        return self._pos

    def size(self):
        return (1920, 1080)

    def moveTo(self, x, y, duration=0, _pause=True):
        pass                                  # silently does nothing, like the real failure

    def click(self, **kw):
        pass


def test_move_reports_a_stall_not_a_yield(monkeypatch):
    monkeypatch.setattr(hm, "_pg", lambda: _StuckPG())
    hm.set_options(enabled=True, yield_to_human=True)
    # Independent-pointer travel intentionally does not touch the hardware cursor.  Request
    # a real move here because this test is specifically about detecting a blocked OS write.
    assert hm.move(1200, 800, duration=0, real=True) is False
    assert hm.input_stalled() is True
    assert hm.yielded_to_human() is False, "nobody touched the mouse"


def test_the_stall_hint_names_the_actual_fix():
    hint = hm.stall_hint()
    assert "could not move the cursor" in hint
    import sys
    if sys.platform == "darwin":
        assert "Accessibility" in hint


def test_tools_surfaces_the_permission_hint(monkeypatch):
    """The user must be told about permissions, not accused of holding the mouse."""
    import sys
    import types
    # tools.py imports the screen/input stack at module level; none of it is exercised here.
    for name, attrs in (("mss", {"mss": lambda *a, **k: None}),
                        ("pyautogui", {"FAILSAFE": False, "PAUSE": 0,
                                       "size": lambda: (1920, 1080),
                                       "position": lambda: (0, 0)})):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            monkeypatch.setitem(sys.modules, name, mod)
    tools = pytest.importorskip("tools")
    if not hasattr(tools, "move_mouse"):
        # Several test modules stub `tools` into sys.modules at import time and never restore
        # it (the same leak that upsets test_tools_linux in a full run). Skip rather than
        # assert against somebody else's stub.
        pytest.skip("sys.modules['tools'] is a stub left by another test module")
    monkeypatch.setattr(hm, "_pg", lambda: _StuckPG())
    hm.set_options(enabled=True, yield_to_human=True)
    r = tools.move_mouse(1200, 800)
    if r.get("ok"):
        pytest.skip("the plain pyautogui fallback succeeded in this environment")
    assert "could not move the cursor" in r["error"]
    assert "took the mouse" not in r["error"]


def test_yield_can_be_switched_off(monkeypatch):
    """A user who does not want Ember backing off should be able to say so."""
    monkeypatch.setattr(hm, "_pg", lambda: _StuckPG())
    hm.set_options(enabled=True, yield_to_human=False)
    try:
        hm.move(1200, 800, duration=0)
        assert hm.input_stalled() is False and hm.yielded_to_human() is False
    finally:
        hm.set_options(yield_to_human=True)


# --- 2. Stop actually stops -------------------------------------------------------
def test_stop_silences_speech_and_ends_the_conversation(monkeypatch):
    """Stop used to cancel the turn and leave Ember talking, with the orb still listening."""
    import sys
    import types
    import ui

    stopped = {"speech": False}
    fake_voice = types.ModuleType("voice")
    fake_voice.stop_speaking = lambda: stopped.__setitem__("speech", True)
    monkeypatch.setitem(sys.modules, "voice", fake_voice)

    class _Agent:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    host = type("Host", (), {"_on_stop": ui.EmberWindow._on_stop})()
    host.agent = _Agent()
    host._voice_chat_enabled = False
    host._orb_conversation = True
    host._orb_active = True
    host._ended = False
    host._end_orb_conversation = lambda spoken="": setattr(host, "_ended", True)
    host._set_status = lambda *_a, **_k: None

    host._on_stop()

    assert stopped["speech"] is True, "Stop must silence Ember"
    assert host._ended is True, "Stop must end the hands-free conversation"
    assert host.agent.stopped is True


def test_stop_is_safe_with_nothing_running(monkeypatch):
    import sys
    import types
    import ui

    fake_voice = types.ModuleType("voice")
    fake_voice.stop_speaking = lambda: None
    monkeypatch.setitem(sys.modules, "voice", fake_voice)

    host = type("Host", (), {"_on_stop": ui.EmberWindow._on_stop})()
    host.agent = None
    host._voice_chat_enabled = False
    host._orb_conversation = False
    host._orb_active = False
    host._set_status = lambda *_a, **_k: None
    host._on_stop()          # must not raise


def test_stop_handles_voice_chat_too(monkeypatch):
    import sys
    import types
    import ui

    fake_voice = types.ModuleType("voice")
    fake_voice.stop_speaking = lambda: None
    monkeypatch.setitem(sys.modules, "voice", fake_voice)

    host = type("Host", (), {"_on_stop": ui.EmberWindow._on_stop})()
    host.agent = None
    host._voice_chat_enabled = True
    host._orb_conversation = False
    host._orb_active = False
    host._stopped_chat = False
    host._stop_voice_chat = lambda msg="": setattr(host, "_stopped_chat", True)
    host._set_status = lambda *_a, **_k: None
    host._on_stop()
    assert host._stopped_chat is True
