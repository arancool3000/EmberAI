"""macOS Liquid Glass helper — a real NSVisualEffectView blur behind the Qt content.

Why the earlier version was disabled: mounting the effect view as a *child* of Qt's content
view (it then renders ON TOP and hides every control) or by *replacing* the contentView (it
breaks Qt's renderer) both produced a blank "glass blob". The fix here mounts the effect view
as a **sibling positioned BELOW** the Qt content view, inside the window's frame view — so the
desktop blur sits behind the (translucent) Qt content and the controls stay fully visible.

Robust by construction:
- Any failure tears down cleanly and returns False ("no native blur") so the caller's frosted
  stylesheet still looks like glass.
- It never replaces the contentView and never adds itself as a child of the Qt view.
- The caller can force it off via the `glass_native_blur` setting if a future macOS regresses.
"""
from __future__ import annotations

import os
import sys

_AVAILABLE = sys.platform == "darwin"

# The native NSVisualEffectView blur reparents Qt's NSWindow view hierarchy, which
# segfaults on some macOS versions (notably macOS 26 "Tahoe"). It is therefore OFF by
# default and only attempted when explicitly opted in via EMBER_NATIVE_BLUR=1. With it
# off, the caller's frosted stylesheet still provides a convincing glass look — no crash.
_NATIVE_OK = os.environ.get("EMBER_NATIVE_BLUR", "").strip().lower() not in ("", "0", "false", "no")

# NSVisualEffectView constants (raw ints — avoids importing the AppKit enums).
_BLENDING_BEHIND_WINDOW = 0
_STATE_ACTIVE = 1
_MATERIAL_HUD_WINDOW = 17          # dark frosted glass that follows the desktop
_AUTORESIZE_W_H = 2 | 16           # NSViewWidthSizable | NSViewHeightSizable
_BELOW = 0                         # NSWindowBelow


def _nswindow_for(qwidget):
    import objc
    from ctypes import c_void_p
    view = objc.objc_object(c_void_p=int(qwidget.winId()))
    win = view.window() if view is not None else None
    return view, win


def _teardown(qwidget):
    """Remove any previously-installed effect view and make the window opaque again."""
    eff = getattr(qwidget, "_ns_effect", None)
    if eff is not None:
        try:
            eff.removeFromSuperview()
        except Exception:
            pass
        qwidget._ns_effect = None
    try:
        _, win = _nswindow_for(qwidget)
        if win is not None:
            win.setOpaque_(True)
    except Exception:
        pass


def set_blur(qwidget, enabled: bool, level: int = 60, radius: float = 26.0) -> bool:
    """Mount (or remove) a frosted blur behind the Qt content. Returns True iff a native
    blur is now active (so the caller can thin its stylesheet veil to let it show)."""
    if not _AVAILABLE or not _NATIVE_OK:
        return False
    if not enabled:
        # Only touch native APIs if we actually mounted an effect before. Otherwise do
        # nothing — this avoids bridging Qt's NSWindow into PyObjC on every normal
        # startup (a fragile cross-framework call that can hard-crash the process).
        if getattr(qwidget, "_ns_effect", None) is not None:
            try:
                _teardown(qwidget)
            except Exception:
                pass
        return False
    # Enabling: start from a clean slate so a prior/broken effect self-heals.
    try:
        _teardown(qwidget)
    except Exception:
        pass
    try:
        import objc
        NSVisualEffectView = objc.lookUpClass("NSVisualEffectView")
        NSColor = objc.lookUpClass("NSColor")

        view, window = _nswindow_for(qwidget)
        if view is None or window is None:
            return False
        frame_view = view.superview()
        if frame_view is None:
            return False  # don't risk the on-top fallback — let the stylesheet handle it

        effect = NSVisualEffectView.alloc().initWithFrame_(view.frame())
        effect.setAutoresizingMask_(_AUTORESIZE_W_H)
        effect.setBlendingMode_(_BLENDING_BEHIND_WINDOW)
        effect.setState_(_STATE_ACTIVE)
        try:
            effect.setMaterial_(_MATERIAL_HUD_WINDOW)
        except Exception:
            pass
        # Rounded "water-droplet" corners on the blur itself.
        try:
            effect.setWantsLayer_(True)
            layer = effect.layer()
            if layer is not None:
                layer.setCornerRadius_(float(radius))
                layer.setMasksToBounds_(True)
        except Exception:
            pass

        # The window must be non-opaque + clear so the blur composites with the desktop.
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())

        # Sibling, positioned BELOW the Qt content view — behind the controls, not over them.
        frame_view.addSubview_positioned_relativeTo_(effect, _BELOW, view)
        qwidget._ns_effect = effect
        return True
    except Exception:
        try:
            _teardown(qwidget)
        except Exception:
            pass
        return False
