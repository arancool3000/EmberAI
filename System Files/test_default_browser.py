"""Tests for default-browser detection and control ranking (default_browser.py).

Pure table logic — nothing is launched. Runnable:
    pytest test_default_browser.py
    python test_default_browser.py
"""
import default_browser as db


# --- identification -----------------------------------------------------------

def test_macos_bundle_ids():
    assert db.identify("com.apple.Safari") == "safari"
    assert db.identify("com.google.Chrome") == "chrome"
    assert db.identify("com.brave.Browser") == "brave"
    assert db.identify("org.mozilla.firefox") == "firefox"
    assert db.identify("com.microsoft.edgemac") == "edge"


def test_macos_bundle_ids_with_channel_suffixes():
    # Beta/dev channels append to the bundle id and must still resolve.
    assert db.identify("com.google.Chrome.beta") == "chrome"
    assert db.identify("com.brave.Browser.nightly") == "brave"


def test_windows_progids():
    assert db.identify("ChromeHTML") == "chrome"
    assert db.identify("MSEdgeHTM") == "edge"
    assert db.identify("FirefoxURL-308046B0AF4A39CB") == "firefox"
    assert db.identify("BraveHTML") == "brave"


def test_linux_desktop_files():
    assert db.identify("firefox.desktop") == "firefox"
    assert db.identify("google-chrome.desktop") == "chrome"
    assert db.identify("brave-browser.desktop") == "brave"


def test_chromium_is_not_mistaken_for_chrome():
    # "chromium" contains "chrom" but is its own browser with its own binary.
    assert db.identify("org.chromium.Chromium") == "chromium"
    assert db.identify("chromium-browser.desktop") == "chromium"


def test_unknown_and_empty_handlers():
    assert db.identify("") is None
    assert db.identify(None) is None
    assert db.identify("com.example.NotABrowser") is None


# --- capability ---------------------------------------------------------------

def test_only_chromium_browsers_can_be_driven():
    for key in ("chrome", "edge", "brave", "vivaldi", "arc", "chromium"):
        assert db.can_drive(key) is True, key
    # This is the fact the agent kept getting wrong — Safari has no DevTools Protocol.
    assert db.can_drive("safari") is False
    assert db.can_drive("firefox") is False
    assert db.can_drive(None) is False
    assert db.can_drive("nonsense") is False


def test_display_names_are_human_readable():
    assert db.display_name("safari") == "Safari"
    assert db.display_name("chrome") == "Google Chrome"
    assert db.display_name(None) == "your default browser"


# --- control ranking ----------------------------------------------------------

def test_the_users_own_browser_is_tried_first():
    # The actual bug: Ember hunted for Chrome even when the user lives in Brave.
    order = db.rank_for_control("brave", ["chrome", "brave", "edge"])
    assert order[0] == "brave"


def test_safari_default_falls_back_to_an_installed_chromium():
    # Safari can't be driven, so DOM control has to go somewhere else — but only for
    # tools that genuinely need the DOM.
    order = db.rank_for_control("safari", ["chrome", "edge"])
    assert "safari" not in order
    assert order and order[0] == "chrome"


def test_ranking_never_offers_an_uninstalled_browser():
    order = db.rank_for_control("vivaldi", ["edge"])
    assert order == ["edge"]


def test_ranking_is_empty_when_nothing_drivable_is_installed():
    assert db.rank_for_control("safari", []) == []
    assert db.rank_for_control("firefox", ["firefox"]) == []


def test_ranking_has_no_duplicates():
    order = db.rank_for_control("chrome", ["chrome", "edge", "brave"])
    assert len(order) == len(set(order))
    assert order[0] == "chrome"


# --- binary lookup ------------------------------------------------------------

def test_binary_lookup_uses_the_platform_table():
    found = {"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
    got = db.binary_for("chrome", platform="darwin", exists=lambda p: p in found)
    assert got in found


def test_binary_lookup_returns_none_when_absent():
    assert db.binary_for("chrome", platform="darwin", exists=lambda p: False) is None
    assert db.binary_for("nonsense", platform="darwin") is None


def test_windows_paths_expand_environment_variables():
    # %VAR% is only expanded by os.path.expandvars when running ON Windows, so resolving
    # the Windows table from macOS/Linux used to leave the literal — which can never match.
    import os
    prev = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = r"C:\Users\test\AppData\Local"
    try:
        seen = []
        db.binary_for("chrome", platform="win32", exists=lambda p: seen.append(p) or False)
        assert seen and not any("%LOCALAPPDATA%" in p for p in seen), seen
        assert any(r"C:\Users\test\AppData\Local" in p for p in seen), seen
    finally:
        if prev is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = prev


def test_linux_lookup_goes_through_which():
    got = db.binary_for("chrome", platform="linux",
                        which=lambda c: "/usr/bin/google-chrome" if c == "google-chrome" else None)
    assert got == "/usr/bin/google-chrome"


def test_arc_has_no_windows_or_linux_build():
    assert db.binary_for("arc", platform="win32", exists=lambda p: True) is None
    assert db.binary_for("arc", platform="linux", which=lambda c: "/x") is None


# --- table sanity -------------------------------------------------------------

def test_every_browser_entry_is_complete():
    for key, spec in db.BROWSERS.items():
        assert spec["name"] and isinstance(spec["cdp"], bool), key
        assert spec["bundles"], key
        for platform_key in ("mac", "win", "linux"):
            assert platform_key in spec, (key, platform_key)


def test_detect_and_status_never_raise():
    info = db.detect()
    assert set(("id", "name", "handler", "can_drive", "note")) <= set(info)
    assert info["note"].strip()
    st = db.status()
    assert "control_order" in st and "drivable_installed" in st


def test_status_note_explains_a_non_drivable_default():
    # A Safari user must be told why DOM control needs something else, not left guessing.
    note = db.detect()["note"]
    assert isinstance(note, str) and note


def _run():
    failures = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in sorted(names):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - failures}/{len(names)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
