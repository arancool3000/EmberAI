"""Hermetic regression tests for key_vault's corrupt-vault protection.

The bug: _read_file_vault() caught ALL exceptions and returned {} — so a vault whose ciphertext
couldn't be decrypted (lost/replaced master key, truncated vault.enc) looked EMPTY, and the next
set_key() would re-encrypt a dict with only the new key, silently destroying every other secret.

A fake `cryptography.fernet.Fernet` is injected before importing key_vault (real cryptography
needs a native backend CI lacks). The fake models Fernet's KEY-SCOPING: ciphertext is tagged
with the key, and decrypt() raises unless the SAME key is presented — so writing a different
vault.key faithfully reproduces "the vault can no longer be decrypted".
Run: python test_key_vault_recovery.py
"""
import sys
import tempfile
import types
from pathlib import Path

# --- fake Fernet, injected before key_vault imports it ----------------------
if "cryptography.fernet" not in sys.modules:
    _c = types.ModuleType("cryptography")
    _cf = types.ModuleType("cryptography.fernet")

    class _FakeFernet:
        _counter = [0]

        def __init__(self, key):
            self.key = bytes(key)

        @staticmethod
        def generate_key():
            _FakeFernet._counter[0] += 1
            return f"fake-key-{_FakeFernet._counter[0]:08d}".encode()

        def encrypt(self, data: bytes) -> bytes:
            return b"ENC:" + self.key + b":" + data

        def decrypt(self, token: bytes) -> bytes:
            prefix = b"ENC:" + self.key + b":"
            if not token.startswith(prefix):
                raise ValueError("invalid token / wrong key")
            return token[len(prefix):]

    _cf.Fernet = _FakeFernet
    _c.fernet = _cf
    sys.modules["cryptography"] = _c
    sys.modules["cryptography.fernet"] = _cf

import key_vault as KV  # noqa: E402  (fake crypto injected above first)


def _fresh_dir():
    d = Path(tempfile.mkdtemp(prefix="ember_vault_rec_"))
    KV.VAULT_FILE = d / "vault.enc"
    KV.KEY_FILE = d / "vault.key"
    return d


def _newkey():
    return KV.Fernet.generate_key()


def test_healthy_vault_still_roundtrips():
    _fresh_dir()
    assert KV.set_key("a", "aaaa1111") is True
    assert KV.set_key("b", "bbbb2222") is True
    assert KV.get_key("a") == "aaaa1111"
    assert sorted(KV.list_keys()) == ["a", "b"]


def test_missing_vault_is_not_treated_as_corrupt():
    _fresh_dir()
    # No vault file yet -> the very first write must SUCCEED (empty IS fine when the file is
    # absent; only a decrypt FAILURE on an existing file is the danger).
    assert KV.set_key("first", "value-1234") is True
    assert KV.get_key("first") == "value-1234"


def test_corrupt_vault_write_refuses_instead_of_wiping_secrets():
    d = _fresh_dir()
    KV.set_key("a", "aaaa1111")
    KV.set_key("b", "bbbb2222")
    (d / "vault.key").write_bytes(_newkey())        # master key lost/replaced
    assert KV.get_key("a") is None                  # can't decrypt -> reads as missing, no crash
    assert KV.set_key("c", "cccc3333") is False     # WRITE must refuse, not clobber
    assert (d / "vault.enc.corrupt").exists()        # original preserved for recovery


def test_corrupt_backup_is_the_original_ciphertext():
    d = _fresh_dir()
    KV.set_key("a", "aaaa1111")
    original = (d / "vault.enc").read_bytes()
    (d / "vault.key").write_bytes(_newkey())
    KV.set_key("c", "cccc3333")                     # triggers backup + refusal
    assert (d / "vault.enc.corrupt").read_bytes() == original


def test_recovery_after_restoring_the_key_file():
    d = _fresh_dir()
    KV.set_key("a", "aaaa1111")
    good_key = (d / "vault.key").read_bytes()
    (d / "vault.key").write_bytes(_newkey())        # break decryption
    assert KV.set_key("c", "cccc3333") is False     # refused, nothing destroyed
    (d / "vault.key").write_bytes(good_key)         # user restores the real key file
    assert KV.get_key("a") == "aaaa1111"            # original secret intact
    assert KV.set_key("c", "cccc3333") is True      # writes work again
    assert sorted(KV.list_keys()) == ["a", "c"]


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
