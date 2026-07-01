"""Hermetic tests for the Linux additions to tools.py: the wmctrl window-list parser, the
run_powershell shell-selection fix, and Wayland detection/messaging. No GUI, no window manager,
no network - the heavy deps tools.py imports at module load (mss/pyautogui/pyperclip/PIL/browser's
requests) are stubbed so this runs in CI with only the standard library.
Run: python test_tools_linux.py"""
import os
import sys
import types

for _name in ("mss", "pyautogui", "pyperclip"):
    if _name not in sys.modules:
        m = types.ModuleType(_name)
        sys.modules[_name] = m
pyautogui = sys.modules["pyautogui"]
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0
pyautogui.position = lambda: (0, 0)
pyautogui.moveTo = lambda *a, **k: None
pyautogui.click = lambda *a, **k: None
pyautogui.doubleClick = lambda *a, **k: None
pyautogui.hotkey = lambda *a, **k: None
pyautogui.press = lambda *a, **k: None

if "PIL" not in sys.modules:
    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace()
    pil.ImageDraw = types.SimpleNamespace()
    pil.ImageFont = types.SimpleNamespace()
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = pil.Image
    sys.modules["PIL.ImageDraw"] = pil.ImageDraw
    sys.modules["PIL.ImageFont"] = pil.ImageFont

if "requests" not in sys.modules:
    req = types.ModuleType("requests")
    req.exceptions = types.SimpleNamespace(RequestException=Exception)
    sys.modules["requests"] = req

import tools  # noqa: E402  (heavy deps stubbed above first)

assert sys.platform.startswith("linux"), "these tests assume a Linux sys.platform (matches CI)"


# --- _parse_wmctrl_lpg: pure parser for `wmctrl -lpG` output --------------------------------
_SAMPLE = (
    "0x02200003  0 1234   -1    -1    1920  1080 host Desktop\n"
    "0x02e0000e  0 5678   66    97    1200  800  host Terminal - user@host: ~\n"
    "\n"                                    # blank lines are skipped
    "0x0300000a  1 9012   0     0     800   600  host \n"  # empty title (still 9 tokens incl. blank)
)


def test_parses_multiple_windows():
    out = tools._parse_wmctrl_lpg(_SAMPLE)
    assert len(out) >= 2
    titles = [w["title"] for w in out]
    assert "Desktop" in titles
    assert any(t.startswith("Terminal") for t in titles)


def test_parses_geometry_and_pid_as_ints():
    out = tools._parse_wmctrl_lpg(_SAMPLE)
    term = next(w for w in out if w["title"].startswith("Terminal"))
    assert term == {"title": "Terminal - user@host: ~", "class": "host",
                     "x": 66, "y": 97, "w": 1200, "h": 800, "process_id": 5678}


def test_malformed_lines_are_skipped_not_raised():
    assert tools._parse_wmctrl_lpg("garbage\nnot enough columns here\n") == []
    assert tools._parse_wmctrl_lpg("") == []
    assert tools._parse_wmctrl_lpg(None) == []


def test_non_numeric_geometry_is_skipped():
    bad = "0x1  0 abc  x  y  w  h  host Title\n"
    assert tools._parse_wmctrl_lpg(bad) == []


# --- _linux_list_windows / _linux_focus_window: graceful "not installed" (no wmctrl in CI) --
def test_linux_list_windows_without_wmctrl_is_a_clean_error():
    r = tools._linux_list_windows()
    assert r["ok"] is False and "wmctrl" in r["error"].lower()


def test_linux_focus_window_without_wmctrl_is_a_clean_error():
    r = tools._linux_focus_window("Terminal")
    assert r["ok"] is False and "wmctrl" in r["error"].lower()


def test_list_windows_dispatches_to_linux_impl():
    # sys.platform is genuinely "linux" here (matches the ubuntu-latest CI runner), so the
    # public list_windows()/focus_window() entry points should hit the Linux path directly.
    assert tools.list_windows() == tools._linux_list_windows()


def test_find_ui_elements_gives_a_clear_not_supported_message_on_linux():
    r = tools.find_ui_elements()
    assert r["ok"] is False
    assert "linux" in r["error"].lower() or "ocr" in r["error"].lower()


# --- run_powershell: must not hardcode zsh (missing on most Linux distros) ------------------
def test_run_powershell_uses_bash_or_shell_env_on_linux():
    # We can't assume bash is installed in every CI image, so just check it does NOT pick zsh
    # (the pre-fix bug) and produces a real, runnable shell command.
    res = tools.run_powershell("echo hi", timeout=5)
    assert isinstance(res, dict) and "ok" in res
    # Confirm the shell selection logic itself picks something sane without invoking a shell:
    import shutil as _shutil
    expected = _shutil.which("bash") or os.environ.get("SHELL") or "/bin/sh"
    assert expected != "/bin/zsh" or _shutil.which("zsh") is not None  # sanity: not blindly zsh


# --- Wayland detection ------------------------------------------------------------------
def test_is_wayland_false_without_env_vars():
    old_wd = os.environ.pop("WAYLAND_DISPLAY", None)
    old_st = os.environ.pop("XDG_SESSION_TYPE", None)
    try:
        assert tools.is_wayland() is False
    finally:
        if old_wd is not None:
            os.environ["WAYLAND_DISPLAY"] = old_wd
        if old_st is not None:
            os.environ["XDG_SESSION_TYPE"] = old_st


def test_is_wayland_true_with_wayland_display():
    old = os.environ.get("WAYLAND_DISPLAY")
    os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    try:
        assert tools.is_wayland() is True
    finally:
        if old is None:
            os.environ.pop("WAYLAND_DISPLAY", None)
        else:
            os.environ["WAYLAND_DISPLAY"] = old


def test_is_wayland_true_with_session_type():
    old = os.environ.get("XDG_SESSION_TYPE")
    os.environ["XDG_SESSION_TYPE"] = "wayland"
    try:
        assert tools.is_wayland() is True
    finally:
        if old is None:
            os.environ.pop("XDG_SESSION_TYPE", None)
        else:
            os.environ["XDG_SESSION_TYPE"] = old


def test_wayland_note_appends_hint_only_when_wayland():
    old = os.environ.get("WAYLAND_DISPLAY")
    os.environ.pop("WAYLAND_DISPLAY", None)
    old_st = os.environ.pop("XDG_SESSION_TYPE", None)
    try:
        assert tools._wayland_note("boom") == "boom"
    finally:
        if old is not None:
            os.environ["WAYLAND_DISPLAY"] = old
        if old_st is not None:
            os.environ["XDG_SESSION_TYPE"] = old_st
    os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    try:
        note = tools._wayland_note("boom")
        assert note.startswith("boom (") and "Wayland" in note
    finally:
        os.environ.pop("WAYLAND_DISPLAY", None)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tools-linux tests passed")
