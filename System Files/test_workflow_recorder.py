"""Hermetic tests for workflow_recorder.py — must pass WITHOUT pynput or pyautogui installed.

Exercises the pure event-builder helpers, file persistence, list/delete tools, the key-mapping
helper used by replay, and the friendly missing-dependency / not-found error paths. No pytest
(not installed in CI) - a fresh temp WORKFLOW_DIR is set per run, matching every other test file
in this repo.
Run: python test_workflow_recorder.py"""
import json
import tempfile
from pathlib import Path

import workflow_recorder as wr

wr.WORKFLOW_DIR = Path(tempfile.mkdtemp(prefix="ember_workflow_test_"))


def _sample_events():
    """Build a workflow via the pure helpers (no pynput needed)."""
    return [
        wr._record_move(10, 20, 0.0),
        wr._record_click(10, 20, "left", True, 0.5),
        wr._record_scroll(10, 20, 0, -2, 1.0),
        wr._record_key("a", 1.2),
        wr._record_key("space", 1.4),
    ]


# ---- pure event builders -----------------------------------------------------
def test_serialize_key_variants():
    assert wr._serialize_key("a") == "a"
    assert wr._serialize_key(None) == ""

    class _KC:  # mimic pynput KeyCode with a .char
        char = "z"
    assert wr._serialize_key(_KC()) == "z"

    class _SpecialName:  # mimic pynput Key with a .name and no char
        char = None
        name = "enter"
    assert wr._serialize_key(_SpecialName()) == "enter"

    class _ReprOnly:
        char = None
        name = None
        def __str__(self):
            return "Key.space"
    assert wr._serialize_key(_ReprOnly()) == "space"


def test_event_builders_shape():
    mv = wr._record_move(5, 6, 0.1234567)
    assert mv == {"t": 0.1235, "type": "move", "x": 5, "y": 6}

    ck = wr._record_click(1, 2, "left", True, 0.5)
    assert ck["type"] == "click" and ck["button"] == "left" and ck["pressed"] is True

    sc = wr._record_scroll(1, 2, 0, -3, 0.7)
    assert sc["type"] == "scroll" and sc["dx"] == 0 and sc["dy"] == -3

    ky = wr._record_key("a", 0.9)
    assert ky["type"] == "key" and ky["key"] == "a"


# ---- replay key-name mapping (pure, no pyautogui needed) ---------------------
def test_pyautogui_key_passthrough_for_single_chars():
    assert wr._pyautogui_key("a") == "a"
    assert wr._pyautogui_key("Z") == "Z"


def test_pyautogui_key_maps_pynput_modifier_aliases():
    assert wr._pyautogui_key("cmd") == "command"
    assert wr._pyautogui_key("cmd_l") == "command"
    assert wr._pyautogui_key("alt_r") == "alt"
    assert wr._pyautogui_key("ctrl_l") == "ctrl"
    assert wr._pyautogui_key("shift_r") == "shift"


def test_pyautogui_key_passes_through_already_compatible_names():
    assert wr._pyautogui_key("space") == "space"
    assert wr._pyautogui_key("enter") == "enter"
    assert wr._pyautogui_key("backspace") == "backspace"


def test_pyautogui_key_empty():
    assert wr._pyautogui_key("") == ""
    assert wr._pyautogui_key(None) == ""


# ---- persistence + list + delete --------------------------------------------
def test_save_list_delete_roundtrip():
    events = _sample_events()
    saved = wr._save_workflow("My Demo Flow", events, duration=1.4)
    assert saved["ok"] is True
    assert saved["event_count"] == 5
    assert saved["name"] == "My Demo Flow"

    # File written under the temp dir as a slug.
    path = wr._path_for("My Demo Flow")
    assert path.exists()
    on_disk = json.loads(path.read_text("utf-8"))
    assert on_disk["name"] == "My Demo Flow"
    assert len(on_disk["events"]) == 5
    assert "created" in on_disk and on_disk["duration"] == 1.4

    listing = wr.list_workflows()
    assert listing["ok"] is True
    assert listing["count"] == 1
    entry = listing["workflows"][0]
    assert entry["name"] == "My Demo Flow"
    assert entry["event_count"] == 5
    assert entry["duration"] == 1.4
    assert entry["created"]

    deleted = wr.delete_workflow("My Demo Flow")
    assert deleted["ok"] is True and deleted["deleted"] is True
    assert not path.exists()
    assert wr.list_workflows()["count"] == 0


def test_list_workflows_empty():
    wr.delete_workflow("leftover")  # no-op if absent; just ensure a clean slate
    for entry in wr.list_workflows()["workflows"]:
        wr.delete_workflow(entry["name"])
    r = wr.list_workflows()
    assert r["ok"] is True and r["count"] == 0 and r["workflows"] == []


def test_save_requires_name():
    assert wr._save_workflow("", [], 0)["ok"] is False


def test_delete_missing():
    r = wr.delete_workflow("does-not-exist")
    assert r["ok"] is False
    assert "no workflow" in r["error"]


# ---- missing-dependency friendly errors (pynput/pyautogui absent in this env) -
def test_record_start_without_pynput():
    r = wr.record_workflow_start("anything")
    assert r["ok"] is False
    # macOS refuses before even reaching the pynput import (uses its own recorder backend);
    # other platforms hit the pynput-missing message directly.
    assert "pynput" in r["error"].lower() or "accessibility" in r["error"].lower() \
        or "main thread" in r["error"].lower()


def test_replay_without_pyautogui():
    # Save a real workflow first so the failure is the pyautogui import, not not-found.
    wr._save_workflow("x", _sample_events(), 1.4)
    r = wr.replay_workflow("x")
    assert r["ok"] is False
    assert "pyautogui" in r["error"].lower()
    wr.delete_workflow("x")


def test_replay_not_found():
    r = wr.replay_workflow("nope-not-here")
    assert r["ok"] is False
    assert "no workflow" in r["error"]


def test_record_stop_when_not_recording():
    r = wr.record_workflow_stop()
    assert r["ok"] is False


# ---- export integrity --------------------------------------------------------
def test_exports_consistent():
    assert set(wr.TOOL_DISPATCH) == {d["name"] for d in wr.TOOL_DECLARATIONS}
    assert len(wr.TOOL_DECLARATIONS) == 5
    assert wr.READONLY_TOOLS == {"list_workflows"}
    assert wr.INTERACTION_TOOLS == {"record_workflow_start", "record_workflow_stop", "delete_workflow"}
    # replay_workflow is in neither set.
    assert "replay_workflow" not in wr.READONLY_TOOLS
    assert "replay_workflow" not in wr.INTERACTION_TOOLS


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
    import sys
    sys.exit(0 if _run() else 1)
