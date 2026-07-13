"""Hermetic tests for the local Ollama tool-use: the curated toolset schemas + arg coercion,
and the OllamaAgent's _exec_tool flow (events + safety/confirmation), with the actual tool
call and safety stubbed so no GUI/network/Ollama is needed.

Run: python test_ollama_tools.py
"""
import ollama_tools as ot
import ollama_agent as oa


# ---- ollama_tools (pure) --------------------------------------------------

def test_tool_schemas_wellformed():
    assert len(ot.TOOLS) >= 15
    for t in ot.TOOLS:
        assert t["type"] == "function"
        f = t["function"]
        assert f["name"] and isinstance(f["description"], str) and f["description"]
        assert f["parameters"]["type"] == "object"
    # core local tools are present
    for n in ("run_shell", "read_file", "write_file", "take_screenshot", "click", "type_text"):
        assert n in ot.TOOL_NAMES, n


def test_readonly_is_subset_and_excludes_writers():
    assert ot.READONLY <= ot.TOOL_NAMES
    for w in ("run_shell", "write_file", "click", "type_text"):
        assert w not in ot.READONLY, w
    for r in ("read_file", "take_screenshot", "recall"):
        assert r in ot.READONLY, r


def test_coerce_args_parses_and_intifies():
    assert ot.coerce_args("click", '{"x":"10","y":"20"}') == {"x": 10, "y": 20}
    assert ot.coerce_args("scroll", {"direction": "down", "amount": "3"}) == {"direction": "down", "amount": 3}
    assert ot.coerce_args("read_file", "not json") == {}
    assert ot.coerce_args("read_file", {"path": "~/x"}) == {"path": "~/x"}


def test_call_unknown_tool():
    r = ot.call("does_not_exist", {})
    assert r["ok"] is False and "unknown tool" in r["error"]


# ---- OllamaAgent._exec_tool (events + safety + confirmation) ---------------

def _agent_with_capture():
    a = oa.OllamaAgent(model_name="qwen2.5")
    events = []
    a.subscribe(events.append)
    return a, events


def test_exec_tool_runs_and_emits(monkey_call=None):
    a, events = _agent_with_capture()
    ot.call = lambda name, args: {"ok": True, "ran": name, "args": args}   # stub the real call
    try:
        res = a._exec_tool("get_system_info", {})
    finally:
        pass
    assert res["ok"] is True and res["ran"] == "get_system_info"
    kinds = [e.kind for e in events]
    assert "tool_call" in kinds and "tool_result" in kinds


def test_exec_tool_confirmation_denied_blocks_run():
    a, events = _agent_with_capture()
    import safety
    # Force "needs confirmation" and auto-deny on the confirm event.
    safety.classify = lambda n, ar: ("EXFIL", "test risk")
    safety.needs_confirmation = lambda risk: True
    ran = {"count": 0}
    ot.call = lambda name, args: ran.__setitem__("count", ran["count"] + 1) or {"ok": True}

    def on_event(ev):
        if ev.kind == "confirm":
            ev.payload.response.put(False)   # user denies
    a.subscribe(on_event)
    res = a._exec_tool("run_shell", {"command": "rm -rf /"})
    assert res["ok"] is False and "denied" in res["error"]
    assert ran["count"] == 0, "denied tool must NOT run"


def test_exec_tool_unknown_name():
    a, events = _agent_with_capture()
    res = a._exec_tool("totally_unknown", {})
    assert res["ok"] is False and "unknown tool" in res["error"]


if __name__ == "__main__":
    import importlib
    import safety
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        importlib.reload(ot)        # restore the real ollama_tools.call
        importlib.reload(safety)    # restore real safety (one test stubs it)
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ollama tool tests passed")
