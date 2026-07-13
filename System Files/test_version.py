"""Tests for version.py's pure helpers: version comparison and the per-OS asset/platform-key
mapping (macOS/Windows/Linux) the updater and download page rely on.
Run: python test_version.py"""
import sys

import version


def test_parse_handles_plain_and_v_prefixed():
    assert version.parse("1.2.3") == (1, 2, 3)
    assert version.parse("v1.2.3") == (1, 2, 3)
    assert version.parse("") == (0,)
    assert version.parse(None) == (0,)


def test_is_newer():
    assert version.is_newer("1.9.1", current="1.9.0") is True
    assert version.is_newer("1.9.0", current="1.9.0") is False
    assert version.is_newer("1.8.9", current="1.9.0") is False


def test_platform_key_matches_running_platform():
    key = version.platform_key()
    if sys.platform == "darwin":
        assert key == "macos"
    elif sys.platform.startswith("win"):
        assert key == "windows"
    elif sys.platform.startswith("linux"):
        assert key == "linux"
    else:
        assert key is None


def test_asset_names_cover_all_three_platforms():
    assert version.asset_name("macos") == "Ember-macOS.zip"
    assert version.asset_name("windows") == "Ember-Windows.zip"
    assert version.asset_name("linux") == "Ember-Linux.AppImage"


def test_asset_name_falls_back_to_macos_for_unknown():
    assert version.asset_name("plan9") == "Ember-macOS.zip"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} version tests passed")
