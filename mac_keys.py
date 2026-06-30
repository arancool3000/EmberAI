"""macOS key-combo sender — press a shortcut like ``cmd+w`` ATOMICALLY.

Why this exists: ``pyautogui.hotkey('command', 'w')`` on macOS frequently releases the modifier
before the character event registers, so the OS sees ``command`` and then ``w`` as two separate
presses and the shortcut never fires (the exact bug: "it pressed command then after releasing it
pressed w"). Routing the combo through AppleScript's System Events ``keystroke`` holds every
modifier down for the duration of the keypress, so the shortcut lands as one chord.

The script builder (`build_combo_script`) is PURE and unit-tested with no OS; `send_combo` runs
``osascript`` best-effort and never raises. This module imports with only the standard library.
"""
from __future__ import annotations

# Modifier name (any common spelling) -> AppleScript "… down" token.
_AS_MODS = {
    "command": "command down", "cmd": "command down", "win": "command down",
    "windows": "command down", "meta": "command down", "super": "command down",
    "control": "control down", "ctrl": "control down",
    "option": "option down", "opt": "option down", "alt": "option down",
    "shift": "shift down", "fn": "function down", "function": "function down",
}

# Non-character keys -> AppleScript virtual key codes (so e.g. cmd+left, cmd+delete work).
_AS_KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "forwarddelete": 117, "fdel": 117,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


def _split(parts):
    """Return (modifier-tokens, main-key) from a list of key names. The last non-modifier wins."""
    mods, key = [], None
    for p in parts:
        p = (p or "").strip().lower()
        if not p:
            continue
        tok = _AS_MODS.get(p)
        if tok is not None:
            if tok not in mods:
                mods.append(tok)
        else:
            key = p
    return mods, key


def build_combo_script(parts) -> str | None:
    """Build the System Events AppleScript for a key combo, or None if it can't be expressed
    (no modifier, no key, or a multi-char key that isn't a known special key)."""
    mods, key = _split(parts)
    if key is None or not mods:
        return None
    using = " using {" + ", ".join(mods) + "}"
    if key in _AS_KEYCODES:
        action = f"key code {_AS_KEYCODES[key]}"
    elif len(key) == 1:
        esc = key.replace("\\", "\\\\").replace('"', '\\"')
        action = f'keystroke "{esc}"'
    else:
        return None
    return f'tell application "System Events" to {action}{using}'


def send_combo(parts, _runner=None) -> bool:
    """Press the combo via osascript. Returns False if it can't be expressed or the run failed,
    so the caller can fall back to pyautogui. `_runner` is injectable for tests."""
    script = build_combo_script(parts)
    if not script:
        return False
    runner = _runner or _osascript
    try:
        return bool(runner(script))
    except Exception:
        return False


def _osascript(script: str) -> bool:
    import subprocess
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    return r.returncode == 0
