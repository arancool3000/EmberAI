"""Tests for the browser password manager (vault-backed)."""
import json

import key_vault
import browser_passwords as bp


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(key_vault, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(key_vault, "KEY_FILE", tmp_path / "vault.key")


def test_domain_normalisation():
    assert bp._domain("https://www.GitHub.com/login?x=1") == "github.com"
    assert bp._domain("http://user:pw@example.com:8443/path") == "example.com"
    assert bp._domain("EXAMPLE.org") == "example.org"
    assert bp._domain("") == ""


def test_save_get_list_delete(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert bp.save_login("https://www.example.com/login", "alice", "s3cret")
    got = bp.get_login("example.com")
    assert got["username"] == "alice" and got["password"] == "s3cret"
    # www + scheme variants resolve to the same domain
    assert bp.get_login("https://example.com/account")["password"] == "s3cret"
    assert "example.com" in bp.list_logins()
    assert bp.delete_login("example.com")
    assert bp.get_login("example.com") is None


def test_get_missing_returns_none(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert bp.get_login("nope.test") is None
    assert bp.list_logins() == []


def test_autofill_js_is_safe_and_embeds_values(tmp_path, monkeypatch):
    js = bp.autofill_js({"username": 'a"b', "password": "p'\\x"})
    # values are JSON-encoded, so the quotes are escaped, not breaking the JS string
    assert json.dumps('a"b') in js
    assert json.dumps("p'\\x") in js
    assert "input[type=password]" in js
