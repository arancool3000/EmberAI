"""Hermetic tests for bulk_tools.py — folder triage, bulk document read, and the
dry-run/apply organise + rename flows. Uses a throwaway temp folder; no network, no PyQt.
Run: python test_bulk_tools.py"""
import os
import tempfile
from pathlib import Path

import bulk_tools as bt


def _make_folder():
    d = Path(tempfile.mkdtemp(prefix="ember_bulk_"))
    (d / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "notes.txt").write_text("hello world\nsecond line", encoding="utf-8")
    (d / "todo.md").write_text("# tasks\n- a\n- b", encoding="utf-8")
    (d / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00")
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftyp")
    (d / "archive.zip").write_bytes(b"PK\x03\x04")
    (d / ".hidden").write_text("ignore me", encoding="utf-8")
    return d


def test_folder_report():
    d = _make_folder()
    r = bt.folder_report(str(d))
    assert r["ok"] and r["file_count"] == 6          # hidden file excluded
    assert r["documents"] == 3                        # pdf, txt, md
    cats = r["by_category"]
    assert cats.get("Documents") == 3 and cats.get("Images") == 1
    assert cats.get("Video") == 1 and cats.get("Archives") == 1
    assert r["biggest"] and r["newest"]


def test_folder_report_bad_path():
    assert bt.folder_report("/no/such/folder/xyz")["ok"] is False


def test_bulk_read_documents():
    d = _make_folder()
    r = bt.bulk_read_documents(str(d), pattern="*.txt")
    assert r["ok"] and r["count"] == 1
    assert "hello world" in r["documents"][0]["text"]
    # all docs (no jpg/mp4/zip)
    r2 = bt.bulk_read_documents(str(d))
    names = {doc["name"] for doc in r2["documents"]}
    assert names == {"report.pdf", "notes.txt", "todo.md"}


def test_organize_folder_dry_run_then_apply():
    d = _make_folder()
    plan = bt.organize_folder(str(d), by="type", apply=False)
    assert plan["ok"] and plan["dry_run"] is True
    assert plan["would_move"] == 6
    assert plan["by_bucket"].get("Documents") == 3
    # nothing moved yet
    assert (d / "report.pdf").exists()

    done = bt.organize_folder(str(d), by="type", apply=True)
    assert done["ok"] and done["dry_run"] is False and done["moved"] == 6
    assert (d / "Documents" / "report.pdf").exists()
    assert (d / "Images" / "photo.jpg").exists()
    assert (d / "Video" / "clip.mp4").exists()
    assert not (d / "report.pdf").exists()


def test_organize_no_clobber():
    d = Path(tempfile.mkdtemp(prefix="ember_bulk_"))
    (d / "a.txt").write_text("one", encoding="utf-8")
    (d / "Documents").mkdir()
    (d / "Documents" / "a.txt").write_text("existing", encoding="utf-8")
    bt.organize_folder(str(d), by="type", apply=True)
    # the existing Documents/a.txt is preserved; the moved one gets a (2) suffix
    assert (d / "Documents" / "a.txt").read_text() == "existing"
    assert (d / "Documents" / "a (2).txt").exists()


def test_bulk_rename_dry_run_then_apply():
    d = Path(tempfile.mkdtemp(prefix="ember_bulk_"))
    for n in ("IMG_1.jpg", "IMG_2.jpg", "keep.txt"):
        (d / n).write_bytes(b"x")
    plan = bt.bulk_rename(str(d), find="IMG_", replace="vacation_", pattern="*.jpg", apply=False)
    assert plan["ok"] and plan["dry_run"] and plan["would_rename"] == 2
    assert (d / "IMG_1.jpg").exists()                 # not yet

    done = bt.bulk_rename(str(d), find="IMG_", replace="vacation_", pattern="*.jpg", apply=True)
    assert done["ok"] and done["renamed"] == 2
    assert (d / "vacation_1.jpg").exists() and (d / "vacation_2.jpg").exists()
    assert (d / "keep.txt").exists()                  # untouched (didn't match pattern)


def test_bulk_rename_requires_find():
    d = Path(tempfile.mkdtemp(prefix="ember_bulk_"))
    assert bt.bulk_rename(str(d), find="")["ok"] is False


def test_exports_consistent():
    assert set(bt.TOOL_DISPATCH) == {x["name"] for x in bt.TOOL_DECLARATIONS}
    assert bt.READONLY_TOOLS == {"folder_report", "bulk_read_documents"}
    assert bt.INTERACTION_TOOLS == {"organize_folder", "bulk_rename"}
    assert bt.READONLY_TOOLS.isdisjoint(bt.INTERACTION_TOOLS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} bulk_tools tests passed")
