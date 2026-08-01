"""Tests for adp_watch — the folder auto-protect watcher.

The watch loop is driven directly rather than through the background thread wherever possible,
so the tests assert behaviour instead of racing a timer. The threaded path gets its own test.
"""
import threading
import time
from pathlib import Path

import pytest

import key_vault as KV
import data_protect as DP
import adp_watch as W


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """Throwaway vault, recipient list and watcher config; always stop the thread after."""
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "adp_recipients.json")
    monkeypatch.setattr(W, "CONFIG_FILE", tmp_path / "adp_watch.json")
    W._STATE.update(thread=None, stop=None, events=[], protected=0, failed=0, config=None)
    yield
    W.stop_watching(persist=False)


@pytest.fixture
def ready(tmp_path):
    assert DP.adp_setup("a good long passphrase")["ok"] is True
    src = tmp_path / "import"
    dst = tmp_path / "iCloudDrive"
    src.mkdir()
    dst.mkdir()
    return src, dst


def _old_file(folder, name="IMG_0001.jpg", data=b"\xff\xd8\xff-camera-roll-bytes"):
    """A file that already looks settled — written, and back-dated past min_age."""
    p = folder / name
    p.write_bytes(data)
    past = time.time() - 60
    import os
    os.utime(p, (past, past))
    return p


def _drain(cfg, times=2):
    """Run the loop body without the thread: `times` polls, then stop."""
    stop = threading.Event()
    calls = {"n": 0}
    real_wait = stop.wait

    def _wait(_timeout):
        calls["n"] += 1
        if calls["n"] >= times:
            stop.set()
        return real_wait(0)

    stop.wait = _wait  # type: ignore[method-assign]
    W._loop(cfg, stop)


def _cfg(src, dst, **over):
    c = {"source": str(src), "dest": str(dst), "delete_originals": False,
         "all_files": True, "interval": 0.01, "min_age": 0.0}
    c.update(over)
    return c


# --- start/stop -----------------------------------------------------------------
def test_refuses_without_setup(tmp_path):
    r = W.adp_watch_start(str(tmp_path))
    assert r["ok"] is False and "not set up" in r["error"]


def test_refuses_a_missing_folder(ready, tmp_path):
    r = W.adp_watch_start(str(tmp_path / "nope"))
    assert r["ok"] is False and "no such folder" in r["error"]


def test_refuses_to_start_twice(ready):
    src, dst = ready
    assert W.adp_watch_start(str(src), str(dst))["ok"] is True
    r = W.adp_watch_start(str(src), str(dst))
    assert r["ok"] is False and "already watching" in r["error"]


def test_status_reflects_running_state(ready):
    src, dst = ready
    assert W.adp_watch_status()["running"] is False
    W.adp_watch_start(str(src), str(dst))
    st = W.adp_watch_status()
    assert st["running"] is True and st["source"] == str(src)
    W.adp_watch_stop()
    assert W.adp_watch_status()["running"] is False


# --- the actual protecting ------------------------------------------------------
def test_encrypts_what_lands_in_the_folder(ready):
    src, dst = ready
    _old_file(src)
    _drain(_cfg(src, dst))
    out = dst / "IMG_0001.jpg.ember"
    assert out.exists()
    assert b"camera-roll-bytes" not in out.read_bytes()
    assert (src / "IMG_0001.jpg").exists()  # original kept by default


def test_encrypted_output_actually_decrypts(ready):
    src, dst = ready
    _old_file(src, data=b"the real photo")
    _drain(_cfg(src, dst))
    back = dst / "restored"
    r = DP.adp_unprotect_folder(str(dst), dest_dir=str(back))
    assert r["restored_count"] == 1
    assert (back / "IMG_0001.jpg").read_bytes() == b"the real photo"


def test_mirrors_the_source_tree(ready):
    src, dst = ready
    sub = src / "2026" / "june"
    sub.mkdir(parents=True)
    _old_file(src, "same.jpg", b"root")
    _old_file(sub, "same.jpg", b"nested")
    _drain(_cfg(src, dst))
    assert (dst / "same.jpg.ember").exists()
    assert (dst / "2026" / "june" / "same.jpg.ember").exists()


def test_does_not_reprotect_its_own_output(ready):
    """Destination inside the source would otherwise feed the watcher its own ciphertext."""
    src, _ = ready
    inner = src / "protected"
    inner.mkdir()
    _old_file(src)
    _drain(_cfg(src, inner), times=3)
    assert (inner / "IMG_0001.jpg.ember").exists()
    assert not (inner / "IMG_0001.jpg.ember.ember").exists()
    assert not list(inner.rglob("*.ember.ember"))


def test_each_file_is_protected_once(ready):
    src, dst = ready
    _old_file(src)
    _drain(_cfg(src, dst), times=4)
    assert W._STATE["protected"] == 1


def test_photos_only_mode_leaves_other_files(ready):
    src, dst = ready
    _old_file(src, "IMG_0001.jpg")
    _old_file(src, "notes.txt", b"not a photo")
    _drain(_cfg(src, dst, all_files=False))
    assert (dst / "IMG_0001.jpg.ember").exists()
    assert not (dst / "notes.txt.ember").exists()


def test_shreds_originals_only_when_asked(ready):
    src, dst = ready
    _old_file(src)
    _drain(_cfg(src, dst, delete_originals=True))
    assert (dst / "IMG_0001.jpg.ember").exists()
    assert not (src / "IMG_0001.jpg").exists()


def test_a_file_still_being_written_is_left_alone(ready):
    """A photo mid-copy would encrypt cleanly as a truncated file — valid ciphertext of the
    wrong bytes, which reads as success. It must wait until the size stops changing."""
    src, dst = ready
    p = src / "IMG_0002.jpg"
    p.write_bytes(b"first chunk")
    seen = {}
    # First sighting: never stable, whatever its age.
    assert W._stable(p, seen, min_age=0.0) is False
    # Still growing between polls: still not stable.
    p.write_bytes(b"first chunk" + b"second chunk")
    assert W._stable(p, seen, min_age=0.0) is False
    # Unchanged since the previous poll: now safe.
    assert W._stable(p, seen, min_age=0.0) is True


def test_min_age_holds_a_just_written_file(ready):
    src, _ = ready
    p = src / "IMG_0003.jpg"
    p.write_bytes(b"fresh")
    seen = {}
    W._stable(p, seen, min_age=30.0)
    assert W._stable(p, seen, min_age=30.0) is False  # unchanged, but too new to trust


def test_failures_are_recorded_not_swallowed(ready, monkeypatch):
    src, dst = ready
    _old_file(src)

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(DP, "encrypt_file", _boom)
    _drain(_cfg(src, dst))
    assert W._STATE["failed"] == 1
    kinds = [e["kind"] for e in W.adp_watch_status()["recent_events"]]
    assert "failed" in kinds
    assert (src / "IMG_0001.jpg").exists()  # a failed encrypt must never shred


def test_shred_does_not_run_when_encryption_failed(ready, monkeypatch):
    src, dst = ready
    _old_file(src)
    monkeypatch.setattr(DP, "encrypt_file", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    _drain(_cfg(src, dst, delete_originals=True))
    assert (src / "IMG_0001.jpg").exists()


def test_keeps_running_when_protection_is_reset(ready):
    """Losing the passphrase mid-watch must not kill the thread silently."""
    src, dst = ready
    DP.adp_reset()
    _drain(_cfg(src, dst))
    kinds = [e["kind"] for e in W.adp_watch_status()["recent_events"]]
    assert "idle" in kinds


# --- persistence ----------------------------------------------------------------
def test_config_persists_and_resumes(ready):
    src, dst = ready
    W.adp_watch_start(str(src), str(dst))
    W.stop_watching(persist=False)          # simulate a quit, leaving enabled=True on disk
    W._STATE.update(thread=None, stop=None)
    r = W.resume_if_enabled()
    assert r["resumed"] is True
    assert W.is_running() is True
    assert W.adp_watch_status()["source"] == str(src)


def test_stop_clears_the_resume_flag(ready):
    src, dst = ready
    W.adp_watch_start(str(src), str(dst))
    W.adp_watch_stop()
    assert W.load_config().get("enabled") is False
    assert W.resume_if_enabled()["resumed"] is False


def test_resume_is_a_no_op_without_protection(ready):
    src, dst = ready
    W.adp_watch_start(str(src), str(dst))
    W.stop_watching(persist=False)
    DP.adp_reset()
    r = W.resume_if_enabled()
    assert r["resumed"] is False and "not set up" in r["reason"]


def test_resume_is_a_no_op_when_never_configured():
    assert W.resume_if_enabled()["resumed"] is False


# --- the threaded path ----------------------------------------------------------
def test_the_background_thread_really_protects(ready):
    """Everything above drives the loop directly; this one proves the thread does its job."""
    src, dst = ready
    W.start(str(src), str(dst), interval=0.05, min_age=0.0)
    _old_file(src, "threaded.jpg", b"via the thread")
    out = dst / "threaded.jpg.ember"
    for _ in range(100):                      # up to ~5s, exits as soon as it appears
        if out.exists():
            break
        time.sleep(0.05)
    assert out.exists(), "watcher thread did not protect the file"
    r = W.adp_watch_stop()
    assert r["was_running"] is True


# --- wiring ---------------------------------------------------------------------
def test_tool_tables_agree():
    names = {d["name"] for d in W.TOOL_DECLARATIONS}
    assert names == set(W.TOOL_DISPATCH)
    assert W.READONLY_TOOLS | W.INTERACTION_TOOLS == names
    assert not (W.READONLY_TOOLS & W.INTERACTION_TOOLS)


def test_registered_with_the_agent():
    src = open("agent.py", encoding="utf-8").read()
    assert "\nimport adp_watch\n" in src
    assert "adp_watch" in src.split("for _feat in (", 1)[1].split("):", 1)[0]


def test_shredding_variant_is_high_risk():
    import safety
    assert safety.classify("adp_watch_start", {"delete_originals": True})[0] == "high"
    assert safety.classify("adp_watch_start", {"folder": "/x"})[0] == "medium"
