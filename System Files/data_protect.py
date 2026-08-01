"""Advanced Data Protection — client-side encryption for anything before it reaches the cloud.

Why this exists
---------------
iCloud is encrypted in transit and at rest, but by default Apple holds the keys: the data is
*not* end-to-end encrypted unless the user turns on Apple's own "Advanced Data Protection"
(iOS 16.2+). Even with ADP on, the protection starts at Apple's boundary — the plaintext still
leaves the device under Apple's key hierarchy.

And that setting can be taken away. In February 2025 Apple withdrew ADP from the United Kingdom
rather than comply with a secret Technical Capability Notice served by the Home Office under the
Investigatory Powers Act 2016 — a notice Apple was legally gagged from disclosing. A first,
worldwide-scoped notice was dropped in August 2025; a second, narrowed to UK users, followed
that October, and the resulting challenges are before the Investigatory Powers Tribunal. UK
users still cannot enable ADP.

That is the design argument for doing this locally. A cloud provider's strongest setting is
subject to jurisdiction and can be revoked without the user being told; a key that never leaves
the user's own machines cannot be served with a notice. For a UK user this module is not a
second layer under Apple's — it is the only end-to-end layer available at all.

Multi-device and multi-user
---------------------------
Every file gets its own random content key (CEK). The CEK is then *wrapped* separately for each
recipient, so one encrypted file can be opened by any of them:

* **the passphrase** — always included, so the data is recoverable with something the user
  remembers even if every device is lost;
* **each enrolled device or person** — identified by an X25519 public key.

Adding a device means exchanging public keys, not sharing the passphrase, and the recipient list
holds only public keys, so it is safe to sync through iCloud itself. Any enrolled device can both
encrypt (for everyone) and decrypt. `adp_grant_access` re-wraps files already encrypted so a
newly-added device can read the back catalogue without the payload being re-encrypted.

Honest limits (read before relying on this)
-------------------------------------------
* This does NOT encrypt an existing iCloud Photos *library*. iCloud Photos only syncs real image
  files; an encrypted blob isn't one. Export photos out of Photos, protect them into a synced
  *folder* (iCloud Drive), then delete the originals. `adp_icloud_targets` reports which paths
  on this machine are usable.
* Anything already uploaded stays uploaded. This protects data going forward.
* Removing a recipient stops them receiving *new* files. It cannot un-see what they could already
  decrypt, and anyone holding an old copy of a file keeps their access to that copy.
* Lose the passphrase *and* every enrolled device and the data is gone. There is no recovery.
* Encrypted files no longer preview in Finder or Photos — that is the point.

Crypto
------
Payload: Fernet (AES-128-CBC + HMAC-SHA256) under a random per-file CEK.
Passphrase wrap: PBKDF2-HMAC-SHA256, 600,000 iterations, fresh 16-byte salt per file.
Recipient wrap: X25519 ECDH to an ephemeral key, HKDF-SHA256 over the shared secret and both
public keys, then Fernet-wrapping the CEK.

File layout (v2)::

    b"EMBERADP2" | uint32be header_len | header JSON | Fernet token

The header carries only wrapped keys and public values. It is not itself authenticated, but the
payload is: tampering with the header can at worst cause unwrapping to fail or produce a wrong
CEK, and the body's HMAC then rejects it. No forged plaintext can result.

Files written by the earlier v1 format (``EMBERADP1``, passphrase-only) still decrypt.

Secrets live in ``key_vault`` (OS keychain where available): the passphrase under
``adp_passphrase`` and this machine's X25519 private key under ``adp_device_key``. No tool in
this module ever returns either one.

Every tool returns a dict and never raises.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC_V1 = b"EMBERADP1"
MAGIC = b"EMBERADP2"
SALT_LEN = 16
KDF_ITERATIONS = 600_000
EXT = ".ember"
HKDF_INFO = b"ember-adp-v2-keywrap"
PUBKEY_PREFIX = "ember1:"

VAULT_KEY = "adp_passphrase"
DEVICE_KEY = "adp_device_key"


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


#: Module-level so tests can point it at a temp dir.
RECIPIENTS_FILE = _data_dir() / "adp_recipients.json"

#: Files `adp_protect_folder` treats as photos when `all_files` is False (the default).
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".webp", ".bmp", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf",
    ".rw2", ".raf", ".mov", ".mp4", ".m4v", ".avi",
}

#: Never encrypt Ember's own key material — protecting the vault with a key stored inside it
#: would be unrecoverable.
SKIP_NAMES = {"vault.enc", "vault.key", "vault.enc.corrupt", "adp_recipients.json"}

#: Hard ceiling per file (bytes). Fernet holds the whole payload in memory, so a multi-gigabyte
#: file would exhaust RAM rather than fail cleanly.
MAX_FILE_BYTES = 512 * 1024 * 1024


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def _passphrase() -> str | None:
    """Fetch the stored passphrase. Never exposed through a tool result."""
    try:
        import key_vault
        return key_vault.get_key(VAULT_KEY) or None
    except Exception:
        return None


def is_set_up() -> bool:
    """True if a protection passphrase has been stored."""
    return _passphrase() is not None


def _device_private() -> X25519PrivateKey | None:
    """This machine's X25519 private key, or None if no identity exists yet."""
    try:
        import key_vault
        raw = key_vault.get_key(DEVICE_KEY)
        if not raw:
            return None
        return X25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    except Exception:
        return None


def _ensure_device_key() -> X25519PrivateKey | None:
    """Return this machine's identity key, creating it on first use."""
    priv = _device_private()
    if priv is not None:
        return priv
    try:
        import key_vault
        priv = X25519PrivateKey.generate()
        raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        if not key_vault.set_key(DEVICE_KEY, base64.b64encode(raw).decode()):
            return None
        return priv
    except Exception:
        return None


def _pub_raw(priv: X25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )


def encode_pubkey(raw: bytes) -> str:
    return PUBKEY_PREFIX + base64.urlsafe_b64encode(raw).decode()


def decode_pubkey(text: str) -> bytes:
    """Parse an ``ember1:...`` public key. Raises ValueError if malformed."""
    t = str(text).strip()
    if not t.startswith(PUBKEY_PREFIX):
        raise ValueError("not an Ember public key (expected an 'ember1:' prefix)")
    raw = base64.urlsafe_b64decode(t[len(PUBKEY_PREFIX):])
    if len(raw) != 32:
        raise ValueError("malformed Ember public key")
    return raw


def key_id(raw: bytes) -> str:
    """Short stable fingerprint, for showing which recipients a file was sealed to."""
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Recipients (public keys only — safe to sync)
# ---------------------------------------------------------------------------
def load_recipients() -> list[dict]:
    """Enrolled devices/people, always including this machine."""
    out: list[dict] = []
    try:
        p = Path(RECIPIENTS_FILE)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                out = [r for r in data if isinstance(r, dict) and r.get("pub")]
    except Exception:
        out = []
    priv = _device_private()
    if priv is not None:
        raw = _pub_raw(priv)
        kid = key_id(raw)
        marked = False
        for r in out:
            if r.get("kid") == kid:
                r["this_device"] = True
                marked = True
        if not marked:
            out.insert(0, {"label": "this device", "pub": encode_pubkey(raw), "kid": kid,
                           "this_device": True})
    return out


def _save_recipients(recips: list[dict]) -> None:
    p = Path(RECIPIENTS_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Persist the enrolled list only — "this device" is re-derived from the local key on load, so
    # a synced copy of this file never claims some other machine is this one.
    keep = [{"label": r.get("label", ""), "pub": r["pub"], "kid": r.get("kid", "")}
            for r in recips]
    p.write_text(json.dumps(keep, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Key wrapping
# ---------------------------------------------------------------------------
def _passphrase_key(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))


def _ecdh_key(shared: bytes, epk_raw: bytes, pub_raw: bytes) -> Fernet:
    """Bind the wrapping key to both public keys so a wrap can't be replayed at a different
    recipient."""
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=HKDF_INFO + epk_raw + pub_raw).derive(shared)
    return Fernet(base64.urlsafe_b64encode(derived))


def _wrap_for_recipients(cek: bytes, passphrase: str, recips: list[dict]) -> list[dict]:
    salt = secrets.token_bytes(SALT_LEN)
    slots = [{
        "type": "passphrase",
        "salt": base64.b64encode(salt).decode(),
        "iterations": KDF_ITERATIONS,
        "wrapped": _passphrase_key(passphrase, salt).encrypt(cek).decode(),
    }]
    for r in recips:
        try:
            pub_raw = decode_pubkey(r["pub"])
        except Exception:
            continue  # a malformed entry must not stop the file being protected
        esk = X25519PrivateKey.generate()
        epk_raw = _pub_raw(esk)
        shared = esk.exchange(X25519PublicKey.from_public_bytes(pub_raw))
        slots.append({
            "type": "x25519",
            "kid": key_id(pub_raw),
            "label": r.get("label", ""),
            "epk": base64.b64encode(epk_raw).decode(),
            "wrapped": _ecdh_key(shared, epk_raw, pub_raw).encrypt(cek).decode(),
        })
    return slots


def _unwrap_cek(slots: list[dict], passphrase: str | None) -> bytes:
    """Recover the content key from whichever slot this machine can open."""
    priv = _device_private()
    if priv is not None:
        my_pub = _pub_raw(priv)
        my_kid = key_id(my_pub)
        for s in slots:
            if s.get("type") != "x25519" or s.get("kid") != my_kid:
                continue
            try:
                epk_raw = base64.b64decode(s["epk"])
                shared = priv.exchange(X25519PublicKey.from_public_bytes(epk_raw))
                return _ecdh_key(shared, epk_raw, my_pub).decrypt(s["wrapped"].encode())
            except Exception:
                continue
    if passphrase:
        for s in slots:
            if s.get("type") != "passphrase":
                continue
            try:
                salt = base64.b64decode(s["salt"])
                iters = int(s.get("iterations", KDF_ITERATIONS))
                return _passphrase_key(passphrase, salt, iters).decrypt(s["wrapped"].encode())
            except Exception:
                continue
    raise InvalidToken("no usable key: wrong passphrase, or this device is not a recipient")


# ---------------------------------------------------------------------------
# File format
# ---------------------------------------------------------------------------
def _atomic_write(dest: Path, blob: bytes, private: bool) -> None:
    """Write via a .part sibling so an interrupted run can't leave a truncated file that the
    user then deletes the original for."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(blob)
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # e.g. Windows — best-effort
    os.replace(tmp, dest)


def _pack(slots: list[dict], token: bytes) -> bytes:
    header = json.dumps({"v": 2, "recipients": slots}, separators=(",", ":")).encode("utf-8")
    return MAGIC + struct.pack(">I", len(header)) + header + token


def _unpack(blob: bytes) -> tuple[list[dict], bytes]:
    off = len(MAGIC)
    (hlen,) = struct.unpack(">I", blob[off:off + 4])
    off += 4
    header = json.loads(blob[off:off + hlen].decode("utf-8"))
    slots = header.get("recipients")
    if not isinstance(slots, list):
        raise ValueError("protected file has no recipient list")
    return slots, blob[off + hlen:]


def encrypt_bytes(data: bytes, dest: Path, passphrase: str, recips: list[dict]) -> list[dict]:
    """Encrypt in-memory `data` to `dest`. Raises on failure.

    Used by the phone intake so an uploaded photo is never written to disk in the clear —
    there is no plaintext temp file to shred, race, or leave behind on a crash.
    """
    cek = Fernet.generate_key()
    token = Fernet(cek).encrypt(data)
    slots = _wrap_for_recipients(cek, passphrase, recips)
    _atomic_write(dest, _pack(slots, token), private=True)
    return slots


def encrypt_file(src: Path, dest: Path, passphrase: str, recips: list[dict]) -> list[dict]:
    """Encrypt `src` to `dest` for the passphrase plus every recipient. Raises on failure."""
    return encrypt_bytes(src.read_bytes(), dest, passphrase, recips)


def decrypt_file(src: Path, dest: Path, passphrase: str | None) -> None:
    """Decrypt `src` to `dest` using whichever key this machine holds. Raises on failure."""
    blob = src.read_bytes()
    if blob.startswith(MAGIC):
        slots, token = _unpack(blob)
        data = Fernet(_unwrap_cek(slots, passphrase)).decrypt(token)
    elif blob.startswith(MAGIC_V1):
        # v1: passphrase-only, the key derived directly over the whole payload.
        if not passphrase:
            raise InvalidToken("this file predates device keys and needs the passphrase")
        salt = blob[len(MAGIC_V1):len(MAGIC_V1) + SALT_LEN]
        data = _passphrase_key(passphrase, salt).decrypt(blob[len(MAGIC_V1) + SALT_LEN:])
    else:
        raise ValueError("not an Ember-protected file (bad header)")
    _atomic_write(dest, data, private=False)


def rewrap_file(path: Path, passphrase: str, recips: list[dict]) -> list[dict]:
    """Re-wrap an existing file's content key for `recips`, leaving the payload untouched.
    Requires that this machine can already open the file."""
    blob = path.read_bytes()
    if blob.startswith(MAGIC):
        slots, token = _unpack(blob)
        cek = _unwrap_cek(slots, passphrase)
    elif blob.startswith(MAGIC_V1):
        # Upgrade v1 in place: decrypt with the passphrase, then re-seal under a fresh CEK.
        if not passphrase:
            raise InvalidToken("this file predates device keys and needs the passphrase")
        salt = blob[len(MAGIC_V1):len(MAGIC_V1) + SALT_LEN]
        data = _passphrase_key(passphrase, salt).decrypt(blob[len(MAGIC_V1) + SALT_LEN:])
        cek = Fernet.generate_key()
        token = Fernet(cek).encrypt(data)
    else:
        raise ValueError("not an Ember-protected file (bad header)")
    new_slots = _wrap_for_recipients(cek, passphrase, recips)
    _atomic_write(path, _pack(new_slots, token), private=True)
    return new_slots


def _shred(path: Path) -> bool:
    """Overwrite-then-delete, reusing the existing file shredder."""
    try:
        import power_tools
        return bool(power_tools.secure_delete(str(path)).get("ok"))
    except Exception:
        try:
            path.unlink()
            return True
        except OSError:
            return False


def _check_source(p: Path) -> str | None:
    """Return an error string if `p` isn't a usable source file, else None."""
    if not p.exists() or not p.is_file():
        return f"no such file: {p}"
    if p.name in SKIP_NAMES:
        return f"{p.name} is Ember's own key material and is never encrypted"
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return f"file is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit: {p}"
    except OSError as e:
        return str(e)
    return None


# ---------------------------------------------------------------------------
# Apple ADP availability
# ---------------------------------------------------------------------------
#: Regions where Apple withdrew Advanced Data Protection rather than comply with a government
#: access demand, so users there cannot enable it at all. Keyed by ISO country code.
APPLE_ADP_UNAVAILABLE = {
    "GB": "Apple withdrew Advanced Data Protection for UK users in February 2025 rather than "
          "comply with a Home Office Technical Capability Notice issued under the Investigatory "
          "Powers Act 2016. It still cannot be enabled here, so Apple holds the keys to your "
          "iCloud data and Ember's own encryption is the only end-to-end layer you have.",
}


def _region() -> str:
    """Best-effort ISO country code for this machine, or '' if it can't be determined.

    Locale is a weak signal (a UK user may run a US locale), so callers must treat a miss as
    'unknown' rather than 'ADP is fine here'.
    """
    import locale
    for src in (os.environ.get("LANG"), os.environ.get("LC_ALL")):
        if src and "_" in src:
            return src.split("_", 1)[1][:2].upper()
    try:
        loc = locale.getlocale()[0] or ""
        if "_" in loc:
            return loc.split("_", 1)[1][:2].upper()
    except Exception:
        pass
    return ""


def apple_adp_note() -> str | None:
    """Explain why Apple's own ADP is unavailable in this region, or None if it isn't known to
    be blocked here."""
    return APPLE_ADP_UNAVAILABLE.get(_region())


# ---------------------------------------------------------------------------
# Sync folder discovery
# ---------------------------------------------------------------------------
def icloud_targets() -> list[dict]:
    """Sync folders on this machine that protected files can be written into.

    ``syncable`` is False for the Photos library itself — iCloud Photos only syncs real images,
    so encrypted blobs cannot live there.
    """
    home = Path.home()
    out: list[dict] = []

    def add(path: Path, label: str, syncable: bool, note: str = "") -> None:
        out.append({"path": str(path), "label": label, "exists": path.exists(),
                    "syncable": syncable, "note": note})

    import sys
    if sys.platform == "darwin":
        add(home / "Library" / "Mobile Documents" / "com~apple~CloudDocs", "iCloud Drive", True)
        add(home / "Pictures" / "Photos Library.photoslibrary", "Photos library", False,
            "iCloud Photos syncs images only — export photos out of here, then protect them "
            "into iCloud Drive.")
    elif sys.platform.startswith("win"):
        add(home / "iCloudDrive", "iCloud Drive", True)
        add(home / "Pictures" / "iCloud Photos", "iCloud Photos (downloads)", False,
            "The iCloud Photos folder only accepts real images — protect into iCloud Drive "
            "instead.")
    add(home / "Dropbox", "Dropbox", True)
    add(home / "OneDrive", "OneDrive", True)
    add(home / "Google Drive", "Google Drive", True)
    return out


# ---------------------------------------------------------------------------
# Tools (exposed to the LLM)
# ---------------------------------------------------------------------------
def adp_status() -> dict:
    """Report whether protection is set up, who can decrypt, and which sync folders exist."""
    try:
        recips = load_recipients() if is_set_up() else []
        return {
            "ok": True,
            "configured": is_set_up(),
            "payload_encryption": "Fernet (AES-128-CBC + HMAC-SHA256) under a random per-file key",
            "key_wrapping": "PBKDF2-HMAC-SHA256 (passphrase) + X25519/HKDF-SHA256 (devices)",
            "iterations": KDF_ITERATIONS,
            "extension": EXT,
            "recipients": [{"label": r.get("label", ""), "key_id": r.get("kid", ""),
                            "this_device": bool(r.get("this_device"))} for r in recips],
            "sync_folders": [t for t in icloud_targets() if t["exists"]],
            "region": _region() or "unknown",
            "apple_adp_unavailable_here": apple_adp_note(),
            "note": ("Files are encrypted on this machine before they reach any cloud. Each file "
                     "can be opened by the passphrase or by any enrolled device. Neither the "
                     "passphrase nor any private key is ever uploaded."),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_setup(passphrase: str) -> dict:
    """Set the passphrase and create this device's identity key. Warns if already set up."""
    if not passphrase or len(str(passphrase)) < 8:
        return {"ok": False, "error": "passphrase must be at least 8 characters"}
    try:
        if is_set_up():
            return {"ok": False, "error": (
                "a protection passphrase is already set. Changing it would make existing "
                "protected files unreadable by passphrase — decrypt them first, then run "
                "adp_reset.")}
        import key_vault
        if not key_vault.set_key(VAULT_KEY, str(passphrase)):
            return {"ok": False, "error": "failed to store passphrase in the vault"}
        priv = _ensure_device_key()
        if priv is None:
            return {"ok": False, "error": "failed to create this device's identity key"}
        raw = _pub_raw(priv)
        return {
            "ok": True,
            "configured": True,
            "backend": key_vault.backend(),
            "device_public_key": encode_pubkey(raw),
            "device_key_id": key_id(raw),
            "next_step": ("Share device_public_key with your other devices and run "
                          "adp_add_recipient there (and vice versa) so they can decrypt too."),
            "warning": ("There is no recovery. If the passphrase is lost and no enrolled device "
                        "survives, protected files cannot be decrypted by anyone, including Ember."),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_reset() -> dict:
    """Forget the passphrase and this device's identity key. Protected files become unreadable
    on this machine."""
    try:
        import key_vault
        return {"ok": True,
                "passphrase_cleared": key_vault.delete_key(VAULT_KEY),
                "device_key_cleared": key_vault.delete_key(DEVICE_KEY)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_identity() -> dict:
    """Show this device's public key, for enrolling it on another device."""
    try:
        priv = _ensure_device_key()
        if priv is None:
            return {"ok": False, "error": "failed to create this device's identity key"}
        raw = _pub_raw(priv)
        return {"ok": True, "public_key": encode_pubkey(raw), "key_id": key_id(raw),
                "note": "Safe to share. The matching private key never leaves this machine."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_add_recipient(public_key: str, label: str = "") -> dict:
    """Enrol another device or person, by their Ember public key, so they can decrypt new files."""
    try:
        raw = decode_pubkey(public_key)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        kid = key_id(raw)
        recips = load_recipients()
        if any(r.get("kid") == kid for r in recips):
            return {"ok": False, "error": f"{kid} is already a recipient"}
        recips.append({"label": str(label) or f"device {kid[:8]}", "pub": encode_pubkey(raw),
                       "kid": kid})
        _save_recipients(recips)
        return {"ok": True, "key_id": kid, "recipient_count": len(recips),
                "note": ("New files will be readable by this recipient. Run adp_grant_access on a "
                         "folder to extend that to files already protected.")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_remove_recipient(key_id_or_label: str) -> dict:
    """Stop including a device/person in NEW files. Cannot revoke what they could already read."""
    try:
        want = str(key_id_or_label).strip()
        keep, dropped = [], []
        for r in load_recipients():
            if r.get("this_device"):
                keep.append(r)  # removing this machine would lock it out of its own files
                continue
            if r.get("kid") == want or r.get("label") == want:
                dropped.append(r.get("kid", ""))
            else:
                keep.append(r)
        if not dropped:
            return {"ok": False, "error": f"no recipient matching {want!r}"}
        _save_recipients(keep)
        return {"ok": True, "removed": dropped, "recipient_count": len(keep),
                "warning": ("This only affects files protected from now on. Copies they already "
                            "hold, and files already wrapped for them, stay readable to them.")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_list_recipients() -> dict:
    """List everyone who can decrypt newly protected files."""
    try:
        return {"ok": True, "recipients": [
            {"label": r.get("label", ""), "key_id": r.get("kid", ""),
             "this_device": bool(r.get("this_device"))} for r in load_recipients()]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_protect_file(path: str, dest_dir: str = "", delete_original: bool = False) -> dict:
    """Encrypt one file of any type. Writes `<name><EXT>` next to it, or into `dest_dir`."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "data protection is not set up — run adp_setup first"}
    try:
        src = Path(str(path)).expanduser()
        err = _check_source(src)
        if err:
            return {"ok": False, "error": err}
        if src.suffix == EXT:
            return {"ok": False, "error": f"{src} is already protected"}
        out_dir = Path(str(dest_dir)).expanduser() if dest_dir else src.parent
        dest = out_dir / (src.name + EXT)
        if dest.exists():
            return {"ok": False, "error": f"{dest} already exists"}
        recips = load_recipients()
        encrypt_file(src, dest, pw, recips)
        shredded = _shred(src) if delete_original else False
        return {"ok": True, "source": str(src), "protected": str(dest),
                "bytes": dest.stat().st_size, "original_deleted": shredded,
                "readable_by": ["passphrase"] + [r.get("kid", "") for r in recips]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_unprotect_file(path: str, dest_dir: str = "") -> dict:
    """Decrypt one protected file back to its original form."""
    pw = _passphrase()
    try:
        src = Path(str(path)).expanduser()
        err = _check_source(src)
        if err:
            return {"ok": False, "error": err}
        if src.suffix != EXT:
            return {"ok": False, "error": f"{src} is not an Ember-protected file"}
        out_dir = Path(str(dest_dir)).expanduser() if dest_dir else src.parent
        dest = out_dir / src.stem  # strips the .ember suffix, restoring the real name
        if dest.exists():
            return {"ok": False, "error": f"{dest} already exists"}
        decrypt_file(src, dest, pw)
        return {"ok": True, "source": str(src), "restored": str(dest),
                "bytes": dest.stat().st_size}
    except InvalidToken:
        return {"ok": False, "error": ("cannot decrypt: this device is not a recipient and the "
                                       "passphrase is wrong or missing, or the file was modified")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _walk(folder: Path, recursive: bool):
    return sorted(folder.rglob("*") if recursive else folder.glob("*"))


def _selected(f: Path, all_files: bool) -> bool:
    """Should this path be swept up by a folder run?"""
    if not f.is_file() or f.name in SKIP_NAMES or f.name.endswith(".part"):
        return False
    return True if all_files else f.suffix.lower() in PHOTO_EXTS


def adp_protect_folder(folder: str, dest_dir: str = "", recursive: bool = False,
                       delete_originals: bool = False, all_files: bool = False) -> dict:
    """Encrypt a folder before it syncs to iCloud. Photos and videos by default; set all_files
    to protect every file type."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "data protection is not set up — run adp_setup first"}
    try:
        root = Path(str(folder)).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"no such folder: {root}"}
        out_root = Path(str(dest_dir)).expanduser() if dest_dir else None
        recips = load_recipients()
        protected, skipped, failed = [], [], []
        for f in _walk(root, recursive):
            if f.suffix == EXT or not _selected(f, all_files):
                continue
            # Mirror the source tree under dest_dir so a recursive run doesn't flatten (and
            # silently collide on) same-named files from different subfolders.
            out_dir = (out_root / f.parent.relative_to(root)) if out_root else f.parent
            dest = out_dir / (f.name + EXT)
            if dest.exists():
                skipped.append(str(f))
                continue
            err = _check_source(f)
            if err:
                failed.append({"path": str(f), "error": err})
                continue
            try:
                encrypt_file(f, dest, pw, recips)
                protected.append(str(dest))
                if delete_originals:
                    _shred(f)
            except Exception as e:
                failed.append({"path": str(f), "error": str(e)})
        return {"ok": True, "folder": str(root), "protected_count": len(protected),
                "protected": protected, "skipped_existing": skipped, "failed": failed,
                "originals_deleted": bool(delete_originals), "all_files": bool(all_files),
                "readable_by": ["passphrase"] + [r.get("kid", "") for r in recips]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_unprotect_folder(folder: str, dest_dir: str = "", recursive: bool = False) -> dict:
    """Decrypt every Ember-protected file in a folder back to its original form."""
    pw = _passphrase()
    try:
        root = Path(str(folder)).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"no such folder: {root}"}
        out_root = Path(str(dest_dir)).expanduser() if dest_dir else None
        restored, skipped, failed = [], [], []
        for f in _walk(root, recursive):
            if not f.is_file() or f.suffix != EXT:
                continue
            out_dir = (out_root / f.parent.relative_to(root)) if out_root else f.parent
            dest = out_dir / f.stem
            if dest.exists():
                skipped.append(str(f))
                continue
            try:
                decrypt_file(f, dest, pw)
                restored.append(str(dest))
            except InvalidToken:
                failed.append({"path": str(f), "error": "not a recipient, or wrong passphrase"})
            except Exception as e:
                failed.append({"path": str(f), "error": str(e)})
        return {"ok": True, "folder": str(root), "restored_count": len(restored),
                "restored": restored, "skipped_existing": skipped, "failed": failed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_grant_access(folder: str, recursive: bool = False) -> dict:
    """Re-wrap already-protected files so every current recipient can read them. Use after
    enrolling a new device. The encrypted payload is not rewritten."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "data protection is not set up — run adp_setup first"}
    try:
        root = Path(str(folder)).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"no such folder: {root}"}
        recips = load_recipients()
        updated, failed = [], []
        for f in _walk(root, recursive):
            if not f.is_file() or f.suffix != EXT:
                continue
            try:
                rewrap_file(f, pw, recips)
                updated.append(str(f))
            except InvalidToken:
                failed.append({"path": str(f),
                               "error": "this device cannot open it, so it cannot be shared "
                                        "from here"})
            except Exception as e:
                failed.append({"path": str(f), "error": str(e)})
        return {"ok": True, "folder": str(root), "updated_count": len(updated),
                "updated": updated, "failed": failed,
                "readable_by": ["passphrase"] + [r.get("kid", "") for r in recips]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_inspect(path: str) -> dict:
    """Show who can decrypt a protected file, without decrypting it."""
    try:
        p = Path(str(path)).expanduser()
        if not p.is_file():
            return {"ok": False, "error": f"no such file: {p}"}
        blob = p.read_bytes()
        if blob.startswith(MAGIC_V1):
            return {"ok": True, "path": str(p), "format": "v1",
                    "readable_by": [{"type": "passphrase"}],
                    "note": "Predates device keys. Run adp_grant_access to upgrade it."}
        if not blob.startswith(MAGIC):
            return {"ok": False, "error": "not an Ember-protected file"}
        slots, _ = _unpack(blob)
        priv = _device_private()
        my_kid = key_id(_pub_raw(priv)) if priv is not None else None
        return {"ok": True, "path": str(p), "format": "v2", "readable_by": [
            {"type": s.get("type"), "key_id": s.get("kid", ""), "label": s.get("label", ""),
             "this_device": s.get("kid") == my_kid} for s in slots]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_icloud_targets() -> dict:
    """List the iCloud/Dropbox/OneDrive/Google Drive folders on this machine and say which ones
    protected files can be stored in."""
    try:
        return {"ok": True, "targets": icloud_targets()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "adp_status",
        "description": "Report whether Ember's Advanced Data Protection is set up, which "
                       "encryption it uses, who can decrypt, and which cloud sync folders exist. "
                       "Also flags regions where Apple's own ADP cannot be enabled.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_setup",
        "description": "Set the passphrase and create this device's identity key, so files can be "
                       "encrypted before they sync to iCloud. There is no recovery if both the "
                       "passphrase and every enrolled device are lost.",
        "parameters": {"type": "OBJECT", "properties": {
            "passphrase": {"type": "STRING", "description": "at least 8 characters"}},
            "required": ["passphrase"]},
    },
    {
        "name": "adp_reset",
        "description": "Forget the stored passphrase and this device's identity key. Files "
                       "protected with them become unreadable on this machine.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_identity",
        "description": "Show this device's Ember public key so another device or person can "
                       "enrol it as a recipient. Safe to share.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_add_recipient",
        "description": "Enrol another device or person by their Ember public key so they can "
                       "decrypt newly protected files.",
        "parameters": {"type": "OBJECT", "properties": {
            "public_key": {"type": "STRING", "description": "their key, starting with 'ember1:'"},
            "label": {"type": "STRING", "description": "a name for them, e.g. 'my iPad'"}},
            "required": ["public_key"]},
    },
    {
        "name": "adp_remove_recipient",
        "description": "Stop including a device or person in newly protected files. Does not "
                       "revoke access to files they can already read.",
        "parameters": {"type": "OBJECT", "properties": {
            "key_id_or_label": {"type": "STRING",
                                "description": "the recipient's key id or label"}},
            "required": ["key_id_or_label"]},
    },
    {
        "name": "adp_list_recipients",
        "description": "List every device and person who can decrypt newly protected files.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_protect_file",
        "description": "Encrypt a single file of any type on this machine so that what reaches "
                       "iCloud (or any cloud) is unreadable without a key.",
        "parameters": {"type": "OBJECT", "properties": {
            "path": {"type": "STRING", "description": "path to the file"},
            "dest_dir": {"type": "STRING", "description": "where to write the protected file; defaults to alongside the original"},
            "delete_original": {"type": "BOOLEAN", "description": "securely shred the unencrypted original afterwards (default false)"}},
            "required": ["path"]},
    },
    {
        "name": "adp_unprotect_file",
        "description": "Decrypt one Ember-protected file back to its original form.",
        "parameters": {"type": "OBJECT", "properties": {
            "path": {"type": "STRING", "description": "path to the .ember file"},
            "dest_dir": {"type": "STRING", "description": "where to write the restored file"}},
            "required": ["path"]},
    },
    {
        "name": "adp_protect_folder",
        "description": "Encrypt a folder before it syncs to iCloud or another cloud drive. "
                       "Protects photos and videos by default; set all_files to protect every "
                       "file type.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder to protect"},
            "dest_dir": {"type": "STRING", "description": "where to write protected copies, e.g. the iCloud Drive folder"},
            "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"},
            "delete_originals": {"type": "BOOLEAN", "description": "securely shred each unencrypted original after protecting it (default false)"},
            "all_files": {"type": "BOOLEAN", "description": "protect every file type, not just photos and videos (default false)"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_unprotect_folder",
        "description": "Decrypt every Ember-protected file in a folder back to its original form.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder containing .ember files"},
            "dest_dir": {"type": "STRING", "description": "where to write the restored files"},
            "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_grant_access",
        "description": "Re-wrap already-protected files so every currently enrolled device can "
                       "read them. Run this after adding a new device.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder containing .ember files"},
            "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_inspect",
        "description": "Show which passphrase/devices can decrypt a protected file, without "
                       "decrypting it.",
        "parameters": {"type": "OBJECT", "properties": {
            "path": {"type": "STRING", "description": "path to the .ember file"}},
            "required": ["path"]},
    },
    {
        "name": "adp_icloud_targets",
        "description": "List the iCloud/Dropbox/OneDrive/Google Drive folders on this machine "
                       "and say which ones protected files can be stored in.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH = {
    "adp_status": adp_status,
    "adp_setup": adp_setup,
    "adp_reset": adp_reset,
    "adp_identity": adp_identity,
    "adp_add_recipient": adp_add_recipient,
    "adp_remove_recipient": adp_remove_recipient,
    "adp_list_recipients": adp_list_recipients,
    "adp_protect_file": adp_protect_file,
    "adp_unprotect_file": adp_unprotect_file,
    "adp_protect_folder": adp_protect_folder,
    "adp_unprotect_folder": adp_unprotect_folder,
    "adp_grant_access": adp_grant_access,
    "adp_inspect": adp_inspect,
    "adp_icloud_targets": adp_icloud_targets,
}

READONLY_TOOLS = {"adp_status", "adp_icloud_targets", "adp_list_recipients", "adp_identity",
                  "adp_inspect"}
INTERACTION_TOOLS = {
    "adp_setup", "adp_reset", "adp_add_recipient", "adp_remove_recipient",
    "adp_protect_file", "adp_unprotect_file", "adp_protect_folder", "adp_unprotect_folder",
    "adp_grant_access",
}
