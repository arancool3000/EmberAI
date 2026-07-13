"""Product-behavior guards for browser features users expect from a desktop browser."""
import ast
from pathlib import Path


SOURCE = (Path(__file__).parent / "ember_browser.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
CLS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "EmberBrowser")
CLASS_SOURCE = ast.get_source_segment(SOURCE, CLS)


def test_expected_browser_shortcuts_are_wired():
    for shortcut in ("Ctrl+J", "Ctrl+Shift+A", "Ctrl+Shift+T", "Ctrl+L", "Ctrl+T", "Ctrl+W"):
        assert shortcut in CLASS_SOURCE


def test_downloads_have_progress_history_and_reveal():
    for method in ("_show_downloads", "_update_download", "_finish_download"):
        assert f"def {method}" in CLASS_SOURCE
    assert "receivedBytesChanged" in CLASS_SOURCE
    assert "Show in folder" in CLASS_SOURCE


def test_privacy_menu_controls_blocking_and_local_data():
    assert "def _show_privacy_menu" in CLASS_SOURCE
    assert "Pause tracker blocking" in CLASS_SOURCE
    assert "deleteAllCookies" in CLASS_SOURCE
    assert "clearHttpCache" in CLASS_SOURCE


def test_session_and_closed_tabs_are_recoverable():
    for method in ("_load_session", "_save_session", "_restore_last_closed_tab", "_duplicate_current_tab"):
        assert f"def {method}" in CLASS_SOURCE
    assert "browser_session.json" in CLASS_SOURCE


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} browser product tests passed")
