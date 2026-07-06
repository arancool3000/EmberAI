"""Hermetic tests for update_signing.py. The pure parts (canonical bytes, the inert-by-default
gate) run everywhere; the Ed25519 keygen/sign/verify roundtrip runs only when `cryptography` is
importable (it isn't in every CI sandbox), so it's skipped gracefully otherwise.

Run: python test_update_signing.py
"""
import update_signing as us


# ---- pure: canonicalization + the default-inert gate ----------------------

def test_canonical_excludes_signature_and_is_stable():
    m1 = {"version": "1.2.3", "downloads": {"macos": {"sha256": "abc"}}, "signature": "XXX"}
    m2 = {"downloads": {"macos": {"sha256": "abc"}}, "version": "1.2.3"}  # no sig, diff order
    assert us.canonical_bytes(m1) == us.canonical_bytes(m2)   # signature + key order don't matter


def test_gate_inert_without_bundled_key(monkeypatch=None):
    # No public key bundled -> updates are allowed (today's behavior, nothing breaks).
    orig = us.bundled_public_key
    us.bundled_public_key = lambda: None
    try:
        ok, reason = us.check_manifest({"version": "9.9.9"})
        assert ok is True and "not configured" in reason
        assert us.signing_enforced() is False
    finally:
        us.bundled_public_key = orig


def test_gate_rejects_unsigned_when_key_present():
    orig = us.bundled_public_key
    us.bundled_public_key = lambda: "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----"
    try:
        # crypto may be absent -> fail-closed reason; present -> "not signed". Either way, blocked.
        ok, reason = us.check_manifest({"version": "9.9.9"})   # no signature field
        assert ok is False
    finally:
        us.bundled_public_key = orig


# ---- Ed25519 roundtrip (only when cryptography is available) ---------------

def test_sign_verify_roundtrip_and_tamper_detection():
    if not us.available():
        print("    (skipped: cryptography not importable in this environment)")
        return
    priv, pub = us.generate_keypair()
    manifest = {"version": "2.0.0", "downloads": {"macos": {"sha256": "deadbeef"}}}
    manifest["signature"] = us.sign(manifest, priv)

    assert us.verify(manifest, pub) is True

    # Tamper with a signed field -> verification must fail.
    tampered = dict(manifest)
    tampered["downloads"] = {"macos": {"sha256": "0000"}}
    assert us.verify(tampered, pub) is False

    # A different key must not validate.
    _priv2, pub2 = us.generate_keypair()
    assert us.verify(manifest, pub2) is False

    # And the full gate accepts a validly-signed manifest when that key is bundled.
    orig = us.bundled_public_key
    us.bundled_public_key = lambda: pub
    try:
        ok, reason = us.check_manifest(manifest)
        assert ok is True and "verified" in reason
    finally:
        us.bundled_public_key = orig


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} update signing tests passed")
