"""Tests for API-key resolution when the encrypted key vault is on.

The reported bug: `describe_image` returned "Add a Gemini API key in Ember Settings" while the
very same session was calling gemini-3.1-flash-lite successfully (the transcript showed it being
rate-limited on that model). The user described it as having and not having a key at once.

Cause: with the key vault enabled — which is the default — `ui.save_settings` moves API keys
into the vault and writes settings.json with those keys BLANKED. `ui.load_settings` hydrates
them back into the in-memory dict, so the agent works. But `creative.py` read settings.json
straight off disk, saw an empty key, and told the user to add one.
"""
import json
import os
import tempfile

import pytest

import app_data
import key_vault as KV


@pytest.fixture
def vaulted(monkeypatch, tmp_path):
    """A machine configured exactly as the default install: vault on, key in the vault,
    settings.json holding a blank."""
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    (tmp_path / "settings.json").write_text(json.dumps({
        "use_key_vault": True,
        "gemini_api_key": "",          # blanked on disk, exactly as save_settings writes it
        "anthropic_api_key": "",
    }))
    assert KV.set_key("gemini_api_key", "AIza-the-real-key") is True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


# --- the bug --------------------------------------------------------------------
def test_the_key_is_found_when_it_lives_in_the_vault(vaulted):
    assert app_data.settings_with_keys().get("gemini_api_key") == "AIza-the-real-key"


def test_reading_the_file_alone_is_what_missed_it(vaulted):
    """The old behaviour, kept as a named function so the difference is visible rather than
    implied."""
    assert app_data.read_settings().get("gemini_api_key") == ""


def test_creative_tools_see_the_vaulted_key(vaulted):
    import creative
    assert creative._gemini_key() == "AIza-the-real-key"


def test_describe_image_no_longer_claims_the_key_is_missing(vaulted, tmp_path):
    """The exact failing call from the report. It may still fail — there is no network here —
    but it must not fail by claiming there is no key."""
    import creative
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    r = creative.describe_image(str(img), "what is this?")
    assert "Add a Gemini API key" not in (r.get("error") or "")


# --- the surrounding behaviour ---------------------------------------------------
def test_a_key_in_settings_still_wins_without_a_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({
        "use_key_vault": False, "gemini_api_key": "plain-key"}))
    assert app_data.settings_with_keys()["gemini_api_key"] == "plain-key"


def test_the_environment_still_works_headless(monkeypatch, tmp_path):
    """A CI or MCP run with no settings file at all."""
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    import creative
    assert creative._gemini_key() == "env-key"


def test_a_vault_key_beats_the_environment(vaulted, monkeypatch):
    """What the user configured in the app should win over a stray shell variable."""
    monkeypatch.setenv("GEMINI_API_KEY", "stale-env-key")
    import creative
    assert creative._gemini_key() == "AIza-the-real-key"


def test_whitespace_in_a_pasted_key_is_stripped(monkeypatch, tmp_path):
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    (tmp_path / "settings.json").write_text(json.dumps({"use_key_vault": True}))
    KV.set_key("gemini_api_key", "  AIza-with spaces\n")
    import creative
    assert creative._gemini_key() == "AIza-withspaces"


def test_a_missing_settings_file_is_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    assert app_data.settings_with_keys() == {} or isinstance(app_data.settings_with_keys(), dict)


def test_corrupt_settings_do_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(app_data, "data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{not json at all")
    assert app_data.read_settings() == {}


def test_every_vaulted_key_is_hydrated(vaulted):
    """Not just Gemini — the same blanking applies to every key in the list, so a partial fix
    would leave Claude, OpenAI and the mail password broken in the same way."""
    for name in app_data.VAULT_KEYS:
        KV.set_key(name, f"value-of-{name}")
    resolved = app_data.settings_with_keys()
    for name in app_data.VAULT_KEYS:
        assert resolved.get(name) == f"value-of-{name}", name


def test_the_vault_key_list_matches_the_one_ui_writes():
    """These two lists must not drift: ui blanks what it stores, app_data restores what it
    knows about, and a key in one list but not the other silently disappears."""
    ui_src = open("ui.py", encoding="utf-8").read()
    block = ui_src.split("_VAULT_KEYS = (", 1)[1].split(")", 1)[0]
    ui_keys = {p.strip().strip('"\'') for p in block.split(",") if p.strip()}
    assert ui_keys == set(app_data.VAULT_KEYS)
