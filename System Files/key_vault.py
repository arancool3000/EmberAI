"""Encrypted key vault for Ember — store API keys/secrets encrypted instead of
plaintext in settings.json.

Backends (in priority order):
  1. OS keychain via the optional `keyring` package (service name "EmberAI").
     This is the STRONGEST option — secrets live in the macOS Keychain / Windows
     Credential Manager / Secret Service and never touch Ember's data dir.
  2. An encrypted file `vault.enc` in the data dir, sealed with
     cryptography.fernet.Fernet (AES-128-CBC + HMAC). The Fernet master key is
     written to a sibling file `vault.key` with 0600 permissions (best-effort —
     os.chmod failures, e.g. on Windows, are ignored).

The encrypted-file backend is "encrypted at rest": the secrets are unreadable
without `vault.key`. It is, however, WEAKER than the keychain backend, since the
master key sits next to the ciphertext on disk. Prefer the keychain backend
(install `keyring`) where possible.

`keyring` is imported LAZILY inside functions and is fully optional — the module
imports and works with only the standard library + `cryptography` (+ `requests`,
imported by the wider toolkit). All tool functions return a dict and never raise.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

_SERVICE = "EmberAI"


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


# Module-level so tests can monkeypatch them to a temp dir.
VAULT_FILE = _data_dir() / "vault.enc"
KEY_FILE = _data_dir() / "vault.key"

# Index of key names kept inside the keychain so list_keys() works there too.
_KEYRING_INDEX = "__ember_vault_index__"


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
def _keyring():
    """Return the keyring module if it's importable AND a real backend is
    available, else None. Imported lazily so the module loads without it."""
    try:
        import keyring  # type: ignore
        from keyring.backends import fail as _fail  # type: ignore
        kr = keyring.get_keyring()
        if isinstance(kr, _fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def backend() -> str:
    """'keychain' if the OS keychain is usable, else 'encrypted-file'."""
    return "keychain" if _keyring() is not None else "encrypted-file"


# ---------------------------------------------------------------------------
# Encrypted-file backend internals
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    """Load (or create) the Fernet master key from KEY_FILE."""
    kf = Path(KEY_FILE)
    if kf.exists():
        key = kf.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        kf.parent.mkdir(parents=True, exist_ok=True)
        kf.write_bytes(key)
        try:
            os.chmod(kf, 0o600)
        except OSError:
            pass  # e.g. Windows — best-effort only
    return Fernet(key)


class VaultDecryptError(Exception):
    """The vault file exists but can't be decrypted (corrupt/truncated ciphertext, or the master
    key file was lost/replaced). Distinguished from 'no vault yet' so a write never treats an
    unreadable vault as empty and overwrites every secret."""


def _read_file_vault(strict: bool = False) -> dict:
    vf = Path(VAULT_FILE)
    if not vf.exists():
        return {}
    try:
        raw = _fernet().decrypt(vf.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # A read (get/list) can safely fall back to "empty" — worst case a secret reads as
        # missing. A WRITE must not: see strict=True in set_key.
        if strict:
            raise VaultDecryptError(str(e))
        return {}


def _backup_corrupt_vault() -> Path | None:
    """Preserve an undecryptable vault.enc as vault.enc.corrupt so a later key-file recovery can
    still get the secrets back, instead of them being silently overwritten. Returns the backup
    path (or None). Won't clobber an existing backup."""
    vf = Path(VAULT_FILE)
    try:
        if not vf.exists():
            return None
        bak = vf.with_suffix(vf.suffix + ".corrupt")
        if not bak.exists():
            bak.write_bytes(vf.read_bytes())
        return bak
    except OSError:
        return None


def _write_file_vault(data: dict) -> None:
    vf = Path(VAULT_FILE)
    vf.parent.mkdir(parents=True, exist_ok=True)
    token = _fernet().encrypt(json.dumps(data).encode("utf-8"))
    vf.write_bytes(token)
    try:
        os.chmod(vf, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Keychain backend index (so we can enumerate keys we stored)
# ---------------------------------------------------------------------------
def _kr_index_load(kr) -> list[str]:
    try:
        raw = kr.get_password(_SERVICE, _KEYRING_INDEX)
        names = json.loads(raw) if raw else []
        return [str(n) for n in names] if isinstance(names, list) else []
    except Exception:
        return []


def _kr_index_save(kr, names: list[str]) -> None:
    kr.set_password(_SERVICE, _KEYRING_INDEX, json.dumps(sorted(set(names))))


# ---------------------------------------------------------------------------
# Plain helpers (for ui.py to call directly — NOT exposed as tools).
# These return real secrets where relevant and are not LLM-facing.
# ---------------------------------------------------------------------------
def set_key(name: str, value: str) -> bool:
    """Store a secret under `name`. Returns True on success."""
    if not name:
        return False
    name = str(name)
    value = "" if value is None else str(value)
    kr = _keyring()
    try:
        if kr is not None:
            kr.set_password(_SERVICE, name, value)
            idx = _kr_index_load(kr)
            if name not in idx:
                idx.append(name)
                _kr_index_save(kr, idx)
            return True
        # strict: if the existing vault can't be decrypted, DO NOT proceed — writing here would
        # re-encrypt a dict containing only this one key and destroy every other stored secret.
        # Back the ciphertext up and refuse, so the user (or a restored key file) can recover.
        try:
            data = _read_file_vault(strict=True)
        except VaultDecryptError:
            _backup_corrupt_vault()
            return False
        data[name] = value
        _write_file_vault(data)
        return True
    except Exception:
        return False


def get_key(name: str) -> str | None:
    """Return the real secret for `name`, or None if missing. ui.py only."""
    if not name:
        return None
    name = str(name)
    kr = _keyring()
    try:
        if kr is not None:
            return kr.get_password(_SERVICE, name)
        return _read_file_vault().get(name)
    except Exception:
        return None


def delete_key(name: str) -> bool:
    """Remove `name`. Returns True if it existed and was removed."""
    if not name:
        return False
    name = str(name)
    kr = _keyring()
    try:
        if kr is not None:
            if kr.get_password(_SERVICE, name) is None:
                return False
            kr.delete_password(_SERVICE, name)
            idx = _kr_index_load(kr)
            if name in idx:
                idx.remove(name)
                _kr_index_save(kr, idx)
            return True
        data = _read_file_vault()
        if name not in data:
            return False
        del data[name]
        _write_file_vault(data)
        return True
    except Exception:
        return False


def list_keys() -> list[str]:
    """Return the sorted list of stored key names (never values)."""
    kr = _keyring()
    try:
        if kr is not None:
            return sorted(_kr_index_load(kr))
        return sorted(_read_file_vault().keys())
    except Exception:
        return []


def _mask(value: str) -> str:
    """Mask a secret, showing only the last 4 characters."""
    v = value or ""
    tail = v[-4:] if len(v) >= 4 else v
    return "••••••" + tail


# ---------------------------------------------------------------------------
# Tools (exposed to the LLM). Each returns a dict; never leaks a full secret.
# ---------------------------------------------------------------------------
def vault_status() -> dict:
    """Report the active vault backend and the names of stored keys (no values)."""
    try:
        names = list_keys()
        return {"ok": True, "backend": backend(), "key_count": len(names), "keys": names}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def vault_store_key(name: str, value: str) -> dict:
    """Store an API key/secret in the vault."""
    if not name or not value:
        return {"ok": False, "error": "name and value are required"}
    try:
        if set_key(name, value):
            return {"ok": True, "name": str(name), "backend": backend()}
        return {"ok": False, "error": "failed to store key"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def vault_get_key(name: str) -> dict:
    """Check a stored key. Returns a masked preview (last 4 chars), never the full secret."""
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        val = get_key(name)
        if val is None:
            return {"ok": True, "name": str(name), "exists": False, "masked": None}
        return {"ok": True, "name": str(name), "exists": True, "masked": _mask(val)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def vault_delete_key(name: str) -> dict:
    """Delete a stored key from the vault."""
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        return {"ok": True, "name": str(name), "deleted": delete_key(name)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def vault_list_keys() -> dict:
    """List the names of all stored keys (never values)."""
    try:
        return {"ok": True, "keys": list_keys()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "vault_status",
        "description": "Report the key vault backend (OS keychain or encrypted file) and the names of stored keys (never the secret values).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "vault_store_key",
        "description": "Store an API key or secret in the encrypted key vault (OS keychain or encrypted file).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "key name, e.g. gemini_api_key"},
                "value": {"type": "STRING", "description": "the secret value"},
            },
            "required": ["name", "value"],
        },
    },
    {
        "name": "vault_get_key",
        "description": "Check whether a key exists in the vault and get a masked preview (only the last 4 characters). Never returns the full secret.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "key name, e.g. gemini_api_key"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "vault_delete_key",
        "description": "Delete a key from the encrypted key vault.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "key name to remove"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "vault_list_keys",
        "description": "List the names of all keys stored in the vault (never the secret values).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH = {
    "vault_status": vault_status,
    "vault_store_key": vault_store_key,
    "vault_get_key": vault_get_key,
    "vault_delete_key": vault_delete_key,
    "vault_list_keys": vault_list_keys,
}

READONLY_TOOLS = {"vault_status", "vault_get_key", "vault_list_keys"}
INTERACTION_TOOLS = {"vault_store_key", "vault_delete_key"}
