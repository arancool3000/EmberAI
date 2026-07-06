"""Maintainer helper to sign Ember's update manifest so the app can verify updates.

One-time setup (creates the keypair):

    python sign_release.py keygen
    #  -> writes update_privkey.pem  (KEEP OFFLINE / SECRET — gitignored)
    #  -> writes update_pubkey.pem   (COMMIT this; the app verifies against it)

Each release (after latest.json is generated), sign it:

    python sign_release.py sign docs/latest.json
    #  adds a "signature" field to the JSON, signed with your private key

The app (update_signing.check_manifest) then refuses any update whose manifest isn't validly
signed — closing the "compromised release channel" gap in the auto-updater.
"""
import json
import sys
from pathlib import Path

import update_signing

_PRIV = "update_privkey.pem"
_PUB = "update_pubkey.pem"


def _keygen() -> int:
    if not update_signing.available():
        print("cryptography is required: pip install cryptography", file=sys.stderr)
        return 1
    priv_path = Path(_PRIV)
    if priv_path.exists():
        print(f"refusing to overwrite existing {_PRIV} (delete it first if you really mean to)",
              file=sys.stderr)
        return 1
    private_pem, public_pem = update_signing.generate_keypair()
    priv_path.write_text(private_pem)
    try:
        import os
        os.chmod(_PRIV, 0o600)
    except Exception:
        pass
    Path(_PUB).write_text(public_pem)
    print(f"wrote {_PRIV} (SECRET — do not commit) and {_PUB} (commit this).")
    print("Add update_privkey.pem to .gitignore, then commit update_pubkey.pem.")
    return 0


def _sign(manifest_path: str) -> int:
    if not update_signing.available():
        print("cryptography is required: pip install cryptography", file=sys.stderr)
        return 1
    priv = Path(_PRIV)
    if not priv.exists():
        print(f"missing {_PRIV} — run 'python sign_release.py keygen' first", file=sys.stderr)
        return 1
    p = Path(manifest_path)
    manifest = json.loads(p.read_text())
    manifest.pop("signature", None)
    manifest["signature"] = update_signing.sign(manifest, priv.read_text())
    p.write_text(json.dumps(manifest, indent=2))
    # Sanity: verify what we just wrote against the public key.
    ok = update_signing.verify(manifest, Path(_PUB).read_text()) if Path(_PUB).exists() else None
    print(f"signed {manifest_path}" + ("  (verified ✓)" if ok else ""))
    return 0


def main(argv) -> int:
    if len(argv) >= 1 and argv[0] == "keygen":
        return _keygen()
    if len(argv) >= 2 and argv[0] == "sign":
        return _sign(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
