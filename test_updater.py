"""Tests for updater.py — the bits that don't touch the network: the SSL context, the
raise_on_error contract that lets a USER-initiated check tell "up to date" apart from "couldn't
reach the server" (the bug where a failed HTTPS fetch was misreported as 'up to date'), and the
Linux AppImage self-update path (asset detection, the swap-script builder, and locating the
running AppImage via $APPIMAGE).
Run: python test_updater.py"""
import os
import sys
import urllib.request

import updater
import version


def test_ssl_context_is_usable():
    ctx = updater._ssl_context()
    # Either a real SSLContext (certifi or system) or None — never raises.
    import ssl
    assert ctx is None or isinstance(ctx, ssl.SSLContext)


def test_check_raises_on_error_when_asked(monkeypatch=None):
    # Force "configured" so we reach the fetch, then make the fetch blow up.
    orig_configured = version.is_configured
    orig_urlopen = urllib.request.urlopen
    version.is_configured = lambda: True
    def _boom(*a, **k):
        raise OSError("simulated network/SSL failure")
    urllib.request.urlopen = _boom
    try:
        # Default: swallow the error and report "no update" (None) — never disrupts the app.
        assert updater.check_for_update() is None
        # raise_on_error=True: the caller must be able to see the failure.
        raised = False
        try:
            updater.check_for_update(raise_on_error=True)
        except Exception:
            raised = True
        assert raised, "check_for_update(raise_on_error=True) should propagate fetch errors"
    finally:
        version.is_configured = orig_configured
        urllib.request.urlopen = orig_urlopen


def test_check_returns_manifest_when_newer():
    orig_configured = version.is_configured
    orig_urlopen = urllib.request.urlopen
    orig_current = updater.current_version
    version.is_configured = lambda: True
    updater.current_version = lambda: "1.0.0"

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"version": "9.9.9", "downloads": {}}'
    urllib.request.urlopen = lambda *a, **k: _Resp()
    try:
        m = updater.check_for_update(raise_on_error=True)
        assert m and m.get("version") == "9.9.9", m
        # Not newer -> None.
        updater.current_version = lambda: "9.9.9"
        assert updater.check_for_update() is None
    finally:
        version.is_configured = orig_configured
        urllib.request.urlopen = orig_urlopen
        updater.current_version = orig_current


# --- Linux AppImage self-update ---------------------------------------------------------
def test_linux_platform_key_and_asset_name():
    if not sys.platform.startswith("linux"):
        return
    assert version.platform_key() == "linux"
    assert version.asset_name("linux") == "Ember-Linux.AppImage"


def test_update_host_allowlist():
    # Only github.com over HTTPS may serve the update payload — a tampered manifest can't
    # redirect the download to an attacker host.
    assert updater._host_allowed("https://github.com/o/r/releases/latest/download/Ember.zip")
    assert updater._host_allowed("https://www.github.com/o/r/releases/latest/download/x.zip")
    assert not updater._host_allowed("https://evil.example.com/Ember.zip")
    assert not updater._host_allowed("http://github.com/o/r/x.zip")          # not HTTPS
    assert not updater._host_allowed("https://github.com.evil.com/x.zip")     # look-alike host
    assert not updater._host_allowed("")


def test_is_appimage_asset():
    assert updater.is_appimage_asset("https://x/Ember-Linux.AppImage") is True
    assert updater.is_appimage_asset("https://x/Ember-Linux.AppImage?x=1") is True
    assert updater.is_appimage_asset("HTTPS://X/EMBER-LINUX.APPIMAGE") is True
    assert updater.is_appimage_asset("https://x/Ember-macOS.zip") is False
    assert updater.is_appimage_asset("https://x/Ember-Windows.zip") is False


def test_linux_swap_script_has_backup_and_rollback():
    script = updater.linux_swap_script("/tmp/new.AppImage", "/opt/Ember/Ember.AppImage", 4242)
    assert script.startswith("#!/bin/bash")
    assert "kill -0 4242" in script                       # waits for the old process to exit
    assert "Ember.AppImage.old" in script                 # keeps a backup before replacing
    assert "chmod +x" in script                           # re-executable after replacing
    assert "setsid" in script                              # relaunches detached
    assert script.count("if cp") == 1 and "else" in script  # rollback branch present


def test_linux_swap_script_quotes_paths_with_spaces():
    script = updater.linux_swap_script("/tmp/a b.AppImage", "/opt/My App/Ember.AppImage", 1)
    assert "'/tmp/a b.AppImage'" in script
    assert "'/opt/My App/Ember.AppImage'" in script


def test_install_root_linux_uses_appimage_env_var():
    if not sys.platform.startswith("linux"):
        return
    had_frozen = hasattr(sys, "frozen")
    prev_frozen = getattr(sys, "frozen", None)
    old_appimage = os.environ.get("APPIMAGE")
    sys.frozen = True
    os.environ["APPIMAGE"] = "/home/u/Applications/Ember.AppImage"
    try:
        from pathlib import Path
        assert updater.install_root() == Path("/home/u/Applications/Ember.AppImage")
    finally:
        if had_frozen:
            sys.frozen = prev_frozen
        else:
            del sys.frozen
        if old_appimage is None:
            os.environ.pop("APPIMAGE", None)
        else:
            os.environ["APPIMAGE"] = old_appimage


def test_install_root_linux_none_without_appimage_env_var():
    if not sys.platform.startswith("linux"):
        return
    had_frozen = hasattr(sys, "frozen")
    prev_frozen = getattr(sys, "frozen", None)
    old_appimage = os.environ.pop("APPIMAGE", None)
    sys.frozen = True
    try:
        assert updater.install_root() is None
    finally:
        if had_frozen:
            sys.frozen = prev_frozen
        else:
            del sys.frozen
        if old_appimage is not None:
            os.environ["APPIMAGE"] = old_appimage


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} updater tests passed")
