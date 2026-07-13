"""Tests for updater.py — the bits that don't touch the network: the SSL context, the
raise_on_error contract that lets a USER-initiated check tell "up to date" apart from "couldn't
reach the server" (the bug where a failed HTTPS fetch was misreported as 'up to date'), and the
Linux AppImage self-update path (asset detection, the swap-script builder, and locating the
running AppImage via $APPIMAGE).
Run: python test_updater.py"""
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

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


def test_check_falls_back_to_independent_manifest_source():
    original_fetch = updater._fetch_json
    original_configured = version.is_configured
    original_urls = version.manifest_urls
    original_current = updater.current_version
    calls = []
    version.is_configured = lambda: True
    version.manifest_urls = lambda: ["https://first/latest.json", "https://backup/latest.json"]
    updater.current_version = lambda: "1.0.0"
    def fake_fetch(url, timeout, attempts=1):
        calls.append(url)
        if "first" in url:
            raise RuntimeError("temporary CDN miss")
        return {"version": "2.0.0", "downloads": {}}
    updater._fetch_json = fake_fetch
    try:
        manifest = updater.check_for_update(raise_on_error=True)
        assert manifest["version"] == "2.0.0"
        assert calls == ["https://first/latest.json", "https://backup/latest.json"]
        assert updater.last_check_diagnostics()["source"] == "https://backup/latest.json"
    finally:
        updater._fetch_json = original_fetch
        version.is_configured = original_configured
        version.manifest_urls = original_urls
        updater.current_version = original_current


def test_release_api_can_recover_a_missing_manifest():
    key = version.platform_key()
    if not key:
        return
    release = {
        "tag_name": "v9.8.7",
        "published_at": "2026-07-12T12:00:00Z",
        "assets": [{
            "name": version.asset_name(key),
            "browser_download_url": (
                f"https://github.com/o/r/releases/download/v9.8.7/{version.asset_name(key)}"),
            "digest": "sha256:" + "a" * 64,
        }],
    }
    manifest = updater._manifest_from_release_api(release)
    assert manifest["version"] == "9.8.7"
    assert manifest["downloads"][key]["sha256"] == "a" * 64


def test_download_retries_and_commits_only_complete_file():
    original_urlopen = urllib.request.urlopen
    original_sleep = updater.time.sleep
    calls = []
    class Response:
        headers = {"Content-Length": "6"}
        def __init__(self): self.parts = [b"abc", b"def", b""]
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, _size=-1): return self.parts.pop(0)
    def fake_urlopen(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("connection reset")
        return Response()
    urllib.request.urlopen = fake_urlopen
    updater.time.sleep = lambda *_: None
    try:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "Ember.zip"
            seen = []
            updater._download("https://github.com/o/r/Ember.zip", dest,
                              progress=seen.append, attempts=3)
            assert dest.read_bytes() == b"abcdef"
            assert not Path(str(dest) + ".part").exists()
            assert len(calls) == 2 and seen[-1] == 100
    finally:
        urllib.request.urlopen = original_urlopen
        updater.time.sleep = original_sleep


def test_safe_zip_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        archive_path = Path(td) / "bad.zip"
        extract = Path(td) / "extract"
        extract.mkdir()
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../outside.txt", "nope")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                updater._safe_extract_zip(archive, extract)
            assert False, "path traversal should be rejected"
        except RuntimeError as exc:
            assert "unsafe path" in str(exc)
        assert not (Path(td) / "outside.txt").exists()


def test_update_result_is_consumed_once():
    original_path = updater.update_result_path
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "result.txt"
        updater.update_result_path = lambda: path
        try:
            path.write_text("ok|Update installed successfully.", encoding="utf-8")
            assert updater.consume_update_result()["ok"] is True
            assert updater.consume_update_result() is None
        finally:
            updater.update_result_path = original_path


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
