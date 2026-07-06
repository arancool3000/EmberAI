"""Cryptographic signing for Ember's auto-update manifest (latest.json).

The updater already fetches over verified HTTPS, pins the download host to github.com, and
checks the SHA-256 of the payload. The remaining gap (called out in SECURITY.md) is that the
hash and the file come from the *same* channel — so a compromised GitHub account could serve
a malicious build plus a matching hash. This module closes that with an offline signature:

  * The maintainer generates an Ed25519 keypair ONCE (generate_keypair), keeps the private key
    offline, and commits the PUBLIC key to the repo as `update_pubkey.pem`.
  * Each release signs latest.json with the private key (sign / the sign_release.py helper).
  * Ember verifies the manifest signature against the bundled public key before installing.

Safety by design: verification is ENFORCED only when a public key is bundled AND the manifest
carries a signature. With no bundled key (the default today) this module is inert, so it can
never break the existing update flow — it only starts protecting once the maintainer opts in.

`cryptography` is imported lazily; if it's unavailable the signing/verify calls report that
cleanly instead of raising, and the updater treats "can't verify" conservatively (below).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

_PUBKEY_FILENAME = "update_pubkey.pem"


def available() -> bool:
    """True if the crypto backend needed for Ed25519 is importable. Catches BaseException, not
    just Exception: a broken cryptography install can raise pyo3 PanicException (a BaseException),
    and this must degrade to 'unavailable', never crash the caller."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: F401
        return True
    except BaseException:
        return False


def canonical_bytes(manifest: dict) -> bytes:
    """Deterministic serialization of the manifest for signing — every field EXCEPT the
    signature itself, with sorted keys so signer and verifier agree byte-for-byte."""
    body = {k: v for k, v in (manifest or {}).items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_pem) as PEM strings. Run once; keep the private key offline."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def sign(manifest: dict, private_pem: str | bytes) -> str:
    """Return a base64 Ed25519 signature over the manifest's canonical bytes."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    if isinstance(private_pem, str):
        private_pem = private_pem.encode("ascii")
    key = load_pem_private_key(private_pem, password=None)
    sig = key.sign(canonical_bytes(manifest))
    return base64.b64encode(sig).decode("ascii")


def verify(manifest: dict, public_pem: str | bytes) -> bool:
    """True iff manifest['signature'] is a valid Ed25519 signature over the manifest."""
    sig_b64 = (manifest or {}).get("signature")
    if not sig_b64 or not public_pem:
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        if isinstance(public_pem, str):
            public_pem = public_pem.encode("ascii")
        key = load_pem_public_key(public_pem)
        key.verify(base64.b64decode(sig_b64), canonical_bytes(manifest))
        return True
    except BaseException:
        return False


def bundled_public_key() -> str | None:
    """The public key shipped with the app (repo `update_pubkey.pem`, or the EMBER_UPDATE_PUBKEY
    env var). None when the maintainer hasn't set up signing yet — in which case verification is
    not enforced."""
    env = os.environ.get("EMBER_UPDATE_PUBKEY")
    if env and "BEGIN PUBLIC KEY" in env:
        return env
    try:
        p = Path(__file__).resolve().parent / _PUBKEY_FILENAME
        if p.exists():
            text = p.read_text().strip()
            if "BEGIN PUBLIC KEY" in text:
                return text
    except Exception:
        pass
    return None


def signing_enforced() -> bool:
    """True when a public key is bundled — i.e. updates MUST carry a valid signature."""
    return bundled_public_key() is not None


def check_manifest(manifest: dict) -> tuple[bool, str]:
    """The updater's gate. Returns (ok_to_install, reason).

    - No bundled public key  -> (True, "signing not configured")  [inert; today's behavior]
    - Bundled key but crypto missing -> (False, ...)  [fail closed: we said we'd verify, can't]
    - Bundled key + valid signature  -> (True, "signature verified")
    - Bundled key + missing/invalid signature -> (False, ...)
    """
    pub = bundled_public_key()
    if not pub:
        return True, "update signing not configured (no bundled public key)"
    if not available():
        return False, ("update is signed-only, but the crypto library is unavailable to verify "
                       "it — refusing to install")
    if not manifest.get("signature"):
        return False, "update manifest is not signed — refusing to install"
    if verify(manifest, pub):
        return True, "update signature verified"
    return False, "update signature is INVALID — refusing to install (possible tampering)"
