"""Tests for data_protect — Ember's Advanced Data Protection.

key_vault and the recipient list are pointed at a tmp_path so real user state is never touched.
The passphrase and device key go through the normal vault path (encrypted-file backend here).

`_second_device` simulates another machine by swapping in a fresh vault + recipient file — its
own private key and, deliberately, the WRONG passphrase, so that any successful decrypt there
can only have come from that device's own key slot.
"""
import base64
import contextlib

import key_vault as KV
import data_protect as DP
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


#: A passphrase the second device holds that is NOT the one protecting the files, so a
#: successful decrypt there proves the device key did the work.
WRONG_PW = "a totally different passphrase"


def _isolate(monkeypatch, tmp_path, name="a"):
    """Point the vault and recipient list at a per-device temp location."""
    d = tmp_path / f"_state_{name}"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(KV, "VAULT_FILE", d / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", d / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", d / "adp_recipients.json")
    return d


def _setup(monkeypatch, tmp_path, passphrase="correct horse battery", name="a"):
    _isolate(monkeypatch, tmp_path, name)
    r = DP.adp_setup(passphrase)
    assert r["ok"] is True
    return r


@contextlib.contextmanager
def _second_device(monkeypatch, tmp_path, passphrase="correct horse battery"):
    """Run the body as a *different* machine: its own vault, its own device key.

    Re-entering returns to the same machine (its state persists in the temp dir), so a test can
    read that device's public key, go back to the first machine, then come back to decrypt.
    """
    with monkeypatch.context() as m:
        _isolate(m, tmp_path, "b")
        if not DP.is_set_up():
            assert DP.adp_setup(passphrase)["ok"] is True
        yield


def _file(tmp_path, name="holiday.jpg", data=b"\xff\xd8\xff-real-photo-bytes-secret"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- setup ---------------------------------------------------------------------
def test_not_configured_until_setup(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert DP.is_set_up() is False
    st = DP.adp_status()
    assert st["ok"] is True and st["configured"] is False
    assert DP.adp_protect_file(str(tmp_path / "x.jpg"))["ok"] is False
    assert DP.adp_protect_folder(str(tmp_path))["ok"] is False


def test_setup_rejects_short_passphrase(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert DP.adp_setup("short")["ok"] is False
    assert DP.is_set_up() is False


def test_setup_creates_a_device_identity(monkeypatch, tmp_path):
    r = _setup(monkeypatch, tmp_path)
    assert r["device_public_key"].startswith(DP.PUBKEY_PREFIX)
    assert DP.decode_pubkey(r["device_public_key"])  # parses as a real 32-byte X25519 key
    assert DP.adp_identity()["key_id"] == r["device_key_id"]


def test_setup_refuses_to_silently_replace(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = DP.adp_setup("a different passphrase")
    assert r["ok"] is False and "already set" in r["error"]
    assert KV.get_key(DP.VAULT_KEY) == "correct horse battery"


def test_reset_clears_both_secrets(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = DP.adp_reset()
    assert r["passphrase_cleared"] is True and r["device_key_cleared"] is True
    assert DP.is_set_up() is False


def test_status_never_leaks_secrets(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, passphrase="super-secret-phrase")
    blob = repr(DP.adp_status()) + repr(DP.adp_list_recipients()) + repr(DP.adp_identity())
    assert "super-secret-phrase" not in blob
    # The device *private* key must never appear in any tool result either.
    priv = KV.get_key(DP.DEVICE_KEY)
    assert priv and priv not in blob


# --- single file ---------------------------------------------------------------
def test_roundtrip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path)
    original = src.read_bytes()

    r = DP.adp_protect_file(str(src))
    assert r["ok"] is True
    enc = tmp_path / "holiday.jpg.ember"
    blob = enc.read_bytes()
    assert b"real-photo-bytes-secret" not in blob
    assert blob.startswith(DP.MAGIC)

    src.unlink()
    assert DP.adp_unprotect_file(str(enc))["ok"] is True
    assert (tmp_path / "holiday.jpg").read_bytes() == original


def test_any_file_type_not_just_photos(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path, "tax-return.pdf", b"%PDF-1.7 confidential")
    assert DP.adp_protect_file(str(src))["ok"] is True
    assert b"confidential" not in (tmp_path / "tax-return.pdf.ember").read_bytes()


def test_content_key_is_per_file(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    a = _file(tmp_path, "a.jpg", b"identical")
    b = _file(tmp_path, "b.jpg", b"identical")
    DP.adp_protect_file(str(a))
    DP.adp_protect_file(str(b))
    assert (tmp_path / "a.jpg.ember").read_bytes() != (tmp_path / "b.jpg.ember").read_bytes()


def test_tampered_payload_is_rejected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path)
    DP.adp_protect_file(str(src), delete_original=True)
    enc = tmp_path / "holiday.jpg.ember"
    blob = bytearray(enc.read_bytes())
    blob[-5] ^= 0xFF
    enc.write_bytes(bytes(blob))
    assert DP.adp_unprotect_file(str(enc))["ok"] is False
    assert not (tmp_path / "holiday.jpg").exists()  # no garbage written


def test_delete_original_shreds(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path)
    r = DP.adp_protect_file(str(src), delete_original=True)
    assert r["ok"] is True and r["original_deleted"] is True
    assert not src.exists()


def test_never_overwrites_existing_output(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path)
    assert DP.adp_protect_file(str(src))["ok"] is True
    r = DP.adp_protect_file(str(src))
    assert r["ok"] is False and "already exists" in r["error"]


def test_rejects_missing_and_non_ember_files(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert DP.adp_protect_file(str(tmp_path / "nope.jpg"))["ok"] is False
    plain = _file(tmp_path, "plain.jpg")
    r = DP.adp_unprotect_file(str(plain))
    assert r["ok"] is False and "not an Ember-protected file" in r["error"]


def test_never_encrypts_embers_own_key_material(monkeypatch, tmp_path):
    """Sealing the vault with a key stored inside the vault would be unrecoverable."""
    _setup(monkeypatch, tmp_path)
    vault = _file(tmp_path, "vault.key", b"master-key")
    r = DP.adp_protect_file(str(vault))
    assert r["ok"] is False and "never encrypted" in r["error"]
    out = DP.adp_protect_folder(str(tmp_path), all_files=True)
    assert not (tmp_path / "vault.key.ember").exists()
    assert all("vault.key" not in p for p in out["protected"])


def test_dest_dir_targets_a_sync_folder(monkeypatch, tmp_path):
    """The real use case: protect out of Pictures and into the iCloud Drive folder."""
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path)
    icloud = tmp_path / "iCloudDrive"
    assert DP.adp_protect_file(str(src), dest_dir=str(icloud))["ok"] is True
    assert (icloud / "holiday.jpg.ember").exists()
    assert not (tmp_path / "holiday.jpg.ember").exists()


# --- multi-device / multi-user --------------------------------------------------
def test_enrolled_device_can_decrypt_without_the_passphrase(monkeypatch, tmp_path):
    """The point of the whole design: another device opens the file with its own key."""
    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        other_pub = DP.adp_identity()["public_key"]

    _setup(monkeypatch, tmp_path)
    assert DP.adp_add_recipient(other_pub, "my iPad")["ok"] is True
    src = _file(tmp_path, "shared.jpg", b"visible-to-both")
    assert DP.adp_protect_file(str(src), delete_original=True)["ok"] is True
    enc = tmp_path / "shared.jpg.ember"

    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        # Different passphrase, so success can only come from this device's own key slot.
        assert DP.adp_unprotect_file(str(enc))["ok"] is True
        assert (tmp_path / "shared.jpg").read_bytes() == b"visible-to-both"


def test_stranger_device_cannot_decrypt(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path, "private.jpg", b"not-for-you")
    # Shred the original so a failure here can only be the crypto refusing, never a
    # "destination already exists" guard standing in for it.
    DP.adp_protect_file(str(src), delete_original=True)
    enc = tmp_path / "private.jpg.ember"

    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        r = DP.adp_unprotect_file(str(enc))
        assert r["ok"] is False and "cannot decrypt" in r["error"]


def test_passphrase_always_recovers_even_with_no_devices(monkeypatch, tmp_path):
    """Every device lost: the passphrase alone must still open the file."""
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path, "backup.jpg", b"recoverable")
    DP.adp_protect_file(str(src), delete_original=True)
    enc = tmp_path / "backup.jpg.ember"

    with _second_device(monkeypatch, tmp_path):  # same passphrase, brand-new device key
        assert DP.adp_unprotect_file(str(enc))["ok"] is True
        assert (tmp_path / "backup.jpg").read_bytes() == b"recoverable"


def test_grant_access_extends_to_existing_files(monkeypatch, tmp_path):
    """A device added after the fact reads the back catalogue only once access is granted."""
    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        late_pub = DP.adp_identity()["public_key"]
        late_kid = DP.adp_identity()["key_id"]

    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path, "old.jpg", b"encrypted-before-they-joined")
    DP.adp_protect_file(str(src), delete_original=True)
    enc = tmp_path / "old.jpg.ember"
    assert late_kid not in [s["key_id"] for s in DP.adp_inspect(str(enc))["readable_by"]]

    DP.adp_add_recipient(late_pub, "new laptop")
    r = DP.adp_grant_access(str(tmp_path))
    assert r["ok"] is True and r["updated_count"] == 1
    assert late_kid in [s["key_id"] for s in DP.adp_inspect(str(enc))["readable_by"]]

    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        assert DP.adp_unprotect_file(str(enc))["ok"] is True
        assert (tmp_path / "old.jpg").read_bytes() == b"encrypted-before-they-joined"


def test_grant_access_preserves_the_payload(monkeypatch, tmp_path):
    """Re-wrapping rewrites the header only — the ciphertext body must be byte-identical."""
    _setup(monkeypatch, tmp_path)
    src = _file(tmp_path, "big.jpg", b"x" * 5000)
    DP.adp_protect_file(str(src))
    enc = tmp_path / "big.jpg.ember"
    before = DP._unpack(enc.read_bytes())[1]
    DP.adp_add_recipient(DP.encode_pubkey(
        DP._pub_raw(X25519PrivateKey.generate())), "someone else")
    assert DP.adp_grant_access(str(tmp_path))["ok"] is True
    assert DP._unpack(enc.read_bytes())[1] == before


def test_remove_recipient_is_honest_about_what_it_cannot_do(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    pub = DP.encode_pubkey(DP._pub_raw(X25519PrivateKey.generate()))
    DP.adp_add_recipient(pub, "ex-colleague")
    r = DP.adp_remove_recipient("ex-colleague")
    assert r["ok"] is True and "stay readable to them" in r["warning"]
    assert "ex-colleague" not in [x["label"] for x in DP.adp_list_recipients()["recipients"]]


def test_cannot_remove_this_device(monkeypatch, tmp_path):
    """Dropping our own key would lock this machine out of files it just wrote."""
    _setup(monkeypatch, tmp_path)
    me = DP.adp_identity()["key_id"]
    assert DP.adp_remove_recipient(me)["ok"] is False
    assert me in [x["key_id"] for x in DP.adp_list_recipients()["recipients"]]


def test_rejects_malformed_public_keys(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert DP.adp_add_recipient("not-a-key")["ok"] is False
    assert DP.adp_add_recipient(DP.PUBKEY_PREFIX + base64.urlsafe_b64encode(b"tooshort").decode())["ok"] is False
    assert DP.adp_add_recipient(DP.adp_identity()["public_key"])["ok"] is False  # duplicate


def test_recipient_file_holds_no_private_material(monkeypatch, tmp_path):
    """It is meant to be synced through iCloud, so it must carry public keys only."""
    d = _setup(monkeypatch, tmp_path) and None
    DP.adp_add_recipient(DP.encode_pubkey(DP._pub_raw(X25519PrivateKey.generate())), "iPad")
    text = DP.RECIPIENTS_FILE.read_text()
    assert KV.get_key(DP.DEVICE_KEY) not in text
    assert KV.get_key(DP.VAULT_KEY) not in text


def test_synced_recipient_file_does_not_claim_to_be_this_device(monkeypatch, tmp_path):
    """A copy of the list from another machine must not mark that machine as 'this device'."""
    _setup(monkeypatch, tmp_path)
    DP.adp_add_recipient(DP.encode_pubkey(DP._pub_raw(X25519PrivateKey.generate())), "iPad")
    shared = DP.RECIPIENTS_FILE.read_text()

    with _second_device(monkeypatch, tmp_path):
        DP.RECIPIENTS_FILE.write_text(shared)  # as if synced down from iCloud
        here = [r for r in DP.adp_list_recipients()["recipients"] if r["this_device"]]
        assert len(here) == 1
        assert here[0]["key_id"] == DP.adp_identity()["key_id"]


# --- folders --------------------------------------------------------------------
def test_protect_folder_photos_only_by_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _file(tmp_path, "one.jpg")
    _file(tmp_path, "two.HEIC")
    _file(tmp_path, "notes.txt", b"not a photo")
    r = DP.adp_protect_folder(str(tmp_path))
    assert r["ok"] is True and r["protected_count"] == 2
    assert not (tmp_path / "notes.txt.ember").exists()


def test_protect_folder_all_files(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _file(tmp_path, "one.jpg")
    _file(tmp_path, "notes.txt", b"secret notes")
    _file(tmp_path, "ledger.xlsx", b"secret numbers")
    r = DP.adp_protect_folder(str(tmp_path), all_files=True)
    assert r["ok"] is True and r["protected_count"] == 3
    assert b"secret notes" not in (tmp_path / "notes.txt.ember").read_bytes()


def test_protect_folder_recursive_preserves_tree(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src_root = tmp_path / "pics"
    sub = src_root / "2024" / "spain"
    sub.mkdir(parents=True)
    _file(src_root, "same.jpg", b"root-bytes")
    _file(sub, "same.jpg", b"nested-bytes")
    out = tmp_path / "out"
    r = DP.adp_protect_folder(str(src_root), dest_dir=str(out), recursive=True)
    assert r["ok"] is True and r["protected_count"] == 2
    # Same-named files in different subfolders must not collide in the destination.
    assert (out / "same.jpg.ember").exists()
    assert (out / "2024" / "spain" / "same.jpg.ember").exists()

    back = tmp_path / "back"
    u = DP.adp_unprotect_folder(str(out), dest_dir=str(back), recursive=True)
    assert u["ok"] is True and u["restored_count"] == 2
    assert (back / "2024" / "spain" / "same.jpg").read_bytes() == b"nested-bytes"
    assert (back / "same.jpg").read_bytes() == b"root-bytes"


def test_protect_folder_non_recursive_ignores_subfolders(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src_root = tmp_path / "pics"
    (src_root / "sub").mkdir(parents=True)
    _file(src_root / "sub", "deep.jpg")
    _file(src_root, "top.jpg")
    assert DP.adp_protect_folder(str(src_root))["protected_count"] == 1


def test_protect_folder_skips_already_protected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _file(tmp_path, "one.jpg")
    assert DP.adp_protect_folder(str(tmp_path))["protected_count"] == 1
    r = DP.adp_protect_folder(str(tmp_path), all_files=True)
    # The .ember output must not itself be re-encrypted on a second pass.
    assert r["protected_count"] == 0 and r["skipped_existing"]


def test_protect_folder_missing(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert DP.adp_protect_folder(str(tmp_path / "nope"))["ok"] is False


# --- backward compatibility -----------------------------------------------------
def _write_v1(path, passphrase, data):
    """Produce a file in the original passphrase-only v1 format."""
    import secrets
    salt = secrets.token_bytes(DP.SALT_LEN)
    token = DP._passphrase_key(passphrase, salt).encrypt(data)
    path.write_bytes(DP.MAGIC_V1 + salt + token)


def test_v1_files_still_decrypt(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    enc = tmp_path / "legacy.jpg.ember"
    _write_v1(enc, "correct horse battery", b"written-by-the-old-format")
    assert DP.adp_inspect(str(enc))["format"] == "v1"
    assert DP.adp_unprotect_file(str(enc))["ok"] is True
    assert (tmp_path / "legacy.jpg").read_bytes() == b"written-by-the-old-format"


def test_v1_files_upgrade_on_grant_access(monkeypatch, tmp_path):
    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        other_pub = DP.adp_identity()["public_key"]

    _setup(monkeypatch, tmp_path)
    enc = tmp_path / "legacy.jpg.ember"
    _write_v1(enc, "correct horse battery", b"old-but-still-mine")
    DP.adp_add_recipient(other_pub, "iPad")
    assert DP.adp_grant_access(str(tmp_path))["updated_count"] == 1
    assert DP.adp_inspect(str(enc))["format"] == "v2"

    with _second_device(monkeypatch, tmp_path, WRONG_PW):
        assert DP.adp_unprotect_file(str(enc))["ok"] is True
        assert (tmp_path / "legacy.jpg").read_bytes() == b"old-but-still-mine"


# --- Apple ADP availability -----------------------------------------------------
def test_uk_is_flagged_as_having_no_apple_adp(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    note = DP.apple_adp_note()
    assert note and "Home Office" in note
    st = DP.adp_status()
    assert st["region"] == "GB" and st["apple_adp_unavailable_here"] == note


def test_other_regions_are_not_flagged(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    assert DP.apple_adp_note() is None


def test_unknown_region_is_not_treated_as_fine(monkeypatch, tmp_path):
    """A locale we can't read must report 'unknown', never a false all-clear."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(DP, "_region", lambda: "")
    assert DP.adp_status()["region"] == "unknown"


# --- wiring ---------------------------------------------------------------------
def test_icloud_targets_flags_photos_library():
    r = DP.adp_icloud_targets()
    assert r["ok"] is True
    assert all("syncable" in t and "path" in t for t in r["targets"])


def test_tool_tables_agree():
    names = {d["name"] for d in DP.TOOL_DECLARATIONS}
    assert names == set(DP.TOOL_DISPATCH)
    assert DP.READONLY_TOOLS | DP.INTERACTION_TOOLS == names
    assert not (DP.READONLY_TOOLS & DP.INTERACTION_TOOLS)


def test_registered_with_the_agent():
    """agent.py can't be imported here (needs google-genai), so check the wiring in source:
    the module must be imported and included in the feature-merge loop that builds the central
    TOOL_DECLARATIONS/TOOL_DISPATCH tables."""
    src = open("agent.py", encoding="utf-8").read()
    assert "\nimport data_protect\n" in src
    assert "data_protect" in src.split("for _feat in (", 1)[1].split("):", 1)[0]


def test_risky_variants_are_high_risk():
    import safety
    assert safety.classify("adp_protect_folder", {"delete_originals": True})[0] == "high"
    assert safety.classify("adp_protect_folder", {})[0] == "medium"
    assert safety.classify("adp_reset", {})[0] == "high"
    # Handing decryption ability to another party is a bigger deal than encrypting.
    assert safety.classify("adp_add_recipient", {"public_key": "ember1:x"})[0] == "high"
    assert safety.classify("adp_grant_access", {"folder": "/tmp"})[0] == "high"


def test_every_tool_is_classified():
    import safety
    for name in DP.TOOL_DISPATCH:
        if name in DP.READONLY_TOOLS:
            continue
        risk, why = safety.classify(name, {})
        assert why != "unclassified tool", name


# --- Settings UI wiring ---------------------------------------------------------
def _ui_source():
    return open("ui.py", encoding="utf-8").read()


def test_security_tab_hosts_the_panel():
    """PyQt6 isn't installed here, so assert the wiring statically: the Security tab must call
    the panel builder, and every button handler must exist. (test_settings_dialog_methods.py
    separately proves those handlers resolve on the right class.)"""
    src = _ui_source()
    assert "self._populate_adp_section(v)" in src
    for handler in ("_adp_setup", "_adp_show_identity", "_adp_add_device", "_adp_protect_folder",
                    "_adp_unprotect_folder", "_adp_grant_access", "_refresh_adp_status",
                    "_adp_report"):
        assert f"def {handler}(self" in src, handler


def test_destructive_ui_paths_confirm_first():
    """Shredding originals and widening who can decrypt must each require an explicit yes —
    the panel is the one place a user can trigger them without the tool-risk prompt."""
    src = _ui_source()
    shred = src.split("def _adp_protect_folder", 1)[1].split("def _adp_unprotect_folder", 1)[0]
    assert "Shred the originals?" in shred
    # Default to Cancel, so a reflexive Return keypress never shreds.
    assert "QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes" in shred

    share = src.split("def _adp_grant_access", 1)[1].split("def _adp_report", 1)[0]
    assert "cannot be taken back" in share
    assert "QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes" in share

    setup = src.split("def _adp_setup", 1)[1].split("def _adp_show_identity", 1)[0]
    assert "There is no recovery" in setup  # stated before anything is encrypted with it


def test_ui_is_discoverable():
    assert "Advanced Data Protection" in _ui_source().split("Security & privacy", 1)[1][:2000]
