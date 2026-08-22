"""Runtime guards for the redesigned Settings page shell."""
import inspect
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("EMBER_SAFE_MODE", "1")

from PyQt6.QtWidgets import QApplication

import ui


def _app():
    return QApplication.instance() or QApplication([])


def test_settings_uses_a_left_navigation_rail_with_matching_pages():
    app = _app()
    dialog = ui.SettingsDialog(ui.load_settings())
    try:
        dialog.show()
        app.processEvents()
        assert dialog.tabs.tabBar().isHidden()
        assert dialog.settings_nav.count() == dialog.tabs.count() >= 8
        dialog.settings_nav.setCurrentRow(3)
        app.processEvents()
        assert dialog.tabs.currentIndex() == 3
        assert dialog.tabs.tabText(3) == "Performance"
    finally:
        dialog.close()


def test_focused_settings_page_hides_the_navigation_rail():
    app = _app()
    dialog = ui.SettingsDialog(ui.load_settings(), only_tab="Voice")
    try:
        dialog.show()
        app.processEvents()
        assert dialog.tabs.tabText(dialog.tabs.currentIndex()) == "Voice"
        assert dialog._settings_nav_panel.isHidden()
    finally:
        dialog.close()


def test_settings_contains_no_fire_or_hearth_animation():
    source = inspect.getsource(ui.SettingsDialog).lower()
    for forbidden in ("hearth", "flamebackground", "ember_fx", "_hearth_timer"):
        assert forbidden not in source
