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


# --- aliases: a local model invents a different name for a real tool ---------------------
import ollama_tools as ot  # noqa: E402  (pure import: only stdlib at module load)


def test_alias_screenshot_resolves_to_take_screenshot():
    # The exact screenshot-2 bug: {"name": "screenshot", "parameters": {"path": ...}} leaked as
    # raw text because "screenshot" wasn't a known tool. With aliases it becomes take_screenshot.
    txt = '{"name": "screenshot", "parameters": {"path": "/Users/x/spatial-os"}}'
    calls = oa.extract_text_tool_calls(txt, ot.TOOL_NAMES, ot.TOOL_ALIASES)
    assert _names(calls) == ["take_screenshot"]


def test_alias_case_insensitive_and_nested():
    calls = oa.extract_text_tool_calls('{"function": {"name": "Screenshot", "arguments": {}}}',
                                       ot.TOOL_NAMES, ot.TOOL_ALIASES)
    assert _names(calls) == ["take_screenshot"]


def test_alias_without_map_is_not_matched():
    # No alias map passed -> an invented name stays unknown (backward-compatible behaviour).
    assert oa.extract_text_tool_calls('{"name": "screenshot", "arguments": {}}', ot.TOOL_NAMES) == []


def test_resolve_name_helper():
    assert ot.resolve_name("screenshot") == "take_screenshot"
    assert ot.resolve_name("SHELL") == "run_shell"
    assert ot.resolve_name("take_screenshot") == "take_screenshot"
    assert ot.resolve_name("not_a_tool") == "not_a_tool"


def test_coerce_args_drops_hallucinated_keys():
    # take_screenshot takes NO args; a bogus path must be dropped so the call doesn't error.
    assert ot.coerce_args("take_screenshot", {"path": "/tmp"}) == {}
    assert ot.coerce_args("screenshot", {"path": "/tmp"}) == {}     # via alias
    assert ot.coerce_args("click", {"x": "10", "y": "20", "junk": 1}) == {"x": 10, "y": 20}


# --- vision capability + multimodal-error detection -------------------------------------
def test_model_supports_vision():
    assert oa.model_supports_vision("llava:13b")
    assert oa.model_supports_vision("llama3.2-vision")
    assert oa.model_supports_vision("gemma3:4b")
    assert not oa.model_supports_vision("llama3.2")
    assert not oa.model_supports_vision("qwen2.5:7b")
    assert not oa.model_supports_vision("")


def test_is_multimodal_error():
    body = ('{"error":"{\\"error\\":{\\"code\\":400,\\"message\\":\\"Multimodal data provided, '
            'but model does not support multimodal requests.\\"}}"}')
    assert oa._is_multimodal_error(body)
    assert oa._is_multimodal_error("model does not support vision input")
    assert not oa._is_multimodal_error("rate limit exceeded")
    assert not oa._is_multimodal_error("")


def test_strip_images():
    msgs = [{"role": "user", "content": "hi", "images": ["b64"]},
            {"role": "assistant", "content": "ok"}]
    assert oa._strip_images(msgs) is True
    assert "images" not in msgs[0]
    assert oa._strip_images(msgs) is False   # nothing left to strip


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ollama text-toolcall tests passed")
