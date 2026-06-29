"""Hermetic end-to-end tests for OllamaAgent._run_tool_loop — the two local-AI failure modes
the 1.6.0 fix targets:
  1. a text-only model 400s on image input  -> Ember drops images and retries (no crash), and
  2. a model rejects the structured `tools` field -> Ember keeps going in 'text tool' mode and
     RUNS a tool the model wrote as text (even under an alias like 'screenshot') instead of
     leaking raw JSON.
No network/GUI: `requests` is a scripted stub and tool execution is overridden on the instance.
Run: python test_ollama_loop.py"""
import sys
import types


class _FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


# Stub `requests` BEFORE importing ollama_agent (it imports requests lazily inside the loop).
if "requests" not in sys.modules or not hasattr(sys.modules["requests"], "_scripted"):
    req = types.ModuleType("requests")
    req.exceptions = types.SimpleNamespace(RequestException=Exception)
    req._scripted = True
    _QUEUE = []
    _SENT = []

    def _post(url, json=None, timeout=None, **k):
        _SENT.append(json or {})
        if not _QUEUE:
            return _FakeResp(200, {"message": {"content": "[end]"}})
        return _QUEUE.pop(0)

    req.post = _post
    req._QUEUE = _QUEUE
    req._SENT = _SENT
    sys.modules["requests"] = req

req = sys.modules["requests"]
import ollama_agent as oa


def _fresh_agent():
    req._QUEUE.clear()
    req._SENT.clear()
    ag = oa.OllamaAgent(model_name="llama3.2")
    ag.active_model = "llama3.2"
    ag._events = []
    ag.subscribe(lambda ev: ag._events.append(ev))
    return ag


def _kinds(ag):
    return [e.kind for e in ag._events]


def _messages_text(ag):
    return [e.payload for e in ag._events if e.kind == "message"]


def test_multimodal_400_is_recovered_not_crashed():
    ag = _fresh_agent()
    ag._vision_ok = True   # we *think* it's a vision model, but the endpoint says otherwise
    ag._messages = [{"role": "user", "content": "what's on my screen?", "images": ["BASE64IMG"]}]
    req._QUEUE.extend([
        _FakeResp(400, text='{"error":"Multimodal data provided, but model does not support '
                            'multimodal requests."}'),
        _FakeResp(200, {"message": {"content": "I can't view images, but I can read text."}}),
    ])
    done = ag._run_tool_loop()
    assert done is True
    assert ag._vision_ok is False                       # learned: text-only
    assert "images" not in ag._messages[0]              # images stripped before the retry
    assert _messages_text(ag) and "can't view images" in _messages_text(ag)[-1]
    assert "error" not in _kinds(ag)                    # the 400 did NOT surface as an error


def test_text_tool_mode_runs_aliased_call_no_json_leak():
    ag = _fresh_agent()
    ag._vision_ok = False
    ag._messages = [{"role": "user", "content": "take a screenshot"}]
    ran = []

    def fake_exec(name, args):
        ran.append(name)
        return {"ok": True, "took": name}

    ag._exec_tool = fake_exec    # avoid real OS/tool deps
    req._QUEUE.extend([
        # 1) model/endpoint rejects the structured tools field
        _FakeResp(400, text="this model does not support tools"),
        # 2) text-tool mode: model writes the call as TEXT under the alias "screenshot"
        _FakeResp(200, {"message": {"content": '{"name": "screenshot", "arguments": {}}'}}),
        # 3) model gives a normal final answer
        _FakeResp(200, {"message": {"content": "Done — I captured the screen."}}),
    ])
    done = ag._run_tool_loop()
    assert done is True
    assert ran == ["take_screenshot"]                   # alias resolved + actually executed
    finals = _messages_text(ag)
    assert finals and finals[-1] == "Done — I captured the screen."
    # the raw tool-call JSON must NOT have leaked to the user as a message
    assert not any("{" in m and '"name"' in m for m in finals)
    # after the first 400, later requests must omit the `tools` field
    assert "tools" not in req._SENT[-1]


def test_structured_tool_call_still_works():
    ag = _fresh_agent()
    ag._vision_ok = False
    ag._messages = [{"role": "user", "content": "list my timers"}]
    ran = []
    ag._exec_tool = lambda name, args: ran.append(name) or {"ok": True}
    req._QUEUE.extend([
        _FakeResp(200, {"message": {"tool_calls": [
            {"function": {"name": "list_timers", "arguments": {}}}]}}),
        _FakeResp(200, {"message": {"content": "You have no timers."}}),
    ])
    done = ag._run_tool_loop()
    assert done is True
    assert ran == ["list_timers"]
    assert _messages_text(ag)[-1] == "You have no timers."


def test_plain_answer_returns_immediately():
    ag = _fresh_agent()
    ag._vision_ok = False
    ag._messages = [{"role": "user", "content": "hello"}]
    req._QUEUE.append(_FakeResp(200, {"message": {"content": "Hi there!"}}))
    done = ag._run_tool_loop()
    assert done is True
    assert _messages_text(ag) == ["Hi there!"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ollama-loop tests passed")
