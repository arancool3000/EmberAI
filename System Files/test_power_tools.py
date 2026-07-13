"""Tests for power_tools."""
import tempfile
from pathlib import Path

import power_tools


def test_read_document_text_and_csv():
    d = tempfile.mkdtemp(prefix="doc_")
    p = Path(d) / "note.txt"
    p.write_text("Hello Ember.\nThis is a document.")
    r = power_tools.read_document(str(p))
    assert r["ok"] and "Ember" in r["text"] and r["chars"] > 10, r
    c = Path(d) / "data.csv"
    c.write_text("a,b\n1,2\n")
    assert power_tools.read_document(str(c))["ok"]


def test_read_document_missing():
    assert power_tools.read_document("/nope/x.pdf")["ok"] is False


def test_scan_secrets():
    txt = "here is my key AKIAABCDEFGHIJKLMNOP and email bob@example.com"
    r = power_tools.scan_secrets(txt)
    assert r["ok"] and r["has_secrets"]
    types = {f["type"] for f in r["found"]}
    assert any("AWS" in t for t in types) and any("Email" in t for t in types), r
    assert power_tools.scan_secrets("just normal words here")["has_secrets"] is False


def test_secure_delete():
    d = tempfile.mkdtemp(prefix="shred_")
    p = Path(d) / "secret.txt"
    p.write_text("delete me securely")
    r = power_tools.secure_delete(str(p), passes=2)
    assert r["ok"] and not p.exists(), r


def test_secure_delete_missing():
    assert power_tools.secure_delete("/nope/x")["ok"] is False


def test_unit_convert():
    assert power_tools.unit_convert(1, "km", "m")["result"] == 1000.0
    assert power_tools.unit_convert(100, "c", "f")["result"] == 212.0
    assert round(power_tools.unit_convert(1, "gb", "mb")["result"]) == 1024
    assert power_tools.unit_convert(1, "kg", "m")["ok"] is False  # incompatible
    assert power_tools.unit_convert(1, "km", "zz")["ok"] is False  # unknown


def _run():
    import types
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
