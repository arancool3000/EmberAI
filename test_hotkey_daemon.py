"""Hermetic tests for the quit-proof global-hotkey helper.

Covers the pure builders, listener-arg construction, the listener's combo→pynput
conversion, and install/uninstall (with the LaunchAgent / autostart dirs redirected to a
temp dir via env vars). No real login items, no pynput, no sockets.

Run: python test_hotkey_daemon.py
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="ember_hk_test_")
os.environ["EMBER_LAUNCHAGENTS_DIR"] = os.path.join(_TMP, "LaunchAgents")
os.environ["EMBER_AUTOSTART_DIR"] = os.path.join(_TMP, "autostart")

import hotkey_daemon as hk
import ember_hotkey_listener as listener


def test_listener_args_source_build():
    args = hk.listener_args("ctrl+shift+space")
    assert args[0] == sys.executable
    assert any(a.endswith("ember_hotkey_listener.py") for a in args)
    assert "--combo" in args and "ctrl+shift+space" in args


def test_macos_plist_has_keepalive_true():
    plist = hk.macos_plist(hk.LABEL, ["/usr/bin/python3", "x.py", "--combo", "ctrl+shift+space"])
    assert "<key>KeepAlive</key>" in plist
    # KeepAlive must be an unconditional <true/> (always relaunch the listener).
    assert plist.split("<key>KeepAlive</key>")[1].lstrip().startswith("<true/>")
    assert "<key>RunAtLoad</key>" in plist
    assert "ctrl+shift+space" in plist
    assert hk.LABEL in plist


def test_macos_plist_xml_escapes_args():
    plist = hk.macos_plist("lbl", ["a&b", "c<d>"])
    assert "a&amp;b" in plist and "c&lt;d&gt;" in plist


def test_linux_desktop_builder():
    d = hk.linux_desktop(["python3", "ember_hotkey_listener.py", "--combo", "ctrl+alt+e"])
    assert d.startswith("[Desktop Entry]")
    assert "Exec=python3 ember_hotkey_listener.py --combo ctrl+alt+e" in d
    assert "X-GNOME-Autostart-enabled=true" in d


def test_install_uninstall_roundtrip_macos(monkeypatch=None):
    real = hk.sys.platform
    try:
        hk.sys.platform = "darwin"
        assert hk.is_installed() is False
        r = hk.install("ctrl+shift+space")
        assert r["ok"] is True, r
        # The plist landed in the redirected dir (we don't actually touch ~/Library).
        assert os.path.exists(r["path"])
        assert hk.is_installed() is True
        u = hk.uninstall()
        assert u["ok"] is True
        assert hk.is_installed() is False
    finally:
        hk.sys.platform = real


def test_install_uninstall_roundtrip_linux():
    real = hk.sys.platform
    try:
        hk.sys.platform = "linux"
        assert hk.is_installed() is False
        r = hk.install("ctrl+alt+e")
        assert r["ok"] is True and os.path.exists(r["path"])
        assert hk.is_installed() is True
        body = open(r["path"]).read()
        assert "ctrl+alt+e" in body
        assert hk.uninstall()["ok"] is True
        assert hk.is_installed() is False
    finally:
        hk.sys.platform = real


def test_set_enabled_delegates():
    real = hk.sys.platform
    try:
        hk.sys.platform = "linux"
        hk.set_enabled(True, "ctrl+x")
        assert hk.is_installed() is True
        hk.set_enabled(False)
        assert hk.is_installed() is False
    finally:
        hk.sys.platform = real


def test_listener_combo_conversion():
    assert listener._pynput_combo("ctrl+shift+space") == "<ctrl>+<shift>+<space>"
    assert listener._pynput_combo("ctrl+alt+e") == "<ctrl>+<alt>+e"
    # blanks / stray separators are dropped
    assert listener._pynput_combo("ctrl++space") == "<ctrl>+<space>"


def test_listener_summon_sends_then_launches():
    """_summon: if the single-instance socket answers, it sends SUMMON and does NOT launch;
    if the connect fails, it launches Ember instead."""
    import socket
    calls = {"sent": 0, "launched": 0}

    real_socket = listener.socket.socket
    real_popen = listener.subprocess.Popen

    class _OkSock:
        def settimeout(self, *_):
            pass

        def connect(self, *_):
            pass

        def sendall(self, *_):
            calls["sent"] += 1

        def close(self):
            pass

    class _DeadSock:
        def settimeout(self, *_):
            pass

        def connect(self, *_):
            raise OSError("nothing listening")

        def sendall(self, *_):
            pass

        def close(self):
            pass

    try:
        listener.subprocess.Popen = lambda *a, **k: calls.__setitem__("launched", calls["launched"] + 1)
        # Case 1: a running instance answers -> summon, no launch.
        listener.socket.socket = lambda *a, **k: _OkSock()
        listener._summon()
        assert calls == {"sent": 1, "launched": 0}, calls
        # Case 2: nobody listening -> launch.
        listener.socket.socket = lambda *a, **k: _DeadSock()
        listener._summon()
        assert calls["launched"] == 1, calls
    finally:
        listener.socket.socket = real_socket
        listener.subprocess.Popen = real_popen


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} hotkey_daemon tests passed")
