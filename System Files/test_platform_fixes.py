"""Regression tests for two platform fixes:

* Ember Browser User-Agent — the default QtWebEngine UA advertises 'QtWebEngine/...' and an old
  Chrome, so sites (BandLab, Google Docs, Figma…) show "unsupported browser / update your
  browser". Ember must present a plain, current Chrome UA.
* macOS window-listing timeout — tools._osa used to surface the raw
  "Command '[...]' timed out after 8 seconds" (dumping the whole AppleScript) and list_windows
  gave System Events only 8s to enumerate a busy desktop. It must fail with a clean, actionable
  message and a realistic timeout.

ember_browser/tools import PyQt/pyautogui and can't load headless, so the pure helpers are
extracted from source and exercised directly (like the other browser tests).
"""
import ast
import os
import subprocess as _real_subprocess

_HERE = os.path.dirname(__file__)
_BROWSER = open(os.path.join(_HERE, "ember_browser.py"), encoding="utf-8").read()
_TOOLS = open(os.path.join(_HERE, "tools.py"), encoding="utf-8").read()


def _extract_fn(src, name, glb):
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            exec(compile(ast.Module([n], []), "<x>", "exec"), glb)
            return glb[name]
    raise AssertionError(f"function {name} not found")


# ---- Browser User-Agent -----------------------------------------------------------------

def test_modern_user_agent_is_plain_current_chrome():
    import sys
    fn = _extract_fn(_BROWSER, "_modern_user_agent", {"sys": sys})
    ua = fn()
    assert ua.startswith("Mozilla/5.0")
    assert "Chrome/" in ua and "Safari/537.36" in ua
    assert "QtWebEngine" not in ua        # the token sites sniff and reject
    # a plausibly-modern major version (>= 120), so "update your browser" banners don't fire
    import re
    major = int(re.search(r"Chrome/(\d+)", ua).group(1))
    assert major >= 120, ua


def test_modern_user_agent_matches_each_platform():
    fn_src = None
    for plat, token in (("darwin", "Macintosh"), ("win32", "Windows NT"), ("linux", "X11; Linux")):
        class _FakeSys:
            platform = plat
        fn = _extract_fn(_BROWSER, "_modern_user_agent", {"sys": _FakeSys})
        assert token in fn(), (plat, fn())


def test_profile_sets_the_user_agent():
    # Wired onto the real profile, not just defined.
    assert "setHttpUserAgent(_modern_user_agent())" in _BROWSER


# ---- macOS window listing ---------------------------------------------------------------

def test_osa_timeout_is_clean_not_raw_repr():
    class _FakeSub:
        TimeoutExpired = _real_subprocess.TimeoutExpired

        def run(self, *a, **k):
            raise _real_subprocess.TimeoutExpired(cmd="osascript", timeout=k.get("timeout", 8))

    ok, msg = _extract_fn(_TOOLS, "_osa", {"subprocess": _FakeSub()})("some script", timeout=8)
    assert ok is False
    assert "took too long" in msg.lower()
    # must NOT leak the raw subprocess repr / the whole script back to the user
    assert "Command" not in msg and "some script" not in msg


def test_osa_success_path_still_works():
    class _R:
        returncode = 0
        stdout = "hello"
        stderr = ""

    class _FakeSub:
        TimeoutExpired = _real_subprocess.TimeoutExpired

        def run(self, *a, **k):
            return _R()

    ok, out = _extract_fn(_TOOLS, "_osa", {"subprocess": _FakeSub()})("s")
    assert ok is True and out == "hello"


def test_list_windows_has_a_realistic_timeout():
    # The 8s that users hit is gone; the heavy enumeration gets real room.
    tree = ast.parse(_TOOLS)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "list_windows")
    src = ast.get_source_segment(_TOOLS, fn)
    assert "_osa(script, timeout=30)" in src


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
