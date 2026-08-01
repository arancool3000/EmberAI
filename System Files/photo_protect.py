"""Advanced Data Protection — client-side encryption for photos before they sync to iCloud.

Why this exists
---------------
iCloud Photos is encrypted in transit and at rest, but by default Apple holds the keys: the
data is *not* end-to-end encrypted unless the user turns on Apple's own "Advanced Data
Protection" (iOS 16.2+). Even with ADP on, the protection starts at Apple's boundary — the
plaintext still leaves the device under Apple's key hierarchy.

This module gives Ember a *provider-independent* layer underneath that: photos are encrypted
on the user's own machine, with a passphrase only the user knows, before the file is ever
placed in an iCloud-synced folder. What reaches Apple's servers is an opaque blob. Nobody
without the passphrase — Apple, an attacker with a stolen backup, or Ember itself — can read
it. The same works for Dropbox, Google Drive, OneDrive or any other sync folder.

Honest limits (read before relying on this)
-------------------------------------------
* It does NOT encrypt an existing iCloud Photos *library*. iCloud Photos only syncs real image
  files; an encrypted blob isn't one. To protect photos already in the library you must export
  them, protect them into a synced *folder* (iCloud Drive), and then delete the originals from
  Photos yourself. `adp_icloud_targets` reports which paths on this machine are usable.
* Anything already uploaded stays uploaded. This protects files going forward.
* Lose the passphrase and the photos are gone. There is no recovery path, by design.
* Encrypted files no longer preview as photos in Finder/Photos — that is the point.

Crypto
------
PBKDF2-HMAC-SHA256 (600,000 iterations, 16-byte random salt per file) derives a Fernet key
(AES-128-CBC + HMAC-SHA256) from the passphrase. File layout::

    b"EMBERADP1" | salt (16 bytes) | Fernet token

The salt is per-file, so two copies of the same photo produce unrelated ciphertext. Fernet is
authenticated, so a wrong passphrase or a tampered file fails loudly rather than yielding
garbage.

The passphrase is held in ``key_vault`` (OS keychain where available, else the encrypted-file
vault) under ``adp_passphrase``. No tool in this module ever returns it, and it is never
written into an encrypted file.

Every tool returns a dict and never raises.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"EMBERADP1"
SALT_LEN = 16
KDF_ITERATIONS = 600_000
EXT = ".ember"

VAULT_KEY = "adp_passphrase"

#: Files `adp_protect_folder` treats as photos. Everything else is skipped.
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".tif", ".tiff",
    ".webp", ".bmp", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf",
    ".rw2", ".raf", ".mov", ".mp4", ".m4v", ".avi",
}

#: Hard ceiling per file (bytes). Fernet holds the whole payload in memory, and a
#: multi-gigabyte video would exhaust RAM rather than fail cleanly.
MAX_FILE_BYTES = 512 * 1024 * 1024


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------
def _derive(passphrase: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))


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


# ---------------------------------------------------------------------------
# Single-file encrypt / decrypt (plain helpers — ui.py may call these directly)
# ---------------------------------------------------------------------------
def encrypt_file(src: Path, dest: Path, passphrase: str) -> None:
    """Encrypt `src` to `dest`. Raises on failure; callers wrap."""
    data = src.read_bytes()
    salt = secrets.token_bytes(SALT_LEN)
    token = _derive(passphrase, salt).encrypt(data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp sibling first so an interrupted run can't leave a half-written
    # "protected" file that the user then deletes the original for.
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(MAGIC + salt + token)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # e.g. Windows — best-effort
    os.replace(tmp, dest)


def decrypt_file(src: Path, dest: Path, passphrase: str) -> None:
    """Decrypt `src` to `dest`. Raises on failure; callers wrap."""
    blob = src.read_bytes()
    if not blob.startswith(MAGIC):
        raise ValueError("not an Ember-protected file (bad header)")
    salt = blob[len(MAGIC):len(MAGIC) + SALT_LEN]
    token = blob[len(MAGIC) + SALT_LEN:]
    data = _derive(passphrase, salt).decrypt(token)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


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
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return f"file is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB limit: {p}"
    except OSError as e:
        return str(e)
    return None


# ---------------------------------------------------------------------------
# iCloud / sync folder discovery
# ---------------------------------------------------------------------------
def icloud_targets() -> list[dict]:
    """Sync folders on this machine that protected photos can be written into.

    ``syncable`` is False for the Photos library itself — iCloud Photos only syncs real
    images, so encrypted blobs cannot live there.
    """
    home = Path.home()
    out: list[dict] = []

    def add(path: Path, label: str, syncable: bool, note: str = "") -> None:
        out.append({
            "path": str(path),
            "label": label,
            "exists": path.exists(),
            "syncable": syncable,
            "note": note,
        })

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
    """Report whether photo protection is set up, and which sync folders are available."""
    try:
        targets = icloud_targets()
        return {
            "ok": True,
            "configured": is_set_up(),
            "algorithm": "PBKDF2-HMAC-SHA256 -> Fernet (AES-128-CBC + HMAC-SHA256)",
            "iterations": KDF_ITERATIONS,
            "extension": EXT,
            "sync_folders": [t for t in targets if t["exists"]],
            "note": (
                "Photos are encrypted on this machine before they reach any cloud. "
                "The passphrase is never uploaded and cannot be recovered if lost."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_setup(passphrase: str) -> dict:
    """Store the passphrase used to protect photos. Warns if one is already set."""
    if not passphrase or len(str(passphrase)) < 8:
        return {"ok": False, "error": "passphrase must be at least 8 characters"}
    try:
        if is_set_up():
            return {
                "ok": False,
                "error": (
                    "a protection passphrase is already set. Changing it would make existing "
                    "protected photos unreadable — decrypt them first, then run adp_reset."
                ),
            }
        import key_vault
        if not key_vault.set_key(VAULT_KEY, str(passphrase)):
            return {"ok": False, "error": "failed to store passphrase in the vault"}
        return {
            "ok": True,
            "configured": True,
            "backend": key_vault.backend(),
            "warning": "There is no recovery. If this passphrase is lost, protected photos "
                       "cannot be decrypted by anyone, including Ember.",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_reset() -> dict:
    """Forget the stored passphrase. Existing protected photos become unreadable."""
    try:
        import key_vault
        return {"ok": True, "cleared": key_vault.delete_key(VAULT_KEY)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_protect_photo(path: str, dest_dir: str = "", delete_original: bool = False) -> dict:
    """Encrypt one photo. Writes `<name><EXT>` next to it, or into `dest_dir`."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "photo protection is not set up — run adp_setup first"}
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
        encrypt_file(src, dest, pw)
        shredded = _shred(src) if delete_original else False
        return {
            "ok": True,
            "source": str(src),
            "protected": str(dest),
            "bytes": dest.stat().st_size,
            "original_deleted": shredded,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_unprotect_photo(path: str, dest_dir: str = "") -> dict:
    """Decrypt one protected photo back to its original form."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "photo protection is not set up — run adp_setup first"}
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
        return {"ok": False, "error": "wrong passphrase, or the file was modified/corrupted"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _walk(folder: Path, recursive: bool):
    return sorted(folder.rglob("*") if recursive else folder.glob("*"))


def adp_protect_folder(folder: str, dest_dir: str = "", recursive: bool = False,
                       delete_originals: bool = False) -> dict:
    """Encrypt every photo in a folder — e.g. before dropping it into iCloud Drive."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "photo protection is not set up — run adp_setup first"}
    try:
        root = Path(str(folder)).expanduser()
        if not root.is_dir():
            return {"ok": False, "error": f"no such folder: {root}"}
        out_root = Path(str(dest_dir)).expanduser() if dest_dir else None
        protected, skipped, failed = [], [], []
        for f in _walk(root, recursive):
            if not f.is_file() or f.suffix.lower() not in PHOTO_EXTS:
                continue
            # Mirror the source tree under dest_dir so a recursive run doesn't flatten
            # (and silently collide on) same-named photos from different subfolders.
            out_dir = (out_root / f.parent.relative_to(root)) if out_root else f.parent
            dest = out_dir / (f.name + EXT)
            if dest.exists():
                skipped.append(str(f))
                continue
            if _check_source(f):
                failed.append({"path": str(f), "error": _check_source(f)})
                continue
            try:
                encrypt_file(f, dest, pw)
                protected.append(str(dest))
                if delete_originals:
                    _shred(f)
            except Exception as e:
                failed.append({"path": str(f), "error": str(e)})
        return {"ok": True, "folder": str(root), "protected_count": len(protected),
                "protected": protected, "skipped_existing": skipped, "failed": failed,
                "originals_deleted": bool(delete_originals)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_unprotect_folder(folder: str, dest_dir: str = "", recursive: bool = False) -> dict:
    """Decrypt every Ember-protected photo in a folder back to viewable images."""
    pw = _passphrase()
    if pw is None:
        return {"ok": False, "error": "photo protection is not set up — run adp_setup first"}
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
            if _check_source(f):
                failed.append({"path": str(f), "error": _check_source(f)})
                continue
            try:
                decrypt_file(f, dest, pw)
                restored.append(str(dest))
            except InvalidToken:
                failed.append({"path": str(f),
                               "error": "wrong passphrase, or the file was modified/corrupted"})
            except Exception as e:
                failed.append({"path": str(f), "error": str(e)})
        return {"ok": True, "folder": str(root), "restored_count": len(restored),
                "restored": restored, "skipped_existing": skipped, "failed": failed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_icloud_targets() -> dict:
    """List iCloud/Dropbox/OneDrive folders on this machine and whether they can hold
    protected photos."""
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
        "description": "Report whether Ember's Advanced Data Protection for photos is set up, "
                       "which encryption it uses, and which cloud sync folders exist on this machine.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_setup",
        "description": "Set the passphrase used to encrypt photos before they sync to iCloud. "
                       "Stored in the OS keychain. There is no recovery if it is lost.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "passphrase": {"type": "STRING", "description": "at least 8 characters"},
            },
            "required": ["passphrase"],
        },
    },
    {
        "name": "adp_reset",
        "description": "Forget the stored photo-protection passphrase. Any photos already "
                       "protected with it become permanently unreadable.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_protect_photo",
        "description": "Encrypt a single photo on this machine so that what reaches iCloud (or "
                       "any cloud) is unreadable without the passphrase.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "path to the photo"},
                "dest_dir": {"type": "STRING", "description": "where to write the protected file; defaults to alongside the original"},
                "delete_original": {"type": "BOOLEAN", "description": "securely shred the unencrypted original afterwards (default false)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "adp_unprotect_photo",
        "description": "Decrypt one Ember-protected photo back to a normal viewable image.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "path to the .ember file"},
                "dest_dir": {"type": "STRING", "description": "where to write the restored photo; defaults to alongside the protected file"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "adp_protect_folder",
        "description": "Encrypt every photo in a folder before it syncs to iCloud or another "
                       "cloud drive. Non-photo files are left alone.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "folder": {"type": "STRING", "description": "folder containing the photos"},
                "dest_dir": {"type": "STRING", "description": "where to write protected copies, e.g. the iCloud Drive folder"},
                "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"},
                "delete_originals": {"type": "BOOLEAN", "description": "securely shred each unencrypted original after protecting it (default false)"},
            },
            "required": ["folder"],
        },
    },
    {
        "name": "adp_unprotect_folder",
        "description": "Decrypt every Ember-protected photo in a folder back to viewable images.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "folder": {"type": "STRING", "description": "folder containing .ember files"},
                "dest_dir": {"type": "STRING", "description": "where to write the restored photos"},
                "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"},
            },
            "required": ["folder"],
        },
    },
    {
        "name": "adp_icloud_targets",
        "description": "List the iCloud/Dropbox/OneDrive/Google Drive folders on this machine "
                       "and say which ones protected photos can be stored in.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH = {
    "adp_status": adp_status,
    "adp_setup": adp_setup,
    "adp_reset": adp_reset,
    "adp_protect_photo": adp_protect_photo,
    "adp_unprotect_photo": adp_unprotect_photo,
    "adp_protect_folder": adp_protect_folder,
    "adp_unprotect_folder": adp_unprotect_folder,
    "adp_icloud_targets": adp_icloud_targets,
}

READONLY_TOOLS = {"adp_status", "adp_icloud_targets"}
INTERACTION_TOOLS = {
    "adp_setup", "adp_reset", "adp_protect_photo", "adp_unprotect_photo",
    "adp_protect_folder", "adp_unprotect_folder",
}
