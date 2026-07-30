"""Detached input — let Ember act without taking over your physical mouse.

Until now every Ember click drove the one real system cursor: the pointer jumped out
from under your hand, and if you happened to be moving the mouse at the same moment you
and the agent fought over it. This module gives Ember its own input channel.

Three modes, in order of how little they disturb you:

``detached``
    Ember never moves the system cursor at all. Synthetic events are delivered straight
    to the window under the target point — ``CGEventPostToPid`` on macOS, ``PostMessage``
    to the target HWND on Windows, ``xdotool --window`` (XSendEvent) on X11. Your cursor
    stays exactly where you left it and you can keep working in another window.

``restore``
    The cursor is borrowed: Ember records where your pointer was, performs the action,
    then warps it straight back. Works everywhere, costs a brief flicker. This is the
    automatic fallback when a platform backend can't reach the target window.

``shared``
    The historical behaviour — Ember drives the one real cursor and leaves it on target.
    Still the right choice when you *want* to watch it work, or for drag gestures that a
    posted-event backend can't reproduce faithfully.

Regardless of mode, :class:`TakeoverMonitor` watches the real cursor while Ember moves.
If you grab the mouse mid-action Ember yields instead of fighting you for it.

The negotiation, geometry, and takeover logic are pure functions with no display or
native dependency, so they are unit-tested headlessly (``test_detached_input.py``); the
platform backends are probed lazily and degrade to ``restore`` and then ``shared``.
"""
from __future__ import annotations

import math
import platform
import time
from typing import Callable, Iterable, Optional, Sequence, Tuple

Point = Tuple[int, int]

MODES = ("detached", "restore", "shared")
DEFAULT_MODE = "detached"

#: How far (px) the real cursor may drift from where Ember put it before we conclude a
#: human has taken the mouse. Chosen above typical pointer-acceleration rounding but
#: well below a deliberate nudge.
TAKEOVER_TOLERANCE = 24


# ---------------------------------------------------------------------------
# Mode negotiation (pure)
# ---------------------------------------------------------------------------

def normalize_mode(mode: object) -> str:
    """Coerce any stored/settings value to a valid mode name."""
    text = str(mode or "").strip().lower().replace("-", "_")
    aliases = {
        "": DEFAULT_MODE,
        "auto": DEFAULT_MODE,
        "independent": "detached",
        "separate": "detached",
        "own": "detached",
        "borrow": "restore",
        "return": "restore",
        "real": "shared",
        "system": "shared",
        "classic": "shared",
        "legacy": "shared",
    }
    if text in aliases:
        return aliases[text]
    return text if text in MODES else DEFAULT_MODE


def select_mode(requested: object, caps: dict | None = None) -> tuple[str, str]:
    """Resolve the *requested* mode against what this machine can actually do.

    Returns ``(effective_mode, reason)``. The reason is user-facing copy: when Ember
    silently downgrades, the UI should be able to say why rather than leaving someone
    wondering why their cursor still jumps.
    """
    want = normalize_mode(requested)
    caps = caps or {}
    if want == "shared":
        return "shared", "Ember shares your real cursor (you asked to watch it work)."
    if want == "detached":
        if caps.get("detached"):
            return "detached", "Ember uses its own pointer; your mouse is untouched."
        if caps.get("restore"):
            return ("restore",
                    "No per-window event backend here, so Ember borrows the cursor and "
                    "puts it straight back.")
        return ("shared",
                "This system exposes no way to inject input without the real cursor, so "
                "Ember has to share it.")
    # want == "restore"
    if caps.get("restore"):
        return "restore", "Ember borrows the cursor and returns it to where you left it."
    return "shared", "Cursor position can't be read here, so Ember has to share it."


# ---------------------------------------------------------------------------
# Multi-monitor geometry (pure)
# ---------------------------------------------------------------------------

def virtual_bounds(monitors: Optional[Sequence[Sequence[int]]]) -> Optional[Tuple[int, int, int, int]]:
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


def clamp_to_bounds(x: int, y: int, bounds: Optional[Sequence[int]]) -> Point:
    """Clamp to a full virtual-desktop rect, honouring negative origins.

    Accepts either a 4-tuple ``(left, top, right, bottom)`` or a legacy 2-tuple
    ``(width, height)`` meaning a single origin-anchored display.
    """
    if not bounds:
        return int(x), int(y)
    if len(bounds) == 2:
        left, top, right, bottom = 0, 0, int(bounds[0]), int(bounds[1])
    elif len(bounds) >= 4:
        left, top, right, bottom = (int(bounds[0]), int(bounds[1]),
                                    int(bounds[2]), int(bounds[3]))
    else:
        return int(x), int(y)
    # Degenerate rects (a zero-width probe result) must not invert the clamp.
    right = max(right, left + 1)
    bottom = max(bottom, top + 1)
    return (max(left, min(right - 1, int(x))), max(top, min(bottom - 1, int(y))))


def make_lparam(cx: int, cy: int) -> int:
    """Pack window-relative coords into a Win32 ``LPARAM`` (low word x, high word y).

    Negative client coordinates occur whenever the target point sits left of or above a
    window's client origin, so both halves are masked as two's-complement 16-bit values
    rather than assumed positive.
    """
    return ((int(cy) & 0xFFFF) << 16) | (int(cx) & 0xFFFF)


# ---------------------------------------------------------------------------
# Human-takeover detection (pure)
# ---------------------------------------------------------------------------

def detect_takeover(expected: Point, actual: Point,
                    tolerance: int = TAKEOVER_TOLERANCE) -> bool:
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
        self._expected: Optional[Point] = None
        self._misses = 0
        self.taken_over = False

    def expect(self, x: int, y: int) -> None:
        self._expected = (int(x), int(y))

    def check(self, actual: Optional[Sequence[int]]) -> bool:
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


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------

class Backend:
    """Delivers input to the window under a screen point without moving the cursor.

    Every method returns ``False`` when it cannot honour the request, which is the signal
    for :func:`dispatch` to fall back to a mode that always works rather than silently
    dropping an agent action.
    """

    name = "none"

    def available(self) -> bool:
        return False

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        return False


class _MacBackend(Backend):
    """macOS: post mouse events straight to the owning process via Quartz.

    ``CGEventPostToPid`` hands the event to one application's event queue instead of the
    global HID stream, so the system cursor never moves.
    """

    name = "quartz"

    def __init__(self):
        self._q = None

    def _quartz(self):
        if self._q is None:
            try:
                import Quartz  # type: ignore
                self._q = Quartz
            except Exception:
                self._q = False
        return self._q or None

    def available(self) -> bool:
        q = self._quartz()
        return bool(q and hasattr(q, "CGEventPostToPid"))

    def _pid_at(self, x: int, y: int) -> Optional[int]:
        """Owning pid of the frontmost on-screen window containing (x, y)."""
        q = self._quartz()
        if not q:
            return None
        try:
            windows = q.CGWindowListCopyWindowInfo(
                q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                q.kCGNullWindowID) or []
        except Exception:
            return None
        for win in windows:                      # front-to-back order
            try:
                b = win.get("kCGWindowBounds") or {}
                bx, by = float(b.get("X", 0)), float(b.get("Y", 0))
                bw, bh = float(b.get("Width", 0)), float(b.get("Height", 0))
                if bx <= x < bx + bw and by <= y < by + bh:
                    pid = win.get("kCGWindowOwnerPID")
                    if pid:
                        return int(pid)
            except Exception:
                continue
        return None

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        q = self._quartz()
        if not q:
            return False
        pid = self._pid_at(x, y)
        if not pid:
            return False
        try:
            if button == "right":
                down, up, btn = (q.kCGEventRightMouseDown, q.kCGEventRightMouseUp,
                                 q.kCGMouseButtonRight)
            else:
                down, up, btn = (q.kCGEventLeftMouseDown, q.kCGEventLeftMouseUp,
                                 q.kCGMouseButtonLeft)
            for n in ((1, 2) if double else (1,)):
                for kind in (down, up):
                    ev = q.CGEventCreateMouseEvent(None, kind, (float(x), float(y)), btn)
                    # Click count must be set explicitly or the app sees two unrelated
                    # single clicks instead of a double click.
                    q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState, n)
                    q.CGEventPostToPid(pid, ev)
                if double:
                    time.sleep(0.04)
            return True
        except Exception:
            return False


class _WindowsBackend(Backend):
    """Windows: ``PostMessage`` the click to the target HWND in client coordinates."""

    name = "postmessage"

    WM_MOUSEMOVE = 0x0200
    _MSGS = {
        "left": (0x0201, 0x0202, 0x0203, 0x0001),    # down, up, dblclk, MK_LBUTTON
        "right": (0x0204, 0x0205, 0x0206, 0x0002),   # down, up, dblclk, MK_RBUTTON
    }

    def __init__(self):
        self._u32 = None

    def _user32(self):
        if self._u32 is None:
            try:
                import ctypes
                self._u32 = ctypes.windll.user32
            except Exception:
                self._u32 = False
        return self._u32 or None

    def available(self) -> bool:
        return platform.system() == "Windows" and self._user32() is not None

    def _target(self, x: int, y: int):
        """Deepest child window at (x, y) plus the point in its client space."""
        u = self._user32()
        if not u:
            return None, (0, 0)
        try:
            import ctypes
            from ctypes import wintypes

            pt = wintypes.POINT(int(x), int(y))
            hwnd = u.WindowFromPoint(pt)
            if not hwnd:
                return None, (0, 0)
            # Resolve to the deepest child so the click reaches the actual control
            # rather than the top-level frame, which would ignore it.
            u.ScreenToClient(hwnd, ctypes.byref(pt))
            child = u.ChildWindowFromPointEx(hwnd, pt, 0x0001 | 0x0002)  # skip invisible/disabled
            if child and child != hwnd:
                pt = wintypes.POINT(int(x), int(y))
                u.ScreenToClient(child, ctypes.byref(pt))
                hwnd = child
            return hwnd, (pt.x, pt.y)
        except Exception:
            return None, (0, 0)

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        u = self._user32()
        if not u:
            return False
        hwnd, (cx, cy) = self._target(x, y)
        if not hwnd:
            return False
        down, up, dbl, mk = self._MSGS.get(button, self._MSGS["left"])
        lp = make_lparam(cx, cy)
        try:
            u.PostMessageW(hwnd, self.WM_MOUSEMOVE, 0, lp)
            u.PostMessageW(hwnd, down, mk, lp)
            u.PostMessageW(hwnd, up, 0, lp)
            if double:
                u.PostMessageW(hwnd, dbl, mk, lp)
                u.PostMessageW(hwnd, up, 0, lp)
            return True
        except Exception:
            return False


class _X11Backend(Backend):
    """X11: ``xdotool --window`` sends the click to one window via XSendEvent.

    Without ``--window`` xdotool drives the shared XTEST pointer, which is exactly the
    cursor hijacking this module exists to avoid.
    """

    name = "xdotool"

    def __init__(self):
        self._bin = None

    def _xdotool(self):
        if self._bin is None:
            import shutil
            self._bin = shutil.which("xdotool") or False
        return self._bin or None

    def available(self) -> bool:
        import os
        return bool(os.environ.get("DISPLAY")) and self._xdotool() is not None

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        exe = self._xdotool()
        if not exe:
            return False
        import subprocess
        try:
            win = subprocess.run([exe, "getmouselocation", "--shell"],
                                 capture_output=True, text=True, timeout=5)
            # xdotool can only address a window it can name; without one there is no way
            # to deliver a detached click, so let the caller fall back.
            hint = None
            for line in (win.stdout or "").splitlines():
                if line.startswith("WINDOW="):
                    hint = line.split("=", 1)[1].strip()
            if not hint or hint == "0":
                return False
            btn = "3" if button == "right" else "1"
            cmd = [exe, "mousemove", "--window", hint, str(int(x)), str(int(y)),
                   "click", "--window", hint, "--repeat", "2" if double else "1", btn]
            return subprocess.run(cmd, capture_output=True, timeout=8).returncode == 0
        except Exception:
            return False


_BACKENDS: dict[str, Backend] = {}


def backend_for(system: Optional[str] = None) -> Backend:
    """Cached per-window event backend for this OS (a no-op :class:`Backend` if none)."""
    system = system or platform.system()
    if system not in _BACKENDS:
        if system == "Darwin":
            _BACKENDS[system] = _MacBackend()
        elif system == "Windows":
            _BACKENDS[system] = _WindowsBackend()
        elif system == "Linux":
            _BACKENDS[system] = _X11Backend()
        else:
            _BACKENDS[system] = Backend()
    return _BACKENDS[system]


def capabilities(cursor_reader: Optional[Callable[[], Optional[Point]]] = None) -> dict:
    """What this machine supports: ``{"detached": bool, "restore": bool, "backend": str}``."""
    backend = backend_for()
    try:
        detached = bool(backend.available())
    except Exception:
        detached = False
    restore = True
    if cursor_reader is not None:
        try:
            restore = cursor_reader() is not None
        except Exception:
            restore = False
    return {"detached": detached, "restore": restore, "backend": backend.name}


# ---------------------------------------------------------------------------
# Cursor borrowing
# ---------------------------------------------------------------------------

class CursorGuard:
    """Context manager that returns the pointer to where the human left it.

    Used by ``restore`` mode, and by ``detached`` mode as a safety net for any action that
    had to fall back to the real cursor part-way through.
    """

    def __init__(self, read: Callable[[], Optional[Point]],
                 write: Callable[[int, int], None], enabled: bool = True):
        self._read, self._write = read, write
        self.enabled = bool(enabled)
        self.origin: Optional[Point] = None

    def __enter__(self) -> "CursorGuard":
        if self.enabled:
            try:
                pos = self._read()
                self.origin = (int(pos[0]), int(pos[1])) if pos else None
            except Exception:
                self.origin = None
        return self

    def __exit__(self, *exc) -> bool:
        if self.enabled and self.origin is not None:
            try:
                self._write(self.origin[0], self.origin[1])
            except Exception:
                pass          # never turn a cosmetic restore failure into a tool error
        return False           # never swallow the caller's exception
