"""Tests for AI-authored custom tools (custom_tools.py).

Runnable two ways:
    pytest test_custom_tools.py
    python test_custom_tools.py        # PASS/FAIL summary

State is redirected to a throwaway dir via EMBER_SUPPORT_DIR so nothing touches the
real user profile.
"""
import os
import tempfile

os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_ct_test_"))

import custom_tools as ct


def _reset():
    ct.KNOWN_TOOLS = set()
    try:
        ct._path().unlink()
    except Exception:
        pass


# --- create / validate ---------------------------------------------------------

def test_create_and_list_and_get():
    _reset()
    r = ct.create_custom_tool(
        "tidy_downloads", "Dry-run organize, then list",
        parameters={"type": "OBJECT", "properties": {"folder": {"type": "STRING"}},
                    "required": ["folder"]},
        steps=[{"tool": "organize_folder", "args": {"path": "{{folder}}", "dry_run": True}},
               {"tool": "list_directory", "args": {"path": "{{folder}}"}}])
    assert r["ok"] and r["saved"] == "tidy_downloads" and r["steps"] == 2, r
    lst = ct.list_custom_tools()
    assert lst["count"] == 1 and lst["tools"][0]["name"] == "tidy_downloads", lst
    got = ct.get_custom_tool("tidy_downloads")
    assert got["ok"] and len(got["tool"]["steps"]) == 2, got
    _reset()


def test_bad_name_rejected():
    _reset()
    for bad in ("", "Tidy", "9x", "has space", "a"):
        assert ct.create_custom_tool(bad, steps=[{"tool": "now"}])["ok"] is False, bad
    _reset()


def test_reserved_name_rejected():
    _reset()
    assert ct.create_custom_tool("run_custom_tool", steps=[{"tool": "now"}])["ok"] is False
    _reset()


def test_empty_or_bad_steps_rejected():
    _reset()
    assert ct.create_custom_tool("x_tool", steps=[])["ok"] is False
    assert ct.create_custom_tool("x_tool", steps=[{"no_tool": 1}])["ok"] is False
    assert ct.create_custom_tool("x_tool", steps=[{"tool": "now", "args": "nope"}])["ok"] is False
    _reset()


def test_unknown_step_tool_rejected_when_registry_known():
    _reset()
    ct.KNOWN_TOOLS = {"now", "list_directory"}
    bad = ct.create_custom_tool("x_tool", steps=[{"tool": "totally_made_up"}])
    assert bad["ok"] is False and "unknown tool" in bad["error"], bad
    good = ct.create_custom_tool("x_tool", steps=[{"tool": "now"}])
    assert good["ok"], good
    _reset()


def test_overwrite_guard():
    _reset()
    assert ct.create_custom_tool("dup", steps=[{"tool": "now"}])["ok"]
    assert ct.create_custom_tool("dup", steps=[{"tool": "now"}])["ok"] is False  # exists
    assert ct.create_custom_tool("dup", steps=[{"tool": "now"}], overwrite=True)["ok"]
    _reset()


# --- placeholder substitution --------------------------------------------------

def test_whole_value_placeholder_keeps_type():
    _reset()
    ct.create_custom_tool(
        "echo_count",
        parameters={"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}}},
        steps=[{"tool": "noop", "args": {"count": "{{n}}", "label": "got {{n}} items"}}])
    res = ct.resolve_steps("echo_count", {"n": 5})
    assert res["ok"], res
    args = res["steps"][0]["args"]
    assert args["count"] == 5, "whole-value placeholder must keep the int type"
    assert args["label"] == "got 5 items", "embedded placeholder must string-interpolate"
    _reset()


def test_nested_placeholder_substitution():
    _reset()
    ct.create_custom_tool(
        "nested",
        steps=[{"tool": "noop", "args": {"outer": {"inner": ["{{a}}", "x{{a}}"]}}}])
    res = ct.resolve_steps("nested", {"a": "Z"})
    assert res["steps"][0]["args"]["outer"]["inner"] == ["Z", "xZ"], res
    _reset()


def test_missing_placeholder_left_intact():
    _reset()
    ct.create_custom_tool("m_tool", steps=[{"tool": "noop", "args": {"p": "{{missing}}"}}])
    res = ct.resolve_steps("m_tool", {})
    assert res["steps"][0]["args"]["p"] == "{{missing}}", res
    _reset()


def test_resolve_unknown_tool_errors():
    _reset()
    assert ct.resolve_steps("nope", {})["ok"] is False
    _reset()


# --- export / import (share + reuse) -------------------------------------------

def test_export_import_roundtrip():
    _reset()
    ct.create_custom_tool("shareme", "desc", steps=[{"tool": "now"}])
    exp = ct.export_custom_tool("shareme")
    assert exp["ok"] and "shareme" in exp["json"], exp
    ct.delete_custom_tool("shareme")
    assert ct.get_custom_tool("shareme")["ok"] is False
    imp = ct.import_custom_tool(exp["json"])
    assert imp["ok"], imp
    assert ct.get_custom_tool("shareme")["ok"], "re-imported tool should exist"
    _reset()


def test_import_bad_json_rejected():
    _reset()
    assert ct.import_custom_tool("{not json")["ok"] is False
    assert ct.import_custom_tool('"a string"')["ok"] is False
    _reset()


def test_delete():
    _reset()
    ct.create_custom_tool("gone", steps=[{"tool": "now"}])
    assert ct.delete_custom_tool("gone")["ok"]
    assert ct.delete_custom_tool("gone")["ok"] is False
    _reset()


def test_persistence_across_reload():
    _reset()
    ct.create_custom_tool("persist", steps=[{"tool": "now"}])
    # Simulate a fresh process: re-read from disk.
    assert ct.get_custom_tool("persist")["ok"], "tool must persist to disk"
    _reset()


def test_wiring_contract():
    """The module must export the standard tool contract the host merges."""
    names = {d["name"] for d in ct.TOOL_DECLARATIONS}
    assert {"create_custom_tool", "run_custom_tool", "list_custom_tools"} <= names
    # run_custom_tool is host-executed, so it must NOT be in the module dispatch.
    assert "run_custom_tool" not in ct.TOOL_DISPATCH
    assert "create_custom_tool" in ct.TOOL_DISPATCH
    assert ct.READONLY_TOOLS and ct.INTERACTION_TOOLS


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
