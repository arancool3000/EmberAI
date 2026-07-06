"""Hermetic tests for the OpenAI/ChatGPT provider agent (openai_agent.py) and the OpenAI
entries in the model catalog. Everything here runs WITHOUT the `openai` SDK or google-genai:
the module imports import-safe (stand-in AgentEvent), and the tricky streaming-reassembly
logic is a pure function exercised with fake chunk objects.

Run: python test_openai_agent.py
"""
import json
from types import SimpleNamespace as NS

import openai_agent as oa
import models


# ---- schema translation + tool build (pure) -------------------------------

def test_lower_types_recurses():
    node = {"type": "OBJECT", "properties": {"x": {"type": "STRING"},
                                             "n": {"type": "ARRAY", "items": {"type": "INTEGER"}}}}
    out = oa._lower_types(node)
    assert out["type"] == "object"
    assert out["properties"]["x"]["type"] == "string"
    assert out["properties"]["n"]["type"] == "array"
    assert out["properties"]["n"]["items"]["type"] == "integer"


def test_build_openai_tools_shape():
    decls = [{"name": "take_screenshot", "description": "cap",
              "parameters": {"type": "OBJECT", "properties": {"grid": {"type": "BOOLEAN"}}, "required": []}}]
    tools = oa._build_openai_tools(decls)
    assert len(tools) == 1
    t = tools[0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "take_screenshot"
    assert t["function"]["parameters"]["type"] == "object"
    assert t["function"]["parameters"]["properties"]["grid"]["type"] == "boolean"


def test_build_openai_tools_defaults_bad_params():
    tools = oa._build_openai_tools([{"name": "weird", "parameters": {"nope": 1}}])
    assert tools[0]["function"]["parameters"]["type"] == "object"


# ---- streaming reassembly (pure) ------------------------------------------

def _chunk(content=None, tool_calls=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)])


def _tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


def test_reassemble_text_only():
    chunks = [_chunk(content="Hel"), _chunk(content="lo"), _chunk(finish="stop")]
    seen = []
    msg, fr = oa._reassemble_stream(chunks, on_text=seen.append)
    assert msg["content"] == "Hello"
    assert fr == "stop"
    assert "tool_calls" not in msg
    assert seen == ["Hel", "lo"]


def test_reassemble_fragmented_tool_call():
    chunks = [
        _chunk(content="working"),
        _chunk(tool_calls=[_tc(0, id="call_1", name="run_shell")]),
        _chunk(tool_calls=[_tc(0, args='{"cmd":')]),
        _chunk(tool_calls=[_tc(0, args=' "ls"}')]),
        _chunk(finish="tool_calls"),
    ]
    msg, fr = oa._reassemble_stream(chunks)
    assert fr == "tool_calls"
    assert msg["content"] == "working"
    assert len(msg["tool_calls"]) == 1
    call = msg["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "run_shell"
    assert json.loads(call["function"]["arguments"]) == {"cmd": "ls"}


def test_reassemble_parallel_tool_calls_by_index():
    chunks = [
        _chunk(tool_calls=[_tc(0, id="a", name="t0", args="{}")]),
        _chunk(tool_calls=[_tc(1, id="b", name="t1", args="{}")]),
        _chunk(finish="tool_calls"),
    ]
    msg, fr = oa._reassemble_stream(chunks)
    names = [c["function"]["name"] for c in msg["tool_calls"]]
    assert names == ["t0", "t1"]


def test_reassemble_drops_empty_tool_slots():
    # A slot that never got an id/name must be discarded, not emitted as a broken call.
    chunks = [_chunk(tool_calls=[_tc(0, args="{}")]), _chunk(finish="stop")]
    msg, fr = oa._reassemble_stream(chunks)
    assert "tool_calls" not in msg


def test_reassemble_stop_aborts():
    chunks = [_chunk(content="a"), _chunk(content="b")]
    msg, fr = oa._reassemble_stream(chunks, stop=lambda: True)
    assert msg["content"] is None  # nothing consumed


# ---- model catalog routing ------------------------------------------------

def test_provider_for_openai_ids():
    for mid in ("gpt-4o", "gpt-4o-mini", "gpt-5", "gpt-5.1", "o1", "o3-mini", "o4-mini",
                "chatgpt-4o-latest", "openai:custom-model"):
        assert models.provider_for(mid) == "openai", mid


def test_provider_for_does_not_hijack_others():
    assert models.provider_for("claude-opus-4-8") == "claude"
    assert models.provider_for("gemini-3.5-flash") == "gemini"
    assert models.provider_for("auto") == "gemini"
    assert models.provider_for("ollama") == "ollama"


def test_openai_models_in_choices():
    choices = models.all_choices()
    openai = [c for c in choices if c[0] == "openai"]
    assert len(openai) == len(models.OPENAI_MODELS)
    ids = {c[1] for c in openai}
    assert "gpt-4o-mini" in ids and "gpt-5" in ids


def test_openai_compat_bases():
    assert models.openai_base_for("xai") == "https://api.x.ai/v1"
    assert models.openai_base_for("openai") == ""       # default OpenAI = no base override
    assert models.openai_base_for("unknown-provider") == ""


def test_openai_supports_tools_and_vision_not_thinking():
    # GPT models drive tools + vision; Anthropic-only thinking/effort knobs stay off.
    assert models.supports_tool_use("gpt-4o") is True
    assert models.supports_vision("gpt-4o") is True
    assert models.supports_adaptive_thinking("gpt-4o") is False
    assert models.supports_effort("gpt-5") is False


def test_prefix_strip_helper_via_provider():
    # openai:<model> classifies as openai (the agent strips the prefix before the API call).
    assert models.provider_for("openai:llama-3.1-70b") == "openai"


def test_heal_dangling_tool_calls():
    # A trailing assistant message with unanswered tool_calls (user hit Stop mid-run) must be
    # trimmed so the next turn isn't a 400. Answered tool_calls stay.
    fake = NS(_messages=[
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [{"id": "1"}]},  # dangling -> drop
    ])
    oa.OpenAIAgent._heal_dangling_tool_calls(fake)
    assert [m["role"] for m in fake._messages] == ["system", "user"]

    fake2 = NS(_messages=[
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},
    ])
    oa.OpenAIAgent._heal_dangling_tool_calls(fake2)
    assert len(fake2._messages) == 2  # answered -> untouched


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} openai agent tests passed")
