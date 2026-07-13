"""Tests for the login-item builders (autostart.py). Pure — no install side effects."""
import sys

import autostart as a


def test_macos_plist_has_label_args_and_runatload():
    xml = a.macos_plist("com.ember.test", ["/usr/bin/open", "/Applications/Ember.app"])
    assert "<key>Label</key>" in xml and "com.ember.test" in xml
    assert "/Applications/Ember.app" in xml
    assert "<key>RunAtLoad</key>" in xml and "<true/>" in xml
    # relaunch only on abnormal exit (a clean Quit stays quit)
    assert "SuccessfulExit" in xml and "<false/>" in xml
    assert xml.startswith("<?xml")


def test_macos_plist_escapes_xml():
    xml = a.macos_plist("lbl", ["/bin/x", "a & b <c>"])
    assert "&amp;" in xml and "&lt;c&gt;" in xml
    assert " & b " not in xml          # raw ampersand must not survive


def test_linux_desktop_entry():
    d = a.linux_desktop(["/bin/bash", "/home/u/run.sh"])
    assert d.startswith("[Desktop Entry]")
    assert "Exec=/bin/bash /home/u/run.sh" in d
    assert "X-GNOME-Autostart-enabled=true" in d


def test_program_args_is_a_nonempty_list():
    args = a.program_args()
    assert isinstance(args, list) and args and all(isinstance(x, str) for x in args)


def test_program_args_frozen_linux_launches_own_executable():
    # Regression: the Linux branch used to ALWAYS look for run.sh (a source-checkout-only
    # convenience script) even in a frozen/packaged build, where it never exists - producing a
    # broken autostart entry. A frozen build's own binary must be the launcher, like Windows.
    if not sys.platform.startswith("linux"):
        return  # this branch only applies on Linux; other OSes have their own frozen handling
    had_attr = hasattr(sys, "frozen")
    prev = getattr(sys, "frozen", None)
    sys.frozen = True
    try:
        assert a.program_args() == [sys.executable]
    finally:
        if had_attr:
            sys.frozen = prev
        else:
            del sys.frozen


def test_status_shape():
    s = a.status()
    assert s["ok"] is True and "installed" in s and isinstance(s["command"], list)


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn(); print(f"PASS  {fn.__name__}"); passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
