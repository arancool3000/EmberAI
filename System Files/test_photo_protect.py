"""Tests for photo_protect — Ember's Advanced Data Protection for photos.

key_vault is pointed at a tmp_path so the real vault is never touched, and the passphrase
is stored through the normal vault path (encrypted-file backend in this environment)."""
import key_vault as KV
import photo_protect as PP


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")


def _setup(monkeypatch, tmp_path, passphrase="correct horse battery"):
    _isolate(monkeypatch, tmp_path)
    assert PP.adp_setup(passphrase)["ok"] is True
    return passphrase


def _photo(tmp_path, name="holiday.jpg", data=b"\xff\xd8\xff-real-photo-bytes-secret"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- setup ---------------------------------------------------------------------
def test_not_configured_until_setup(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert PP.is_set_up() is False
    st = PP.adp_status()
    assert st["ok"] is True and st["configured"] is False
    # Every protect/unprotect tool must refuse before a passphrase exists.
    assert PP.adp_protect_photo(str(tmp_path / "x.jpg"))["ok"] is False
    assert PP.adp_protect_folder(str(tmp_path))["ok"] is False


def test_setup_rejects_short_passphrase(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert PP.adp_setup("short")["ok"] is False
    assert PP.is_set_up() is False


def test_setup_refuses_to_silently_replace(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = PP.adp_setup("a different passphrase")
    assert r["ok"] is False and "already set" in r["error"]
    # The original passphrase must survive the rejected call.
    assert KV.get_key(PP.VAULT_KEY) == "correct horse battery"


def test_reset_clears(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert PP.adp_reset()["cleared"] is True
    assert PP.is_set_up() is False


def test_status_never_leaks_passphrase(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "super-secret-phrase")
    assert "super-secret-phrase" not in repr(PP.adp_status())


# --- single file ---------------------------------------------------------------
def test_roundtrip(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    original = src.read_bytes()

    r = PP.adp_protect_photo(str(src))
    assert r["ok"] is True
    enc = tmp_path / "holiday.jpg.ember"
    assert enc.exists()
    # What would reach iCloud must not contain the photo bytes, and must be tagged as ours.
    blob = enc.read_bytes()
    assert b"real-photo-bytes-secret" not in blob
    assert blob.startswith(PP.MAGIC)

    src.unlink()
    r = PP.adp_unprotect_photo(str(enc))
    assert r["ok"] is True
    assert (tmp_path / "holiday.jpg").read_bytes() == original


def test_salt_is_per_file(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    a = _photo(tmp_path, "a.jpg", b"identical")
    b = _photo(tmp_path, "b.jpg", b"identical")
    PP.adp_protect_photo(str(a))
    PP.adp_protect_photo(str(b))
    # Same plaintext, same passphrase — the ciphertext must still differ.
    assert (tmp_path / "a.jpg.ember").read_bytes() != (tmp_path / "b.jpg.ember").read_bytes()


def test_wrong_passphrase_fails_loudly(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    PP.adp_protect_photo(str(src))
    src.unlink()
    KV.set_key(PP.VAULT_KEY, "the wrong passphrase entirely")
    r = PP.adp_unprotect_photo(str(tmp_path / "holiday.jpg.ember"))
    assert r["ok"] is False and "wrong passphrase" in r["error"]
    assert not (tmp_path / "holiday.jpg").exists()  # no garbage written


def test_tampered_file_is_rejected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    PP.adp_protect_photo(str(src))
    enc = tmp_path / "holiday.jpg.ember"
    blob = bytearray(enc.read_bytes())
    blob[-5] ^= 0xFF
    enc.write_bytes(bytes(blob))
    assert PP.adp_unprotect_photo(str(enc))["ok"] is False


def test_delete_original_shreds(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    r = PP.adp_protect_photo(str(src), delete_original=True)
    assert r["ok"] is True and r["original_deleted"] is True
    assert not src.exists()


def test_never_overwrites_existing_output(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    assert PP.adp_protect_photo(str(src))["ok"] is True
    r = PP.adp_protect_photo(str(src))
    assert r["ok"] is False and "already exists" in r["error"]


def test_rejects_missing_and_non_ember_files(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert PP.adp_protect_photo(str(tmp_path / "nope.jpg"))["ok"] is False
    plain = _photo(tmp_path, "plain.jpg")
    r = PP.adp_unprotect_photo(str(plain))
    assert r["ok"] is False and "not an Ember-protected file" in r["error"]


def test_dest_dir_targets_a_sync_folder(monkeypatch, tmp_path):
    """The real use case: protect out of Pictures and into the iCloud Drive folder."""
    _setup(monkeypatch, tmp_path)
    src = _photo(tmp_path)
    icloud = tmp_path / "iCloudDrive"
    r = PP.adp_protect_photo(str(src), dest_dir=str(icloud))
    assert r["ok"] is True
    assert (icloud / "holiday.jpg.ember").exists()
    assert not (tmp_path / "holiday.jpg.ember").exists()


# --- folders --------------------------------------------------------------------
def test_protect_folder_skips_non_photos(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _photo(tmp_path, "one.jpg")
    _photo(tmp_path, "two.HEIC")
    (tmp_path / "notes.txt").write_text("not a photo")
    r = PP.adp_protect_folder(str(tmp_path))
    assert r["ok"] is True and r["protected_count"] == 2
    assert not (tmp_path / "notes.txt.ember").exists()


def test_protect_folder_recursive_preserves_tree(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sub = tmp_path / "2024" / "spain"
    sub.mkdir(parents=True)
    _photo(tmp_path, "top.jpg", b"top-bytes")
    _photo(sub, "same.jpg", b"nested-bytes")
    _photo(tmp_path, "same.jpg", b"root-bytes")
    out = tmp_path.parent / "out"
    r = PP.adp_protect_folder(str(tmp_path), dest_dir=str(out), recursive=True)
    assert r["ok"] is True and r["protected_count"] == 3
    # Same-named photos in different subfolders must not collide in the destination.
    assert (out / "same.jpg.ember").exists()
    assert (out / "2024" / "spain" / "same.jpg.ember").exists()

    back = tmp_path.parent / "back"
    u = PP.adp_unprotect_folder(str(out), dest_dir=str(back), recursive=True)
    assert u["ok"] is True and u["restored_count"] == 3
    assert (back / "2024" / "spain" / "same.jpg").read_bytes() == b"nested-bytes"
    assert (back / "same.jpg").read_bytes() == b"root-bytes"


def test_protect_folder_non_recursive_ignores_subfolders(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    _photo(sub, "deep.jpg")
    _photo(tmp_path, "top.jpg")
    r = PP.adp_protect_folder(str(tmp_path))
    assert r["protected_count"] == 1


def test_protect_folder_missing(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert PP.adp_protect_folder(str(tmp_path / "nope"))["ok"] is False


# --- wiring ---------------------------------------------------------------------
def test_icloud_targets_flags_photos_library():
    r = PP.adp_icloud_targets()
    assert r["ok"] is True
    # Whatever the platform, every entry must say whether it can hold encrypted blobs.
    assert all("syncable" in t and "path" in t for t in r["targets"])


def test_tool_tables_agree():
    names = {d["name"] for d in PP.TOOL_DECLARATIONS}
    assert names == set(PP.TOOL_DISPATCH)
    assert PP.READONLY_TOOLS | PP.INTERACTION_TOOLS == names
    assert not (PP.READONLY_TOOLS & PP.INTERACTION_TOOLS)


def test_registered_with_the_agent():
    """agent.py can't be imported here (needs google-genai), so check the wiring in source:
    the module must be imported and included in the feature-merge loop that builds the
    central TOOL_DECLARATIONS/TOOL_DISPATCH tables."""
    src = open("agent.py", encoding="utf-8").read()
    assert "\nimport photo_protect\n" in src
    merge = src.split("for _feat in (", 1)[1].split("):", 1)[0]
    assert "photo_protect" in merge


def test_shredding_variants_are_high_risk():
    import safety
    assert safety.classify("adp_protect_folder", {"delete_originals": True})[0] == "high"
    assert safety.classify("adp_protect_folder", {})[0] == "medium"
    assert safety.classify("adp_reset", {})[0] == "high"
