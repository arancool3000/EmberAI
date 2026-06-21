"""Tests for Ember's humanized mouse path geometry (human_mouse.py).

Pure geometry — no display / pyautogui needed. Runnable:
    pytest test_human_mouse.py
    python test_human_mouse.py
"""
import math
import random

import human_mouse as hm


def _seeded():
    return random.Random(1234)


def test_path_starts_near_start_and_ends_exactly():
    path = hm.humanized_path((100, 100), (700, 480), screen=(1920, 1080), rng=_seeded())
    assert len(path) >= 8, len(path)
    assert path[-1] == (700, 480), path[-1]                 # lands exactly on target
    assert math.hypot(path[0][0] - 100, path[0][1] - 100) < 40, path[0]


def test_path_stays_within_screen_bounds():
    screen = (1280, 800)
    path = hm.humanized_path((10, 10), (1279, 799), screen=screen, rng=_seeded())
    for x, y in path:
        assert 0 <= x < screen[0] and 0 <= y < screen[1], (x, y)


def test_same_point_is_single_point():
    path = hm.humanized_path((400, 400), (400, 400), screen=(1920, 1080))
    assert path == [(400, 400)], path


def test_path_makes_overall_progress_to_target():
    start, end = (0, 0), (900, 0)
    path = hm.humanized_path(start, end, overshoot=False, screen=(1920, 1080), rng=_seeded())
    xs = [p[0] for p in path]
    # Net progress reaches the target and is broadly monotonic (allow tiny jitter dips).
    assert xs[-1] == 900
    backsteps = sum(1 for a, b in zip(xs, xs[1:]) if b < a - 3)
    assert backsteps <= len(xs) // 5, backsteps


def test_curve_actually_bows_off_the_straight_line():
    # With curvature, the path should deviate from the straight A->B line somewhere.
    start, end = (0, 0), (800, 0)
    path = hm.humanized_path(start, end, curve=1.0, jitter=0.0, overshoot=False, rng=_seeded())
    max_perp = max(abs(y) for _, y in path)
    assert max_perp > 3, max_perp                            # it's not a ruler-straight line


def test_straight_when_curve_zero():
    path = hm.humanized_path((0, 0), (600, 0), curve=0.0, jitter=0.0, overshoot=False, rng=_seeded())
    assert max(abs(y) for _, y in path) <= 1, path


def test_overshoot_then_settles_exactly():
    start, end = (0, 0), (1000, 0)
    path = hm.humanized_path(start, end, curve=0.0, jitter=0.0, overshoot=True, rng=_seeded())
    assert max(x for x, _ in path) >= 1000               # overshoots past the target…
    assert path[-1] == (1000, 0)                          # …then settles exactly on it


def test_duration_scales_with_distance_and_is_bounded():
    short = hm.duration_for(20, speed=1.0)
    longd = hm.duration_for(1500, speed=1.0)
    assert longd > short
    assert 0.05 <= short <= 1.6 and 0.05 <= longd <= 1.6
    # faster speed => shorter time
    assert hm.duration_for(800, speed=2.0) < hm.duration_for(800, speed=0.5)


def test_more_steps_for_longer_moves():
    short = hm.humanized_path((0, 0), (30, 0), jitter=0.0, overshoot=False, rng=_seeded())
    longp = hm.humanized_path((0, 0), (1500, 0), jitter=0.0, overshoot=False, rng=_seeded())
    assert len(longp) > len(short)


def test_options_roundtrip():
    hm.set_options(speed=2.0, enabled=False)
    o = hm.get_options()
    assert o["speed"] == 2.0 and o["enabled"] is False
    hm.set_options(speed=1.0, enabled=True)


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
