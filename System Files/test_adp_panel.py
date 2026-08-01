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

import pytest
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout

import key_vault as KV
import data_protect as DP
import ui

_ADP_METHODS = ("_populate_adp_section", "_refresh_adp_status", "_adp_setup",
                "_adp_show_identity", "_adp_add_device", "_adp_protect_folder",
                "_adp_unprotect_folder", "_adp_grant_access", "_adp_report")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication(["ember-adp-panel-tests"])


@pytest.fixture
def panel(qapp, monkeypatch, tmp_path):
    """A live ADP panel backed by a throwaway vault and recipient list."""
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "adp_recipients.json")

    host_cls = type("AdpHost", (QWidget,),
                    {n: getattr(ui.SettingsDialog, n) for n in _ADP_METHODS})
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
    assert "On —" in text and "passphrase and 1 device(s)" in text
    # Nothing to turn on twice; setup refuses anyway, but the button shouldn't invite it.
    assert not panel._adp_setup_btn.isEnabled()


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
    assert "On —" in panel._adp_status_lbl.text()


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
