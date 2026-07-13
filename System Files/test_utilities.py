"""Tests for Ember's multitool utilities (utilities.py).

    pytest test_utilities.py
    python test_utilities.py
"""
import os
import tempfile
from pathlib import Path

import utilities


def test_disk_usage_lists_biggest():
    d = tempfile.mkdtemp(prefix="ember_du_")
    (Path(d) / "a.bin").write_bytes(b"x" * 2048)
    sub = Path(d) / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 4096)
    r = utilities.disk_usage(d, top=10)
    assert r["ok"] and len(r["items"]) >= 2 and r["total_mb"] >= 0, r


def test_disk_usage_missing_path():
    assert utilities.disk_usage("/no/such/path/xyz123")["ok"] is False


def test_password_strength_ratings():
    assert "weak" in utilities.password_strength("password")["rating"]
    r = utilities.password_strength("Xk9$mQ2!pLz7vB")
    assert r["ok"] and r["entropy_bits"] > 60 and "strong" in r["rating"], r
    assert utilities.password_strength("")["ok"] is False


def test_list_open_ports_is_graceful():
    r = utilities.list_open_ports()
    assert "ok" in r
    if r["ok"]:
        assert "listening" in r
    else:
        assert "error" in r  # e.g. psutil unavailable / needs privileges


def test_system_health_is_graceful():
    r = utilities.system_health()
    assert "ok" in r
    if r["ok"]:
        assert "cpu_percent" in r and "memory_percent" in r and "disk_free_gb" in r
    else:
        assert "error" in r


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
