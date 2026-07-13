"""Tests for Ember run modes + named agent profiles (agents.py).

Hermetic: all state is redirected to a throwaway dir via EMBER_SUPPORT_DIR.
Runnable: pytest test_agents.py  /  python test_agents.py
"""
import os
import tempfile
import time

os.environ["EMBER_SUPPORT_DIR"] = tempfile.mkdtemp(prefix="ember_agents_test_")

import agents


def _wipe():
    agents._save({})
    agents.set_run_mode("auto")


# --- run modes -----------------------------------------------------------------

def test_run_modes_listed_with_current():
    r = agents.list_run_modes()
    ids = {m["id"] for m in r["modes"]}
    assert {"auto", "plan", "chat", "read_only"} <= ids, ids
    assert r["current"] in ids


def test_set_and_get_run_mode_persists():
    assert agents.set_run_mode("plan")["ok"]
    assert agents.get_run_mode() == "plan"
    assert agents.set_run_mode("nonsense")["ok"] is False
    assert agents.get_run_mode() == "plan"      # unchanged on bad input
    agents.set_run_mode("auto")


def test_run_mode_directive_reflects_mode():
    assert "numbered" in agents.run_mode_directive("plan").lower()
    assert "do not control" in agents.run_mode_directive("chat").lower()
    assert "read-only" in agents.run_mode_directive("read_only").lower()


# --- tool scoping --------------------------------------------------------------

_TOOLS = ["read_file", "write_file", "click", "browser_open", "browser_navigate",
          "http_get", "send_email", "scan_file", "take_screenshot", "open_url"]


def test_scope_all_minus_deny():
    out = agents.filter_tools(_TOOLS, {"mode": "all", "deny": ["send_email"]})
    assert "send_email" not in out and "click" in out


def test_scope_custom_categories():
    out = agents.filter_tools(_TOOLS, {"mode": "custom", "categories": ["browser", "web"]})
    assert "browser_open" in out and "http_get" in out and "open_url" in out
    assert "click" not in out and "send_email" not in out


def test_scope_custom_allow_and_deny():
    out = agents.filter_tools(_TOOLS, {"mode": "custom", "allow": ["click", "read_file"],
                                       "deny": ["read_file"]})
    assert out == ["click"], out


def test_scope_read_only_uses_safety_set():
    # read_file / take_screenshot are in safety.SAFE_READONLY; write_file / click are not.
    out = set(agents.filter_tools(_TOOLS, {"mode": "read_only"}))
    assert "read_file" in out and "take_screenshot" in out
    assert "write_file" not in out and "send_email" not in out


# --- profile CRUD --------------------------------------------------------------

def test_create_list_get_delete():
    _wipe()
    c = agents.create_agent("Inbox Triage", instructions="Summarize new mail",
                            description="email helper", run_mode="read_only",
                            tool_scope={"mode": "custom", "categories": ["web"]})
    assert c["ok"] and c["agent"]["name"] == "inbox-triage", c
    lst = agents.agent_list()
    assert lst["count"] == 1 and lst["agents"][0]["name"] == "inbox-triage"
    got = agents.agent_get("Inbox Triage")
    assert got["ok"] and got["agent"]["run_mode"] == "read_only"
    assert agents.delete_agent("inbox-triage")["ok"]
    assert agents.agent_list()["count"] == 0


def test_create_validates_run_mode_and_schedule():
    _wipe()
    assert agents.create_agent("x", run_mode="bogus")["ok"] is False
    assert agents.create_agent("x", schedule={"every_minutes": 0})["ok"] is False
    assert agents.create_agent("x", schedule={"daily_at": "9am"})["ok"] is False
    assert agents.create_agent("x", schedule={"daily_at": "09:30"})["ok"] is True


def test_update_agent():
    _wipe()
    agents.create_agent("watcher", run_mode="auto")
    u = agents.update_agent("watcher", run_mode="plan", enabled=False)
    assert u["ok"] and u["agent"]["run_mode"] == "plan" and u["agent"]["enabled"] is False
    assert agents.update_agent("nope", run_mode="auto")["ok"] is False


# --- scheduling ----------------------------------------------------------------

def test_every_minutes_due_logic():
    _wipe()
    agents.create_agent("poller", schedule={"every_minutes": 10})
    now = time.time()
    assert agents.is_due(agents.get_agent("poller")["agent"], now) is True   # never ran
    agents.mark_ran("poller", now)
    assert agents.is_due(agents.get_agent("poller")["agent"], now + 60) is False
    assert agents.is_due(agents.get_agent("poller")["agent"], now + 11 * 60) is True


def test_disabled_agent_not_due():
    _wipe()
    agents.create_agent("p", schedule={"every_minutes": 1}, run_mode="auto")
    agents.update_agent("p", enabled=False)
    assert "p" not in agents.due_agents()


def test_due_agents_collection():
    _wipe()
    agents.create_agent("a", schedule={"every_minutes": 5})
    agents.create_agent("b")                       # no schedule -> never due
    due = agents.due_agents()
    assert "a" in due and "b" not in due


# --- build run request ---------------------------------------------------------

def test_build_run_request_assembles_scope_and_prompt():
    _wipe()
    agents.create_agent("Researcher", instructions="Find and summarize sources.",
                        run_mode="read_only",
                        tool_scope={"mode": "custom", "categories": ["web"]})
    req = agents.build_run_request("Researcher", task="latest on X",
                                   all_tool_names=_TOOLS)
    assert req["ok"] and req["run_mode"] == "read_only" and req["capability"] == "read_only"
    assert "Find and summarize" in req["instructions"]
    assert "# Task" in req["instructions"] and "latest on X" in req["instructions"]
    assert "http_get" in req["allowed_tools"] and "click" not in req["allowed_tools"]


def test_build_run_request_unknown_agent():
    _wipe()
    assert agents.build_run_request("ghost")["ok"] is False


def test_tool_wiring_exports():
    decl_names = {d["name"] for d in agents.TOOL_DECLARATIONS}
    # every dispatch key is declared; agent_run is declared but runtime-handled
    assert set(agents.TOOL_DISPATCH) <= decl_names
    assert "agent_run" in decl_names and "agent_run" not in agents.TOOL_DISPATCH
    assert "set_run_mode" in agents.INTERACTION_TOOLS


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
