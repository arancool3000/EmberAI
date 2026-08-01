"""Tests for adp_organise — sorting protected photos without exposing them.

The classifier is stubbed throughout. What matters here is not whether a vision model picks the
right album, but that sorting never writes plaintext to disk, never silently ships photos to a
cloud, and never turns folder names into a searchable index of what you encrypted.
"""
import json
from pathlib import Path

import pytest

import key_vault as KV
import data_protect as DP
import adp_organise as ORG

MARKER = b"\xff\xd8\xff-unique-photo-content-marker"


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "recipients.json")
    monkeypatch.setattr(DP, "LEVEL_FILE", tmp_path / "level.json")
    assert DP.adp_setup("a good long passphrase")["ok"] is True
    return tmp_path


@pytest.fixture
def album(tmp_path):
    """A folder of protected photos, originals shredded."""
    d = tmp_path / "Ember Photos"
    d.mkdir()
    for i, body in enumerate((MARKER, MARKER + b"2", MARKER + b"3")):
        p = d / f"IMG_000{i}.JPG"
        p.write_bytes(body)
        assert DP.adp_protect_file(str(p), delete_original=True)["ok"] is True
    return d


def _stub(monkeypatch, category="people", ok=True):
    calls = []
    monkeypatch.setattr(ORG, "local_vision_models", lambda: ["llava:latest"])

    def fake(image, *a, **k):
        calls.append(image)
        return {"ok": ok, "category": category, "model": "stub"} if ok else \
               {"ok": False, "error": "no model"}
    monkeypatch.setattr(ORG, "classify_local", fake)
    monkeypatch.setattr(ORG, "classify_cloud", fake)
    return calls


# --- the two rules it must not bend ---------------------------------------------
def test_no_plaintext_is_ever_written_to_disk(isolate, album, monkeypatch):
    """The classifier sees the photo in memory; the disk only ever holds ciphertext."""
    _stub(monkeypatch)
    ORG.organise(str(album))
    for f in isolate.rglob("*"):
        if f.is_file():
            assert MARKER not in f.read_bytes(), f


def test_the_encrypted_file_is_what_moves(isolate, album, monkeypatch):
    _stub(monkeypatch, "documents")
    r = ORG.organise(str(album))
    assert r["ok"] is True and r["sorted_count"] == 3
    moved = list(album.rglob(f"*{DP.EXT}"))
    assert all(m.suffix == DP.EXT for m in moved)
    # And they still decrypt afterwards.
    target = [m for m in moved if m.name.startswith("IMG_0000")][0]
    assert DP.adp_unprotect_file(str(target), dest_dir=str(isolate / "back"))["ok"] is True
    assert (isolate / "back" / "IMG_0000.JPG").read_bytes() == MARKER


def test_the_classifier_actually_sees_the_decrypted_photo(isolate, album, monkeypatch):
    calls = _stub(monkeypatch)
    ORG.organise(str(album))
    assert len(calls) == 3
    assert MARKER in calls[0]          # it got real pixels, not the ciphertext


def test_cloud_is_never_used_without_being_asked(isolate, album, monkeypatch):
    """Shipping decrypted photos to a cloud API must be an explicit choice, not a fallback."""
    cloud_calls = []
    monkeypatch.setattr(ORG, "classify_cloud",
                        lambda *a, **k: cloud_calls.append(1) or {"ok": True, "category": "other"})
    monkeypatch.setattr(ORG, "local_vision_models", lambda: [])
    r = ORG.organise(str(album))
    assert r["ok"] is False
    assert "no local vision model" in r["error"]
    assert "allow_cloud" in r["hint"]
    assert not cloud_calls


def test_cloud_says_what_it_did(isolate, album, monkeypatch):
    _stub(monkeypatch)
    r = ORG.organise(str(album), allow_cloud=True)
    assert r["classifier"] == "cloud"
    assert "sent to your configured cloud model" in r["warning_cloud"]


# --- the metadata problem -------------------------------------------------------
def test_private_names_do_not_leak_the_topic(isolate, album, monkeypatch):
    """Folder names are visible to iCloud. An album called 'documents' is a searchable index of
    exactly what the encryption was for."""
    _stub(monkeypatch, "documents")
    r = ORG.organise(str(album), private_names=True)
    assert r["ok"] is True
    names = [d.name for d in album.iterdir() if d.is_dir()]
    assert names and all(n.startswith("group-") for n in names)
    assert "documents" not in " ".join(names)


def test_the_album_index_is_itself_encrypted(isolate, album, monkeypatch):
    _stub(monkeypatch, "receipts")
    ORG.organise(str(album), private_names=True)
    idx = album / ORG.INDEX_NAME
    assert idx.exists()
    assert b"receipts" not in idx.read_bytes()      # the mapping is not readable on disk
    assert idx.read_bytes().startswith(DP.MAGIC)


def test_albums_reads_the_index_back(isolate, album, monkeypatch):
    _stub(monkeypatch, "receipts")
    ORG.organise(str(album), private_names=True)
    r = ORG.adp_albums(str(album))
    assert r["ok"] is True
    assert [a["album"] for a in r["albums"]] == ["receipts"]
    assert r["albums"][0]["count"] == 3


def test_readable_names_come_with_the_warning(isolate, album, monkeypatch):
    _stub(monkeypatch, "receipts")
    r = ORG.organise(str(album), private_names=False)
    assert (album / "receipts").is_dir()
    assert "NOT encrypted" in r["warning"]
    assert "searchable index" in r["warning"]


def test_a_stranger_cannot_read_the_index(isolate, album, monkeypatch):
    _stub(monkeypatch, "medical" if "medical" in ORG.CATEGORIES else "documents")
    ORG.organise(str(album), private_names=True)
    idx = album / ORG.INDEX_NAME
    monkeypatch.setattr(KV, "VAULT_FILE", isolate / "other.enc")
    monkeypatch.setattr(KV, "KEY_FILE", isolate / "other.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", isolate / "other.json")
    DP.adp_setup("an unrelated passphrase")
    assert ORG.adp_albums(str(album))["albums"] == []
    assert DP.adp_unprotect_file(str(idx), dest_dir=str(isolate / "x"))["ok"] is False


# --- ordinary behaviour ---------------------------------------------------------
def test_refuses_without_protection(monkeypatch, tmp_path):
    DP.adp_reset()
    assert ORG.organise(str(tmp_path))["ok"] is False


def test_refuses_a_missing_folder(isolate):
    assert ORG.organise(str(isolate / "nope"))["ok"] is False


def test_empty_folder_is_not_an_error(isolate, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    r = ORG.organise(str(d))
    assert r["ok"] is True and r["sorted_count"] == 0


def test_a_failing_classifier_leaves_the_file_alone(isolate, album, monkeypatch):
    _stub(monkeypatch, ok=False)
    r = ORG.organise(str(album), allow_cloud=True)
    assert r["sorted_count"] == 0 and len(r["failed"]) == 3
    assert len(list(album.glob(f"*{DP.EXT}"))) == 3   # still where they were


def test_limit_caps_the_work(isolate, album, monkeypatch):
    _stub(monkeypatch)
    r = ORG.organise(str(album), limit=1)
    assert r["sorted_count"] == 1


def test_the_index_is_never_itself_organised(isolate, album, monkeypatch):
    """The index lives in the folder as a .ember file; sorting it into an album would be silly
    and would break adp_albums."""
    _stub(monkeypatch)
    ORG.organise(str(album), private_names=True)
    ORG.organise(str(album), private_names=True)
    assert (album / ORG.INDEX_NAME).exists()


def test_answers_are_reduced_to_a_known_category(monkeypatch):
    """The model is asked for one word, but models editorialise. Anything recognisable maps to
    its category; anything else becomes 'other' rather than inventing an album."""
    assert ORG._normalise("animals") == "animals"
    assert ORG._normalise("  Documents.  ") == "documents"
    assert ORG._normalise("This looks like receipts to me") == "receipts"
    assert ORG._normalise("a dog on a beach") == "other"
    assert ORG._normalise("") == "other"


# --- wiring ---------------------------------------------------------------------
def test_tool_tables_agree():
    names = {d["name"] for d in ORG.TOOL_DECLARATIONS}
    assert names == set(ORG.TOOL_DISPATCH)
    assert ORG.READONLY_TOOLS | ORG.INTERACTION_TOOLS == names


def test_registered_with_the_agent():
    src = open("agent.py", encoding="utf-8").read()
    assert "\nimport adp_organise\n" in src
    assert "adp_organise" in src.split("for _feat in (", 1)[1].split("):", 1)[0]


def test_cloud_organising_is_high_risk():
    import safety
    assert safety.classify("adp_organise", {"allow_cloud": True})[0] == "high"
    assert safety.classify("adp_organise", {"folder": "/x"})[0] == "medium"
