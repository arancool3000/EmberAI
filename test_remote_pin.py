"""Hermetic tests for Ember Link's stable PIN persistence (remote_server.stable_pin()).

Monkeypatches _data_dir() to a fresh temp directory per test so nothing ever touches the real
~/.ember on the machine running the tests.
Run: python test_remote_pin.py"""
import sys
import tempfile
import types
from pathlib import Path

# remote_server imports pyautogui + tools at module load (GUI/native deps). Stub them so this
# test runs in CI with only the standard library.
if "pyautogui" not in sys.modules:
    pg = types.ModuleType("pyautogui")
    pg.FAILSAFE = False
    pg.PAUSE = 0
    pg.size = lambda: (1920, 1080)
    sys.modules["pyautogui"] = pg
if "tools" not in sys.modules:
    t = types.ModuleType("tools")
    t.run_powershell = lambda cmd, timeout=60: {"ok": True, "ran": cmd}
    t.press_key = lambda *a, **k: None
    t.type_text = lambda *a, **k: None
    sys.modules["tools"] = t

import remote_server as rs


def _use_tmp_data_dir():
    d = Path(tempfile.mkdtemp(prefix="ember_pin_test_"))
    rs._data_dir = lambda: d
    return d


def test_pin_is_stable_across_calls():
    _use_tmp_data_dir()
    p1 = rs.stable_pin()
    p2 = rs.stable_pin()
    assert p1 == p2
    assert p1.isdigit() and 4 <= len(p1) <= 8


def test_pin_persists_to_disk_and_leaves_no_tmp_file_behind():
    d = _use_tmp_data_dir()
    p = rs.stable_pin()
    assert (d / "remote_pin.txt").read_text().strip() == p
    leftovers = [x for x in d.iterdir() if ".tmp" in x.name]
    assert leftovers == []


def test_corrupted_pin_file_is_regenerated_not_fatal():
    d = _use_tmp_data_dir()
    (d / "remote_pin.txt").write_text("12")   # too short to be a valid pin
    p = rs.stable_pin()
    assert p.isdigit() and 4 <= len(p) <= 8
    assert (d / "remote_pin.txt").read_text().strip() == p


def test_valid_existing_pin_file_is_never_regenerated():
    # This is the bug the user hit: a stable pin must survive close+reopen unchanged, or a
    # phone that already paired on the old pin starts getting rejected as "wrong PIN".
    d = _use_tmp_data_dir()
    (d / "remote_pin.txt").write_text("424242")
    assert rs.stable_pin() == "424242"
    assert rs.stable_pin() == "424242"


def test_write_uses_replace_not_a_direct_truncating_write():
    # A plain f.write_text() can leave a truncated/corrupt file if the process dies mid-write;
    # write-then-os.replace makes the update atomic so that can never happen.
    import inspect
    src = inspect.getsource(rs._write_stable_pin)
    assert "os.replace" in src


def _run():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
