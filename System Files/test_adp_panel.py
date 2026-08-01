"""Behavioural tests for the Advanced Data Protection panel in Settings ▸ Security.

Builds the real panel offscreen and drives it, rather than only asserting the wiring exists.
The ADP methods are borrowed onto a plain QWidget because constructing the whole SettingsDialog
would drag in the agent, the model catalogue and every other tab.

test_ui_smoke.py separately constructs the full SettingsDialog, which is what proves the panel
survives being built in its real home.
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("EMBER_SAFE_MODE", "1")
os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_adp_panel_"))

import sys
import types

import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout

# The phone-setup handler reaches remote_server, which imports pyautogui/tools at module level
# for the screen-mirroring half. Neither is available headless. Same stubbing as test_pairing.py.
if "pyautogui" not in sys.modules:
    _pg = types.ModuleType("pyautogui")
    _pg.FAILSAFE = False
    _pg.PAUSE = 0
    _pg.size = lambda: (1920, 1080)
    sys.modules["pyautogui"] = _pg
if "tools" not in sys.modules:
    _t = types.ModuleType("tools")
    _t.run_powershell = lambda cmd, timeout=60: {"ok": True, "ran": cmd}
    _t.press_key = lambda *a, **k: None
    _t.type_text = lambda *a, **k: None
    sys.modules["tools"] = _t

import key_vault as KV
import data_protect as DP
import ui

def _adp_methods():
    """Every ADP method on SettingsDialog, discovered rather than listed.

    A hard-coded list silently goes stale the moment a handler is added — the panel would build
    fine here while raising AttributeError in the real dialog.
    """
    return [n for n in dir(ui.SettingsDialog)
            if n.startswith(("_adp_", "_refresh_adp")) or n == "_populate_adp_section"]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["ember-adp-panel-tests"])


@pytest.fixture
def panel(qapp, monkeypatch, tmp_path):
    """A live ADP panel backed by a throwaway vault and recipient list."""
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "adp_recipients.json")
    import adp_watch
    monkeypatch.setattr(adp_watch, "CONFIG_FILE", tmp_path / "adp_watch.json")

    # Safety net: a modal exec() with nothing to click blocks the whole suite forever. Default
    # every dialog to "cancelled"; tests that need it accepted override this themselves.
    monkeypatch.setattr(ui.QDialog, "exec", lambda self: ui.QDialog.DialogCode.Rejected)

    host_cls = type("AdpHost", (QWidget,),
                    {n: getattr(ui.SettingsDialog, n) for n in _adp_methods()})
    host = host_cls()
    host._populate_adp_section(QVBoxLayout(host))
    return host


def test_off_state_says_who_can_read_your_data(panel):
    """The default state has to be honest about what it means, not a neutral 'disabled'."""
    text = panel._adp_status_lbl.text()
    assert "Off" in text and "readable by Apple" in text
    assert panel._adp_setup_btn.isEnabled()


def test_on_state_lists_who_can_decrypt(panel):
    assert DP.adp_setup("a good long passphrase")["ok"] is True
    panel._refresh_adp_status()
    text = panel._adp_status_lbl.text()
    assert "On (standard)" in text and "passphrase and 1 device(s)" in text
    # Once on, the primary button becomes "set up another phone" rather than going dead —
    # adding a second phone is the commonest next thing anyone wants.
    assert panel._adp_setup_btn.isEnabled()
    assert panel._adp_setup_btn.text() == "Set up another phone"


def test_new_device_appears_in_the_panel(panel):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    DP.adp_setup("a good long passphrase")
    DP.adp_add_recipient(DP.encode_pubkey(DP._pub_raw(X25519PrivateKey.generate())), "my iPad")
    panel._refresh_adp_status()
    assert "my iPad" in panel._adp_status_lbl.text()


def test_uk_banner_shows_and_clears_with_region(panel, monkeypatch):
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    panel._refresh_adp_status()
    assert "Home Office" in panel._adp_region_lbl.text()

    monkeypatch.setenv("LANG", "en_US.UTF-8")
    panel._refresh_adp_status()
    assert panel._adp_region_lbl.text() == ""


def test_report_surfaces_partial_failure(panel, monkeypatch):
    """A folder run that half-worked must not read as a clean success."""
    seen = {}
    monkeypatch.setattr(ui.QMessageBox, "warning",
                        lambda *a, **k: seen.update(warn=a[2]))
    monkeypatch.setattr(ui.QMessageBox, "information",
                        lambda *a, **k: seen.update(info=a[2]))
    panel._adp_report("T", {"ok": True, "protected_count": 2,
                            "failed": [{"path": "/x/a.jpg", "error": "boom"}],
                            "skipped_existing": ["/x/b.jpg"]},
                      "protected_count", "protected")
    assert "info" not in seen  # a warning, not an information box
    assert "2 file(s) done" in seen["warn"]
    assert "1 skipped" in seen["warn"] and "1 failed" in seen["warn"]
    assert "a.jpg: boom" in seen["warn"]


def test_report_is_quiet_when_everything_worked(panel, monkeypatch):
    seen = {}
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: seen.update(warn=a[2]))
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: seen.update(info=a[2]))
    panel._adp_report("T", {"ok": True, "restored_count": 3, "failed": [],
                            "skipped_existing": []}, "restored_count", "restored")
    assert "warn" not in seen and "3 file(s) done" in seen["info"]


def test_actions_refuse_before_setup(panel, monkeypatch):
    """Every action that needs a key must say so rather than opening a file picker."""
    seen = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: seen.append(a[2]))
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory",
                        lambda *a, **k: pytest.fail("asked for a folder before setup"))
    panel._adp_add_device()
    panel._adp_protect_folder()
    panel._adp_grant_access()
    assert len(seen) == 3
    assert all("Turn on data protection first" in s or "first" in s for s in seen)


def test_setup_aborts_when_the_two_entries_differ(panel, monkeypatch):
    typed = iter([("one passphrase", True), ("a different one", True)])
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: next(typed))
    warned = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(ui.QMessageBox, "question",
                        lambda *a, **k: pytest.fail("asked to confirm a mismatched passphrase"))
    panel._adp_setup()
    assert warned and "don't match" in warned[0]
    assert DP.is_set_up() is False


def test_setup_aborts_if_the_no_recovery_warning_is_declined(panel, monkeypatch):
    typed = iter([("a good long passphrase", True), ("a good long passphrase", True)])
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: next(typed))
    monkeypatch.setattr(ui.QMessageBox, "question",
                        lambda *a, **k: ui.QMessageBox.StandardButton.Cancel)
    panel._adp_setup()
    assert DP.is_set_up() is False


def test_setup_completes_when_confirmed(panel, monkeypatch):
    typed = iter([("a good long passphrase", True), ("a good long passphrase", True)])
    monkeypatch.setattr(ui.QInputDialog, "getText", lambda *a, **k: next(typed))
    monkeypatch.setattr(ui.QMessageBox, "question",
                        lambda *a, **k: ui.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: None)
    panel._adp_setup()
    assert DP.is_set_up() is True
    assert panel._adp_status_lbl.text().startswith("On (")


def test_shredding_needs_a_second_yes(panel, monkeypatch, tmp_path):
    """Ticking 'shred originals' in the options dialog must not be the last word."""
    DP.adp_setup("a good long passphrase")
    src = tmp_path / "pics"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"original bytes")

    dirs = iter([str(src), ""])
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *a, **k: next(dirs))
    # Accept the options dialog with "shred" ticked...
    def _exec(dlg):
        for c in dlg.findChildren(ui.QCheckBox):
            if "shred" in c.text():
                c.setChecked(True)
        return ui.QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui.QDialog, "exec", _exec)
    # ...then decline the confirmation.
    monkeypatch.setattr(ui.QMessageBox, "question",
                        lambda *a, **k: ui.QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a, **k: None)

    panel._adp_protect_folder()
    assert (src / "a.jpg").read_bytes() == b"original bytes"  # nothing shredded
    assert not (src / "a.jpg.ember").exists()                 # and nothing encrypted either


@pytest.mark.parametrize("platform,expected", [
    ("darwin", "this Mac"), ("win32", "this PC"), ("linux", "this computer"),
])
def test_device_wording_is_not_mac_only(monkeypatch, platform, expected):
    """Ember ships for macOS and Windows from one source; 'this Mac' on a PC reads as a bug."""
    monkeypatch.setattr(ui.sys, "platform", platform)
    assert ui.this_device() == expected
    assert ui.this_device(capitalized=True) == expected[0].upper() + expected[1:]


def test_panel_text_has_no_hard_coded_platform(panel):
    """The status line and setup prompts must go through the helper, not say 'Mac' outright."""
    src = open("ui.py", encoding="utf-8").read()
    body = src.split("def _populate_adp_section", 1)[1].split("def _adp_report", 1)[0]
    assert "this Mac" not in body and "this PC" not in body
    assert "this_device()" in body
    # And the live label reflects whatever platform the tests are running on.
    assert ui.this_device() in panel._adp_status_lbl.text() or "Off" in panel._adp_status_lbl.text()


def test_every_panel_handler_is_reachable():
    """Guard the discovery above: the panel must not reference a handler that doesn't exist."""
    src = open("ui.py", encoding="utf-8").read()
    body = src.split("def _populate_adp_section", 1)[1].split("def _adp_report", 1)[0]
    import re
    # Only call sites — `self._adp_status_lbl` is a widget attribute, not a handler.
    referenced = set(re.findall(r"self\.(_adp_[a-z_]+|_refresh_adp_[a-z_]+)\s*\(", body))
    defined = set(_adp_methods())
    assert referenced <= defined, referenced - defined


def test_watcher_state_shows_in_the_panel(panel, tmp_path):
    """The watcher line appears only once it is running — an "it's off" line about a feature
    nobody switched on is the clutter that made this panel hard to read."""
    import adp_watch
    assert panel._adp_watch_lbl.isVisible() is False
    DP.adp_setup("a good long passphrase")
    src = tmp_path / "import"
    src.mkdir()
    assert adp_watch.adp_watch_start(str(src))["ok"] is True
    try:
        panel._refresh_adp_watch_label()
        assert str(src) in panel._adp_watch_lbl.text()
    finally:
        adp_watch.stop_watching(persist=False)
    panel._refresh_adp_watch_label()
    assert panel._adp_watch_lbl.isVisible() is False


def test_phone_setup_refuses_before_protection(panel, monkeypatch):
    seen = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *a, **k: seen.append(a[2]))
    panel._adp_phone_setup()
    assert seen and "Turn on data protection first" in seen[0]


# --- the one-button flow --------------------------------------------------------
def test_the_panel_leads_with_one_button(panel):
    """The whole point of the simplification: one obvious action, everything else behind
    Advanced."""
    from PyQt6.QtWidgets import QPushButton
    labels = [b.text() for b in panel.findChildren(QPushButton)]
    assert labels[0] == "Protect my photos"          # one obvious primary action
    assert labels[1:] == ["Encryption…", "Advanced…"]  # everything else is a submenu
    assert len(labels) == 3


def test_quick_setup_does_everything_in_one_go(panel, monkeypatch):
    import phone_intake, remote_server
    monkeypatch.setattr(remote_server, "start",
                        lambda *a, **k: {"ok": True, "url": "http://192.168.1.5:8765"})
    monkeypatch.setattr(remote_server, "status", lambda: {"running": True})
    monkeypatch.setattr(remote_server, "magic_link",
                        lambda go="", public=False: f"http://192.168.1.5:8765/#tok=T&go={go}")
    # Yes to the "continue?" confirmation, No to "away from home Wi-Fi?" — answering Yes to the
    # second would spawn a real cloudflared tunnel and block.
    answers = iter([ui.QMessageBox.StandardButton.Yes, ui.QMessageBox.StandardButton.No])
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *a, **k: next(answers))
    shown = {}
    # Patch the HOST class, not SettingsDialog: the host copied the methods at class-creation
    # time, so patching the original has no effect on it.
    monkeypatch.setattr(type(panel), "_adp_show_phone_link",
                        lambda self, info: shown.update(info), raising=False)
    panel._adp_quick_setup()
    assert DP.is_set_up() is True                 # protection turned on
    assert shown["recovery_code"]                 # a code was produced
    assert "go=photos" in shown["link"]           # link opens the right tab
    assert shown["dest"]                          # a destination was chosen


def test_quick_setup_aborts_if_the_warning_is_declined(panel, monkeypatch):
    monkeypatch.setattr(ui.QMessageBox, "question",
                        lambda *a, **k: ui.QMessageBox.StandardButton.Cancel)
    panel._adp_quick_setup()
    assert DP.is_set_up() is False


def test_second_run_does_not_claim_a_new_recovery_code(panel, monkeypatch):
    """An existing passphrase cannot be recovered — the flow must not imply otherwise."""
    import remote_server, phone_intake
    DP.adp_setup("an existing passphrase")
    monkeypatch.setattr(remote_server, "status", lambda: {"running": True})
    monkeypatch.setattr(remote_server, "magic_link", lambda go="", public=False: "http://x/#tok=T")
    r = phone_intake.quick_setup()
    assert r["ok"] is True
    assert r["recovery_code"] is None and r["already_configured"] is True


# --- encryption levels ----------------------------------------------------------
def test_encryption_menu_offers_every_level(panel, monkeypatch, tmp_path):
    import data_protect
    monkeypatch.setattr(data_protect, "LEVEL_FILE", tmp_path / "level.json")
    from PyQt6.QtWidgets import QRadioButton
    seen = {}
    monkeypatch.setattr(ui.QDialog, "exec",
                        lambda self: seen.update(
                            labels=[r.text() for r in self.findChildren(QRadioButton)])
                        or ui.QDialog.DialogCode.Rejected)
    panel._adp_encryption_menu()
    assert seen["labels"] == ["Standard", "High", "Double"]


def test_encryption_menu_states_the_cost_of_each(panel, monkeypatch, tmp_path):
    """A strength picker that only says "stronger" pushes everyone to the slowest option for
    no reason. Every level must show what it costs."""
    import data_protect
    monkeypatch.setattr(data_protect, "LEVEL_FILE", tmp_path / "level.json")
    from PyQt6.QtWidgets import QLabel as QL
    seen = {}
    monkeypatch.setattr(ui.QDialog, "exec",
                        lambda self: seen.update(text=" ".join(l.text() for l in
                                                               self.findChildren(QL)))
                        or ui.QDialog.DialogCode.Rejected)
    panel._adp_encryption_menu()
    assert "Instant." in seen["text"]
    assert "twice the time" in seen["text"]
    # And it must not claim double is twice as strong.
    assert "Not twice the strength" in seen["text"]
