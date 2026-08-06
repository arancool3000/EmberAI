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

Ember drives the ONE real system cursor — the same one you use. There is no second
Ember-owned pointer and no "detached" input channel that clicks without moving your mouse;
that existed, read as a strange growth attached to the user's own cursor, and was removed.
The only concession to sharing is :class:`TakeoverMonitor`: if the cursor stops being where
Ember last put it, a human has grabbed it, and Ember stops rather than fighting for it.

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
    "yield_to_human": True,   # abort a move the moment you grab the mouse yourself
}

_YIELDED = False         # the last action stopped because the user grabbed the mouse
_TAKEOVER_SAMPLE_EVERY = 4   # check the real cursor every Nth path point, not every one

#: How far the real cursor may drift from where Ember last put it before we conclude a human
#: is driving. Small enough to notice a deliberate grab, large enough to ignore pointer noise.
TAKEOVER_TOLERANCE = 24


def detect_takeover(expected, actual, tolerance: int = TAKEOVER_TOLERANCE) -> bool:
    """True when the real cursor has drifted far enough from Ember's last write that a
    human must be driving it."""
    return math.hypot(int(actual[0]) - int(expected[0]),
                      int(actual[1]) - int(expected[1])) > max(1, int(tolerance))


class TakeoverMonitor:
    """Tracks whether the user grabbed the mouse while Ember was moving it.

    Ember writes each intermediate point through :meth:`expect`; before the next write it
    calls :meth:`check` with the cursor position it actually reads back. A single sample
    outside tolerance is treated as noise; ``strikes`` consecutive ones mean the human is
    driving and Ember should yield.
    """

    def __init__(self, tolerance: int = TAKEOVER_TOLERANCE, strikes: int = 2):
        self.tolerance = max(1, int(tolerance))
        self.strikes = max(1, int(strikes))
        self._expected = None
        self._misses = 0
        self.taken_over = False

    def expect(self, x: int, y: int) -> None:
        self._expected = (int(x), int(y))

    def check(self, actual) -> bool:
        """Feed an observed cursor position. Returns True once a takeover is confirmed."""
        if actual is None or self._expected is None or self.taken_over:
            return self.taken_over
        if detect_takeover(self._expected, (int(actual[0]), int(actual[1])), self.tolerance):
            self._misses += 1
            if self._misses >= self.strikes:
                self.taken_over = True
        else:
            self._misses = 0     # a single stray sample is pointer noise, not a takeover
        return self.taken_over

    def reset(self) -> None:
        self._expected = None
        self._misses = 0
        self.taken_over = False


def yielded_to_human() -> bool:
    """True when the last action stopped because the user took the mouse.

    Callers must check this before falling back to a raw pyautogui move: a deliberate
    yield returns False like a failure does, and retrying it would put Ember straight
    back into a tug-of-war with the user's hand.
    """
    return _YIELDED


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
    """Clamp to the display area.

    ``screen`` is either a legacy ``(width, height)`` primary-display size or a full
    virtual-desktop ``(left, top, right, bottom)`` rect. The 4-tuple form is what keeps
    multi-monitor targets — including displays at negative coordinates — from being
    dragged back onto the primary screen.
    """
    if not screen:
        return x, y
    if len(screen) >= 4:
        left, top, right, bottom = (int(screen[0]), int(screen[1]),
                                    int(screen[2]), int(screen[3]))
    else:
        left, top, right, bottom = 0, 0, int(screen[0]), int(screen[1])
    right = max(right, left + 1)
    bottom = max(bottom, top + 1)
    return (max(left, min(right - 1, int(x))), max(top, min(bottom - 1, int(y))))


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


def move(x, y, duration: float | None = None, real: bool = False) -> bool:
    """Move the pointer to (x, y) like a human.

    Returns True if a humanized move ran, False if it fell back to / used a plain move.

    ``real`` is accepted and ignored — Ember always drives the one real cursor now. It is
    kept so existing callers that passed it keep working.
    """
    global _YIELDED
    _YIELDED = False
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
    except Exception:
        start = (x, y)
    screen = _screen_bounds(pg)
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
    watch = TakeoverMonitor() if _OPTS.get("yield_to_human", True) else None
    try:
        pg.PAUSE = 0.0   # our own cadence; don't let the global 50ms pause stutter it
        for i, (px, py) in enumerate(path):
            # Periodically confirm the cursor is still where we last put it; if it isn't,
            # the human has taken the mouse, so stop rather than spend the rest of the
            # path fighting them for it. Sampled every few steps rather than every step:
            # a position read is a syscall, and 160 of them per move is both measurable
            # latency and a bigger window for pointer-acceleration noise to look like a
            # takeover.
            if watch is not None and i % _TAKEOVER_SAMPLE_EVERY == 0:
                if watch.check(_position()):
                    _YIELDED = True
                    return False
            try:
                pg.moveTo(px, py, duration=0, _pause=False)
            except TypeError:
                pg.moveTo(px, py, duration=0)
            if watch is not None:
                watch.expect(px, py)
            # ease the *timing* too: dwell a touch longer near the ends
            t = (i + 1) / len(path)
            time.sleep(per * (0.6 + 0.8 * math.sin(math.pi * t)))
        # Land EXACTLY on target — never leave the pointer a rounded/jittered pixel off.
        _snap(pg, x, y)
    finally:
        pg.PAUSE = saved_pause
    return True


def _position():
    """Current real cursor position, or None when it can't be read."""
    pg = _pg()
    if pg is None:
        return None
    try:
        p = pg.position()
        return (int(p[0]), int(p[1]))
    except Exception:
        return None


def virtual_bounds(monitors):
    """Union of every monitor rect as ``(left, top, right, bottom)``, exclusive right/bottom.

    Each monitor is ``(left, top, width, height)``. Secondary displays legitimately sit at
    negative coordinates (a monitor placed left of or above the primary), which is why
    Ember must not clamp to a bare ``(width, height)`` primary-screen box.
    """
    rects = [m for m in (monitors or []) if m and len(m) >= 4]
    if not rects:
        return None
    left = min(int(m[0]) for m in rects)
    top = min(int(m[1]) for m in rects)
    right = max(int(m[0]) + int(m[2]) for m in rects)
    bottom = max(int(m[1]) + int(m[3]) for m in rects)
    return (left, top, right, bottom)


def _screen_bounds(pg):
    """Virtual-desktop rect ``(left, top, right, bottom)`` spanning every monitor.

    ``pyautogui.size()`` only describes the primary display, so clamping a path to it
    dragged any target on a second monitor back onto the primary one — Ember would trace
    its move along the screen edge and, on a monitor positioned left of or above the
    primary (negative coordinates), miss the target entirely.
    """
    try:
        import mss
        with mss.mss() as sct:
            mons = [(m["left"], m["top"], m["width"], m["height"])
                    for m in sct.monitors[1:]]
        box = virtual_bounds(mons)
        if box:
            return box
    except Exception:
        pass
    try:
        w, h = pg.size()
        return (0, 0, int(w), int(h))
    except Exception:
        return None


def set_options(**kw) -> dict:
    for k, v in kw.items():
        if k in _OPTS:
            _OPTS[k] = v
    return dict(_OPTS)


def get_options() -> dict:
    return dict(_OPTS)


def _snap(pg, x, y) -> None:
    """Place the pointer at the exact integer target with no animation/pause."""
    try:
        pg.moveTo(int(x), int(y), duration=0, _pause=False)
    except TypeError:
        pg.moveTo(int(x), int(y), duration=0)


def click(x, y, button: str = "left", double: bool = False,
          move_first: bool = True) -> bool:
    """Click at (x, y) using the real system cursor."""
    pg = _pg()
    if pg is None:
        return False
    return _click_shared(pg, int(x), int(y), button, double, move_first)


def _click_shared(pg, x, y, button: str, double: bool, move_first: bool) -> bool:
    """The real-cursor click: travel there, land exactly, then press."""
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
    """Press, travel, release, using the real system cursor.

    The intermediate motion *is* the gesture — apps track a drag through the real pointer —
    so there is no meaningful way to do this without moving it.
    """
    pg = _pg()
    if pg is None:
        return False
    return _drag_real(pg, int(from_x), int(from_y), int(to_x), int(to_y), button, duration)


def _drag_real(pg, from_x, from_y, to_x, to_y, button: str,
               duration: float | None) -> bool:
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
