"""Tests for key_vault — exercises the encrypted-file backend and the LLM tools.

VAULT_FILE / KEY_FILE are monkeypatched to a tmp_path so the real data dir is
never touched. `keyring` is absent in this environment, so backend() resolves to
'encrypted-file' and these tests drive the Fernet-encrypted-file path."""
import key_vault as KV


def _isolate(monkeypatch, tmp_path):
    """Point the vault at a throwaway temp dir."""
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")


def test_encrypted_file_backend_active():
    # keyring is not installed here, so the file backend must be selected.
    assert KV.backend() == "encrypted-file"


def test_file_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert KV.list_keys() == []
    assert KV.set_key("gemini_api_key", "sk-secret-1234") is True
    assert KV.set_key("openai_api_key", "op-9999") is True
    assert KV.get_key("gemini_api_key") == "sk-secret-1234"
    assert KV.list_keys() == ["gemini_api_key", "openai_api_key"]
    # The on-disk file must be encrypted (not contain the plaintext secret).
    blob = (tmp_path / "vault.enc").read_bytes()
    assert b"sk-secret-1234" not in blob
    assert KV.delete_key("gemini_api_key") is True
    assert KV.get_key("gemini_api_key") is None
    assert KV.list_keys() == ["openai_api_key"]


def test_keyfile_permissions(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    KV.set_key("x", "y")
    kf = tmp_path / "vault.key"
    assert kf.exists()
    import os
    import stat
    # Best-effort 0600; only the owner bits should be set on POSIX.
    if os.name == "posix":
        mode = stat.S_IMODE(kf.stat().st_mode)
        assert mode == 0o600


def test_store_then_get_is_masked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    stored = KV.vault_store_key("my_key", "ABCDEFG1234")
    assert stored["ok"] is True
    assert stored["name"] == "my_key"
    assert stored["backend"] == "encrypted-file"

    got = KV.vault_get_key("my_key")
    assert got["ok"] is True
    assert got["exists"] is True
    assert got["masked"] == "••••••1234"
    # The full secret must NEVER appear in the tool result.
    assert "ABCDEFG1234" not in str(got)
    assert "ABCDEFG" not in got["masked"]


def test_status_reports_names_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    KV.vault_store_key("a_key", "value-aaaa")
    KV.vault_store_key("b_key", "value-bbbb")
    st = KV.vault_status()
    assert st["ok"] is True
    assert st["backend"] == "encrypted-file"
    assert st["key_count"] == 2
    assert sorted(st["keys"]) == ["a_key", "b_key"]
    # Values must not leak through status.
    assert "value-aaaa" not in str(st)
    assert "value-bbbb" not in str(st)


def test_list_keys_tool(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert KV.vault_list_keys() == {"ok": True, "keys": []}
    KV.vault_store_key("k1", "s1secret")
    assert KV.vault_list_keys() == {"ok": True, "keys": ["k1"]}


def test_missing_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert KV.get_key("nope") is None
    got = KV.vault_get_key("nope")
    assert got["ok"] is True
    assert got["exists"] is False
    assert got["masked"] is None


def test_delete_missing_returns_false(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert KV.delete_key("ghost") is False
    res = KV.vault_delete_key("ghost")
    assert res["ok"] is True
    assert res["deleted"] is False


def test_store_requires_args(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert KV.vault_store_key("", "v")["ok"] is False
    assert KV.vault_store_key("n", "")["ok"] is False


def test_wiring_exports_consistent():
    assert set(KV.TOOL_DISPATCH) == {d["name"] for d in KV.TOOL_DECLARATIONS}
    assert KV.READONLY_TOOLS == {"vault_status", "vault_get_key", "vault_list_keys"}
    assert KV.INTERACTION_TOOLS == {"vault_store_key", "vault_delete_key"}
    # Read-only and write tools are disjoint and cover the whole dispatch table.
    assert KV.READONLY_TOOLS.isdisjoint(KV.INTERACTION_TOOLS)
    assert KV.READONLY_TOOLS | KV.INTERACTION_TOOLS == set(KV.TOOL_DISPATCH)
    # All declared types are uppercase scalars.
    for d in KV.TOOL_DECLARATIONS:
        for prop in d["parameters"]["properties"].values():
            assert prop["type"] in {"STRING", "INTEGER", "NUMBER", "BOOLEAN"}
