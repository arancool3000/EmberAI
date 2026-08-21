"""Platform input that lets Ember click without taking the user's pointer.

Desktop automation normally drives the single system cursor.  That is accurate, but it
also interrupts the person using the computer.  Ember prefers a detached click channel:
events are posted to the app beneath a screen point while the physical cursor stays put.

Not every app or platform accepts posted events, so callers negotiate one of three modes:

``detached``
    Post the click directly to the target app.  The user's cursor never moves.
``restore``
    Borrow the system cursor for the action and immediately return it.
``shared``
    Drive the system cursor normally.

The public negotiation and geometry helpers are dependency-free and safe to import in
headless tests.  Native APIs are loaded lazily and every backend fails closed so an input
request can fall back instead of being silently dropped.
"""
from __future__ import annotations

import os
import platform
import time
from typing import Callable, Optional, Sequence, Tuple

Point = Tuple[int, int]
MODES = ("detached", "restore", "shared")
DEFAULT_MODE = "detached"


def normalize_mode(value: object) -> str:
    """Return a supported pointer mode for a saved/user-facing value."""
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "": DEFAULT_MODE,
        "auto": DEFAULT_MODE,
        "own": "detached",
        "independent": "detached",
        "separate": "detached",
        "borrow": "restore",
        "return": "restore",
        "real": "shared",
        "system": "shared",
        "classic": "shared",
    }
    text = aliases.get(text, text)
    return text if text in MODES else DEFAULT_MODE


def select_mode(requested: object, caps: Optional[dict] = None) -> tuple[str, str]:
    """Resolve a requested mode to what this computer can actually provide."""
    want = normalize_mode(requested)
    caps = caps or {}
    if want == "shared":
        return "shared", "Shared cursor — Ember moves the system pointer while it works."
    if want == "detached" and caps.get("detached"):
        return "detached", "Independent pointer — your mouse stays exactly where you left it."
    if caps.get("restore"):
        if want == "detached":
            return (
                "restore",
                "Borrow and return — this computer cannot target that app independently, "
                "so Ember puts your pointer straight back after each action.",
            )
        return "restore", "Borrow and return — Ember restores your pointer after every action."
    return (
        "shared",
        "Shared cursor — this system does not expose a reliable independent input channel.",
    )


def virtual_bounds(monitors: Optional[Sequence[Sequence[int]]]):
    """Union monitor rectangles as ``(left, top, right, bottom)``."""
    rects = [m for m in (monitors or []) if m and len(m) >= 4]
    if not rects:
        return None
    return (
        min(int(m[0]) for m in rects),
        min(int(m[1]) for m in rects),
        max(int(m[0]) + int(m[2]) for m in rects),
        max(int(m[1]) + int(m[3]) for m in rects),
    )


def make_lparam(x: int, y: int) -> int:
    """Pack signed client coordinates for a Win32 mouse message."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


class Backend:
    """Base class for a non-cursor-moving click backend."""

    name = "unavailable"

    def available(self) -> bool:
        return False

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        return False


class MacBackend(Backend):
    """Post Quartz events to the process owning the topmost window at a point."""

    name = "Quartz"

    def __init__(self):
        self._api = None

    def _quartz(self):
        if self._api is None:
            try:
                import Quartz  # type: ignore
                self._api = Quartz
            except Exception:
                self._api = False
        return self._api or None

    def available(self) -> bool:
        q = self._quartz()
        return bool(q and hasattr(q, "CGEventPostToPid"))

    def _pid_at(self, x: int, y: int) -> Optional[int]:
        q = self._quartz()
        if not q:
            return None
        try:
            windows = q.CGWindowListCopyWindowInfo(
                q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements,
                q.kCGNullWindowID,
            ) or []
        except Exception:
            return None
        for window in windows:  # Quartz returns front-to-back order.
            try:
                bounds = window.get("kCGWindowBounds") or {}
                left, top = float(bounds.get("X", 0)), float(bounds.get("Y", 0))
                width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
                if left <= x < left + width and top <= y < top + height:
                    pid = window.get("kCGWindowOwnerPID")
                    # The click-through cursor is itself a tiny always-on-top Ember window.
                    # Quartz window enumeration still sees it even though hit testing does not.
                    # Selecting that PID was the old implementation's core bug: every intended
                    # click was posted back to Ember and brought the main app forward. Skip only
                    # tiny windows from this process so clicking Ember's real window still works.
                    if (pid and int(pid) == os.getpid()
                            and width <= 96 and height <= 96):
                        continue
                    return int(pid) if pid else None
            except Exception:
                continue
        return None

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        q = self._quartz()
        pid = self._pid_at(x, y)
        if not q or not pid:
            return False
        try:
            # Bring the intended application—not Ember—to the foreground. This keeps the
            # next keyboard action paired with the clicked field while still leaving the
            # user's physical pointer untouched.
            try:
                from AppKit import (  # type: ignore
                    NSApplicationActivateIgnoringOtherApps,
                    NSRunningApplication,
                )
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if app is not None:
                    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                    time.sleep(0.025)
            except Exception:
                pass
            if button == "right":
                down, up, native_button = (
                    q.kCGEventRightMouseDown,
                    q.kCGEventRightMouseUp,
                    q.kCGMouseButtonRight,
                )
            else:
                down, up, native_button = (
                    q.kCGEventLeftMouseDown,
                    q.kCGEventLeftMouseUp,
                    q.kCGMouseButtonLeft,
                )
            click_count = 2 if double else 1
            for index in range(click_count):
                for kind in (down, up):
                    event = q.CGEventCreateMouseEvent(
                        None, kind, (float(x), float(y)), native_button
                    )
                    q.CGEventSetIntegerValueField(
                        event, q.kCGMouseEventClickState, index + 1
                    )
                    q.CGEventPostToPid(pid, event)
                if double:
                    time.sleep(0.045)
            return True
        except Exception:
            return False


class WindowsBackend(Backend):
    """Send a click to the deepest enabled child window under a screen point."""

    name = "Windows messages"
    WM_MOUSEMOVE = 0x0200
    BUTTONS = {
        "left": (0x0201, 0x0202, 0x0203, 0x0001),
        "right": (0x0204, 0x0205, 0x0206, 0x0002),
    }

    def __init__(self):
        self._api = None

    def _user32(self):
        if self._api is None:
            try:
                import ctypes
                self._api = ctypes.windll.user32
            except Exception:
                self._api = False
        return self._api or None

    def available(self) -> bool:
        return platform.system() == "Windows" and self._user32() is not None

    def _target(self, x: int, y: int):
        api = self._user32()
        if not api:
            return None, (0, 0)
        try:
            import ctypes
            from ctypes import wintypes

            screen_point = wintypes.POINT(int(x), int(y))
            hwnd = api.WindowFromPoint(screen_point)
            if not hwnd:
                return None, (0, 0)
            api.ScreenToClient(hwnd, ctypes.byref(screen_point))
            child = api.ChildWindowFromPointEx(hwnd, screen_point, 0x0001 | 0x0002)
            if child and child != hwnd:
                screen_point = wintypes.POINT(int(x), int(y))
                api.ScreenToClient(child, ctypes.byref(screen_point))
                hwnd = child
            return hwnd, (screen_point.x, screen_point.y)
        except Exception:
            return None, (0, 0)

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        api = self._user32()
        hwnd, point = self._target(x, y)
        if not api or not hwnd:
            return False
        down, up, double_down, mask = self.BUTTONS.get(button, self.BUTTONS["left"])
        packed = make_lparam(*point)
        try:
            # Focus the actual top-level target so subsequent text/shortcuts go to the
            # control Ember clicked. The cursor remains in the user's hand.
            root = api.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
            api.SetForegroundWindow(root)
            api.PostMessageW(hwnd, self.WM_MOUSEMOVE, 0, packed)
            api.PostMessageW(hwnd, down, mask, packed)
            api.PostMessageW(hwnd, up, 0, packed)
            if double:
                api.PostMessageW(hwnd, double_down, mask, packed)
                api.PostMessageW(hwnd, up, 0, packed)
            return True
        except Exception:
            return False


_BACKENDS: dict[str, Backend] = {}


def backend_for(system: Optional[str] = None) -> Backend:
    """Return the cached native backend for an operating system."""
    system = system or platform.system()
    if system not in _BACKENDS:
        if system == "Darwin":
            _BACKENDS[system] = MacBackend()
        elif system == "Windows":
            _BACKENDS[system] = WindowsBackend()
        else:
            # Wayland intentionally has no global input injection, and X11 window-at-point
            # discovery is inconsistent across compositors.  Borrow-and-return is honest.
            _BACKENDS[system] = Backend()
    return _BACKENDS[system]


def capabilities(cursor_reader: Optional[Callable[[], Optional[Point]]] = None) -> dict:
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


class CursorGuard:
    """Restore the physical cursor even if an action raises."""

    def __init__(
        self,
        read: Callable[[], Optional[Point]],
        write: Callable[[int, int], None],
        enabled: bool = True,
    ):
        self._read = read
        self._write = write
        self.enabled = bool(enabled)
        self.origin: Optional[Point] = None

    def __enter__(self):
        if self.enabled:
            try:
                point = self._read()
                if point is not None:
                    self.origin = (int(point[0]), int(point[1]))
            except Exception:
                self.origin = None
        return self

    def __exit__(self, *_exc):
        if self.enabled and self.origin is not None:
            try:
                self._write(*self.origin)
            except Exception:
                pass
        return False
