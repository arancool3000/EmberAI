"""Fast, deterministic tests for download_guard.

We never touch the real antivirus engine or the real Downloads folder:
  * `download_guard._SCANNER` is replaced with a fake that returns a result
    shaped like antivirus.scan_file (a "verdict" key: "malicious" for files
    whose path contains "evil", else "clean").
  * The guard is pointed at pytest's tmp_path so it watches an isolated folder.

Every test stops the watcher in a finally block so pytest exits cleanly.
"""
import time

import download_guard


def _fake_scanner(path):
    # Mirrors antivirus.scan_file's contract: success + a "verdict" key.
    if "evil" in path:
        return {"ok": True, "verdict": "malicious", "reasons": ["fake test threat"]}
    return {"ok": True, "verdict": "clean", "reasons": ["no indicators found"]}


def _results_by_name(limit=200):
    evts = download_guard.download_guard_events(limit=limit)["events"]
    return {e["name"]: e["result"] for e in evts}


def _wait_for(predicate, timeout=5.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_scans_new_clean_and_threat_files(tmp_path):
    download_guard._SCANNER = _fake_scanner
    try:
        started = download_guard.download_guard_start(str(tmp_path))
        assert started["ok"] is True
        assert started["watching"] is True
        assert download_guard.is_running() is True

        (tmp_path / "report.pdf").write_text("totally benign content")
        (tmp_path / "evil_payload.exe").write_text("pretend malware")

        def both_scanned():
            res = _results_by_name()
            return "report.pdf" in res and "evil_payload.exe" in res

        assert _wait_for(both_scanned, timeout=5.0), \
            f"files were not scanned in time: {_results_by_name()}"

        res = _results_by_name()
        assert res["report.pdf"] == "clean"
        assert res["evil_payload.exe"] == "threat"

        status = download_guard.download_guard_status()
        assert status["ok"] is True
        assert status["running"] is True
        assert status["threats_found"] >= 1
    finally:
        stopped = download_guard.download_guard_stop()
        assert stopped["ok"] is True
        assert download_guard.is_running() is False


def test_preexisting_file_not_reflagged(tmp_path):
    download_guard._SCANNER = _fake_scanner
    # Create a file BEFORE starting so priming should ignore it forever.
    (tmp_path / "evil_old.exe").write_text("present before start")
    try:
        download_guard.download_guard_start(str(tmp_path))

        # Add one genuinely new file so we have something to wait on.
        (tmp_path / "fresh.txt").write_text("arrived after start")

        assert _wait_for(lambda: "fresh.txt" in _results_by_name(), timeout=5.0), \
            f"new file never scanned: {_results_by_name()}"

        # Give the watcher a couple more poll cycles to (wrongly) pick up the old file.
        time.sleep(2.5)

        res = _results_by_name()
        assert "fresh.txt" in res
        assert "evil_old.exe" not in res, "pre-existing file was incorrectly flagged"
    finally:
        download_guard.download_guard_stop()


def test_start_is_idempotent(tmp_path):
    download_guard._SCANNER = _fake_scanner
    try:
        first = download_guard.download_guard_start(str(tmp_path))
        second = download_guard.download_guard_start(str(tmp_path))
        assert first["ok"] is True
        assert second["ok"] is True
        assert second["watching"] is True
        assert "already" in second["message"].lower()
    finally:
        download_guard.download_guard_stop()


def test_dispatch_matches_declarations():
    assert set(download_guard.TOOL_DISPATCH) == {
        d["name"] for d in download_guard.TOOL_DECLARATIONS
    }
    assert download_guard.READONLY_TOOLS == {
        "download_guard_status", "download_guard_events"
    }
    assert download_guard.INTERACTION_TOOLS == {
        "download_guard_start", "download_guard_stop"
    }


def test_unsigned_dmg_download_is_a_caution(tmp_path):
    """A clean-but-UNSIGNED .dmg download is surfaced as a caution with a plain-language
    explanation — not silently 'clean', and never a 'threat'."""
    def scanner(path):
        if path.lower().endswith(".dmg"):
            return {"ok": True, "verdict": "clean", "reasons": ["no indicators found"],
                    "signing": {"trust": "unsigned"}}
        return {"ok": True, "verdict": "clean", "reasons": []}
    download_guard._SCANNER = scanner
    try:
        download_guard.download_guard_start(str(tmp_path))
        (tmp_path / "OpenApp.dmg").write_bytes(b"koly\x00\x00")

        assert _wait_for(lambda: "OpenApp.dmg" in _results_by_name(), timeout=6.0), \
            _results_by_name()
        evts = {e["name"]: e for e in download_guard.download_guard_events(limit=50)["events"]}
        e = evts["OpenApp.dmg"]
        assert e["result"] == "caution", e
        assert "signed or notarized" in e["detail"].lower(), e
    finally:
        download_guard.download_guard_stop()
        download_guard._SCANNER = None


def test_notarized_dmg_download_is_not_nagged(tmp_path):
    """A notarized app from an identified developer (VS Code, etc.) is trusted — no scary alert."""
    def scanner(path):
        return {"ok": True, "verdict": "clean", "reasons": [], "signing": {"trust": "trusted"}}
    download_guard._SCANNER = scanner
    try:
        download_guard.download_guard_start(str(tmp_path))
        (tmp_path / "VSCode.dmg").write_bytes(b"koly\x00\x00")

        assert _wait_for(lambda: "VSCode.dmg" in _results_by_name(), timeout=6.0), \
            _results_by_name()
        assert _results_by_name()["VSCode.dmg"] == "clean"    # trusted -> no caution
    finally:
        download_guard.download_guard_stop()
        download_guard._SCANNER = None
