"""Humanized mouse movement for Ember.

The old movement called ``pyautogui.moveTo(x, y, duration=0.08)`` — a straight
line at a fixed tiny duration with linear timing. On screen that reads as a robotic
teleport-and-jab. This module moves the pointer the way a person does:

  * a gently curved path (cubic Bézier with a little perpendicular bow), not a
    ruler-straight line;
  * ease-in / ease-out timing so the cursor accelerates, cruises, then settles;
  * travel time scaled to distance (a flick across the screen is quick; a nudge to
    a nearby icon is short) with a touch of randomness;
  * sub-pixel micro-jitter along the way and a small overshoot-and-correct on long
    moves, which is what real hands do.

The path math (``humanized_path``) is pure and import-light so it can be unit
tested without a display. ``move`` / ``click`` / ``drag`` drive pyautogui, importing
it lazily and falling back to a plain ``moveTo`` if anything is unavailable, so this
module never breaks automation on a headless box.
"""
from __future__ import annotations

import math
import random
import time

# ---------------------------------------------------------------------------
# Options (the UI / agent can tune these at runtime)
# ---------------------------------------------------------------------------
_OPTS = {
    "enabled": True,    # master switch — off => plain linear moveTo
    "speed": 1.0,       # >1 faster, <1 slower / more deliberate
    "curve": 1.0,       # how much the path bows (0 = straight)
    "jitter": 1.0,      # scale of along-the-way micro deviations
    "overshoot": True,  # slight overshoot + settle on longer moves
}


def set_options(**kw) -> dict:
    for k, v in kw.items():
        if k in _OPTS:
            _OPTS[k] = v
    return dict(_OPTS)


def get_options() -> dict:
    return dict(_OPTS)


# ---------------------------------------------------------------------------
# Easing + Bézier
# ---------------------------------------------------------------------------

def _ease_in_out(t: float) -> float:
    """Smooth acceleration then deceleration (cubic smoothstep), clamped to [0,1]."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3.0 - 2.0 * t)


def _cubic_bezier(p0, p1, p2, p3, t: float):
    u = 1.0 - t
    a = u * u * u
    b = 3 * u * u * t
    c = 3 * u * t * t
    d = t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _clamp_point(x, y, screen):
    if not screen:
        return x, y
    w, h = screen
    return (max(0, min(int(w) - 1, x)), max(0, min(int(h) - 1, y)))


def _steps_for(distance: float, speed: float) -> int:
    """How many intermediate points: more for longer moves, but bounded."""
    n = int(distance / max(0.2, 6.0 / max(0.1, speed)))
    return max(8, min(160, n))


def duration_for(distance: float, speed: float | None = None) -> float:
    """Human-ish travel time for a move of `distance` pixels (seconds)."""
    speed = _OPTS["speed"] if speed is None else speed
    # A Fitts-law-ish curve: a floor for tiny moves, sub-linear growth for big ones.
    base = 0.12 + 0.00045 * distance + 0.05 * math.log1p(distance)
    base /= max(0.1, speed)
    base *= random.uniform(0.9, 1.12)
    return max(0.08, min(1.6, base))


def humanized_path(start, end, *, curve=None, jitter=None, overshoot=None,
                   screen=None, steps=None, rng=None) -> list:
    """Return a list of integer (x, y) points from `start` to `end` forming a smooth,
    slightly curved, eased path. The final point is exactly `end`. Pure geometry —
    no display required (used directly by the tests)."""
    r = rng or random
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    curve = _OPTS["curve"] if curve is None else curve
    jitter = _OPTS["jitter"] if jitter is None else jitter
    overshoot = _OPTS["overshoot"] if overshoot is None else overshoot

    if dist < 2:
        return [_clamp_point(int(round(x1)), int(round(y1)), screen)]

    n = steps or _steps_for(dist, _OPTS["speed"])

    # Two control points bowed perpendicular to the travel line -> a natural arc.
    nx, ny = (-dy / dist, dx / dist)           # unit normal
    bow = curve * r.uniform(-0.18, 0.18) * dist
    bow2 = curve * r.uniform(-0.12, 0.12) * dist
    p0 = (x0, y0)
    p1 = (x0 + dx * 0.33 + nx * bow, y0 + dy * 0.33 + ny * bow)
    p2 = (x0 + dx * 0.66 + nx * bow2, y0 + dy * 0.66 + ny * bow2)
    p3 = (x1, y1)

    # Optional overshoot: aim a few px past the target, then settle back exactly.
    settle = None
    if overshoot and dist > 180:
        over = min(18.0, dist * 0.03)
        p3 = (x1 + (dx / dist) * over, y1 + (dy / dist) * over)
        settle = (int(round(x1)), int(round(y1)))

    pts = []
    jit = jitter * min(2.2, 0.6 + dist / 600.0)
    for i in range(1, n + 1):
        t = _ease_in_out(i / n)
        bx, by = _cubic_bezier(p0, p1, p2, p3, t)
        if i < n:  # never jitter the final landing point
            damp = math.sin(math.pi * t)        # most jitter mid-flight, none at ends
            bx += r.uniform(-jit, jit) * damp
            by += r.uniform(-jit, jit) * damp
        pts.append(_clamp_point(int(round(bx)), int(round(by)), screen))

    if settle is not None:
        # short corrective hop from the overshoot back onto the exact target
        ox, oy = pts[-1]
        for k in (0.5, 1.0):
            sx = int(round(ox + (settle[0] - ox) * k))
            sy = int(round(oy + (settle[1] - oy) * k))
            pts.append(_clamp_point(sx, sy, screen))
        pts[-1] = _clamp_point(settle[0], settle[1], screen)
    else:
        pts[-1] = _clamp_point(int(round(x1)), int(round(y1)), screen)
    return pts


# ---------------------------------------------------------------------------
# Drivers (pyautogui)
# ---------------------------------------------------------------------------

def _pg():
    try:
        import pyautogui
        return pyautogui
    except Exception:
        return None


def move(x, y, duration: float | None = None) -> bool:
    """Move the pointer to (x, y) like a human. Returns True if a humanized move ran,
    False if it fell back to / used a plain move."""
    x, y = int(x), int(y)
    pg = _pg()
    if pg is None:
        return False
    if not _OPTS["enabled"]:
        # Plain (non-humanized) move still honours the speed setting: faster speed = shorter
        # travel time. duration=0 when speed is very high so it snaps instantly.
        if duration is None:
            duration = max(0.0, 0.2 / max(0.1, _OPTS.get("speed", 1.0)))
        pg.moveTo(x, y, duration=duration)
        return False
    try:
        start = tuple(pg.position())
        screen = tuple(pg.size())
    except Exception:
        start, screen = (x, y), None
    dist = math.hypot(x - start[0], y - start[1])
    if dist < 2:
        try:
            pg.moveTo(x, y, duration=0, _pause=False)
        except TypeError:
            pg.moveTo(x, y, duration=0)
        return True
    path = humanized_path(start, (x, y), screen=screen)
    total = duration if duration is not None else duration_for(dist)
    per = max(0.001, total / len(path))
    saved_pause = getattr(pg, "PAUSE", 0.0)
    try:
        pg.PAUSE = 0.0   # our own cadence; don't let the global 50ms pause stutter it
        for i, (px, py) in enumerate(path):
            try:
                pg.moveTo(px, py, duration=0, _pause=False)
            except TypeError:
                pg.moveTo(px, py, duration=0)
            # ease the *timing* too: dwell a touch longer near the ends
            t = (i + 1) / len(path)
            time.sleep(per * (0.6 + 0.8 * math.sin(math.pi * t)))
        # Land EXACTLY on target — never leave the pointer a rounded/jittered pixel off.
        _snap(pg, x, y)
    finally:
        pg.PAUSE = saved_pause
    return True


def _snap(pg, x, y) -> None:
    """Place the pointer at the exact integer target with no animation/pause."""
    try:
        pg.moveTo(int(x), int(y), duration=0, _pause=False)
    except TypeError:
        pg.moveTo(int(x), int(y), duration=0)


def click(x, y, button: str = "left", double: bool = False,
          move_first: bool = True) -> bool:
    pg = _pg()
    if pg is None:
        return False
    x, y = int(x), int(y)
    if move_first:
        move(x, y)
    _snap(pg, x, y)                              # guarantee exact position before pressing
    time.sleep(random.uniform(0.03, 0.09))      # tiny human pause before the press
    # Press with EXPLICIT coordinates so the click lands on the exact target regardless
    # of any humanized-travel rounding — accuracy first, realism second.
    fn = pg.doubleClick if double else pg.click
    try:
        fn(x=x, y=y, button=button, _pause=False)
    except TypeError:
        try:
            fn(x, y, button=button)
        except TypeError:
            fn(button=button)
    return True


def drag(from_x, from_y, to_x, to_y, button: str = "left",
         duration: float | None = None) -> bool:
    pg = _pg()
    if pg is None:
        return False
    from_x, from_y, to_x, to_y = int(from_x), int(from_y), int(to_x), int(to_y)
    move(from_x, from_y)
    saved_pause = getattr(pg, "PAUSE", 0.0)
    try:
        pg.PAUSE = 0.0
        _snap(pg, from_x, from_y)               # press down at the exact start point
        time.sleep(random.uniform(0.04, 0.1))
        try:
            pg.mouseDown(button=button, _pause=False)
        except TypeError:
            pg.mouseDown(button=button)
        time.sleep(random.uniform(0.03, 0.08))
        # hold the button and trace a human path to the destination
        move(to_x, to_y, duration=duration)
        _snap(pg, to_x, to_y)                    # release at the exact end point
        time.sleep(random.uniform(0.03, 0.08))
        try:
            pg.mouseUp(button=button, _pause=False)
        except TypeError:
            pg.mouseUp(button=button)
    finally:
        pg.PAUSE = saved_pause
    return True
