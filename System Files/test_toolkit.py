"""Tests for Ember's cleanup / network / media / privacy toolkits.

Deterministic where possible (port scan against a local listener, file info,
encrypt/decrypt round-trip when openssl exists); graceful-degradation checks for
platform/network-dependent tools.

    pytest test_toolkit.py
    python test_toolkit.py
"""
import hashlib
import os
import shutil
import socket
import tempfile
from pathlib import Path

import cleanup
import nettools
import mediatools
import privacy


# ------------------------------- cleanup -----------------------------------

def test_clean_temp_dry_run_never_deletes():
    r = cleanup.clean_temp(dry_run=True)
    assert r["ok"] and r["dry_run"] is True and r["deleted_files"] == 0, r
    assert "reclaimable_mb" in r and isinstance(r["scanned_dirs"], list)


def test_list_startup_items():
    r = cleanup.list_startup_items()
    assert r["ok"] and isinstance(r["items"], list)


# ------------------------------- network -----------------------------------

def test_scan_host_ports_finds_open_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        r = nettools.scan_host_ports("127.0.0.1", [port])
        assert r["ok"] and any(o["port"] == port for o in r["open"]), r
    finally:
        srv.close()


def test_scan_host_ports_bad_host():
    assert nettools.scan_host_ports("nonexistent.invalid.", [80])["ok"] is False


def test_network_devices_graceful():
    r = nettools.network_devices()
    assert "ok" in r
    if r["ok"]:
        assert "devices" in r
    else:
        assert "error" in r


def test_wifi_info_graceful():
    r = nettools.wifi_info()
    assert "ok" in r


# ------------------------------- media -------------------------------------

def test_file_info_hashes():
    d = tempfile.mkdtemp(prefix="ember_fi_")
    p = Path(d) / "f.bin"
    p.write_bytes(b"abc123")
    r = mediatools.file_info(str(p))
    assert r["ok"] and r["size_bytes"] == 6
    assert r["sha256"] == hashlib.sha256(b"abc123").hexdigest()


def test_media_convert_graceful_without_ffmpeg():
    d = tempfile.mkdtemp(prefix="ember_mc_")
    src = Path(d) / "a.txt"
    src.write_text("x")
    r = mediatools.media_convert(str(src), str(Path(d) / "b.mp3"))
    assert "ok" in r
    if not r["ok"]:
        assert "error" in r


# ------------------------------- privacy -----------------------------------

def test_password_pwned_check_empty():
    assert privacy.password_pwned_check("")["ok"] is False


def test_encrypt_decrypt_roundtrip():
    if not shutil.which("openssl"):
        return  # openssl unavailable -> skip the round-trip
    d = tempfile.mkdtemp(prefix="ember_enc_")
    src = Path(d) / "secret.txt"
    src.write_text("top secret data 123")
    enc = privacy.encrypt_file(str(src), "pw123", str(Path(d) / "secret.enc"))
    assert enc["ok"], enc
    out = privacy.decrypt_file(enc["output"], "pw123", str(Path(d) / "out.txt"))
    assert out["ok"], out
    assert Path(out["output"]).read_text() == "top secret data 123"


def test_keychain_graceful():
    r = privacy.keychain_get("ember_test_missing_key")
    assert "ok" in r  # not found / unsupported -> graceful, never raises


def test_network_connections_graceful():
    r = nettools.network_connections()
    assert "ok" in r
    if r["ok"]:
        assert "connections" in r


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
