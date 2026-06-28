"""Tests for ollama_agent.extract_text_tool_calls — parsing tool calls that a local model
emits as TEXT instead of using Ollama's structured tool_calls field. No network/requests.
Run: python test_ollama_textcalls.py"""
import sys
import types

# ollama_agent imports `requests` at module load; stub it so this test needs no network dep.
if "requests" not in sys.modules:
    _req = types.ModuleType("requests")
    _req.exceptions = types.SimpleNamespace(RequestException=Exception)
    _req.post = lambda *a, **k: None
    sys.modules["requests"] = _req

import ollama_agent as oa

TOOLS = {"take_screenshot", "click", "type_text", "read_screen_text"}


def _names(calls):
    return [c["function"]["name"] for c in calls]


def test_malformed_screenshot_case():
    # The exact text from the bug report: invalid JSON, but the intent is clear.
    calls = oa.extract_text_tool_calls('{"name": "take_screenshot", "{}"}', TOOLS)
    assert _names(calls) == ["take_screenshot"]
    assert calls[0]["function"]["arguments"] == {}


def test_wellformed_with_arguments():
    calls = oa.extract_text_tool_calls('{"name": "click", "arguments": {"x": 10, "y": 20}}', TOOLS)
    assert _names(calls) == ["click"]
    assert calls[0]["function"]["arguments"] == {"x": 10, "y": 20}


def test_parameters_key_and_fence():
    txt = 'Sure!\n```json\n{"name": "type_text", "parameters": {"text": "hello"}}\n```'
    calls = oa.extract_text_tool_calls(txt, TOOLS)
    assert _names(calls) == ["type_text"]
    assert calls[0]["function"]["arguments"] == {"text": "hello"}


def test_tool_call_tag():
    txt = '<tool_call>{"name": "read_screen_text", "arguments": {}}</tool_call>'
    calls = oa.extract_text_tool_calls(txt, TOOLS)
    assert _names(calls) == ["read_screen_text"]


def test_nested_function_shape():
    txt = '{"function": {"name": "type_text", "arguments": {"text": "hi"}}}'
    calls = oa.extract_text_tool_calls(txt, TOOLS)
    assert _names(calls) == ["type_text"]
    assert calls[0]["function"]["arguments"] == {"text": "hi"}


def test_braces_inside_string_value_dont_break_parsing():
    txt = '{"name": "type_text", "arguments": {"text": "a {b} c"}}'
    calls = oa.extract_text_tool_calls(txt, TOOLS)
    assert _names(calls) == ["type_text"]
    assert calls[0]["function"]["arguments"]["text"] == "a {b} c"


def test_plain_prose_is_not_a_tool_call():
    assert oa.extract_text_tool_calls("Yes, I can see your screen — it shows a code editor.", TOOLS) == []
    # mentions a tool name in prose but no JSON name field -> not a call
    assert oa.extract_text_tool_calls('I will use take_screenshot to look.', TOOLS) == []


def test_unknown_tool_is_ignored():
    assert oa.extract_text_tool_calls('{"name": "rm_rf_everything", "arguments": {}}', TOOLS) == []


def test_empty_content():
    assert oa.extract_text_tool_calls("", TOOLS) == []
    assert oa.extract_text_tool_calls(None, TOOLS) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ollama text-toolcall tests passed")
