"""Hermetic tests for the Ember MCP bridge (ember_bridge.py) and the standalone MCP server's
transport helpers (ember_mcp_server.py). No `mcp` SDK, no google-genai, no GUI: the pure
helpers are exercised directly, and the loopback HTTP server is tested end-to-end with the
tool registries monkeypatched to fakes (so real tool execution / the heavy agent import is
never triggered).

Run: python test_ember_bridge.py
"""
import json
import os
import tempfile
import urllib.request
import urllib.error

# Keep the bridge's info file out of the real support dir during tests.
os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember-bridge-test-"))

import ember_bridge as eb
import ember_mcp_server as ems


# ---- schema translation (pure) --------------------------------------------

def test_translate_schema_lowercases_types():
    node = {"type": "OBJECT", "properties": {"p": {"type": "STRING"},
                                             "xs": {"type": "ARRAY", "items": {"type": "NUMBER"}}}}
    out = eb.translate_schema(node)
    assert out["type"] == "object"
    assert out["properties"]["p"]["type"] == "string"
    assert out["properties"]["xs"]["items"]["type"] == "number"


def test_tool_to_mcp_shape():
    decl = {"name": "take_screenshot", "description": "cap",
            "parameters": {"type": "OBJECT", "properties": {"grid": {"type": "BOOLEAN"}}}}
    m = eb.tool_to_mcp(decl)
    assert m["name"] == "take_screenshot"
    assert m["description"] == "cap"
    assert m["inputSchema"]["type"] == "object"
    assert m["inputSchema"]["properties"]["grid"]["type"] == "boolean"


def test_tool_to_mcp_defaults_missing_schema():
    m = eb.tool_to_mcp({"name": "x"})
    assert m["inputSchema"]["type"] == "object"
    assert "properties" in m["inputSchema"]


def test_list_tools_filters_agent_only_and_undispatchable():
    decls = [
        {"name": "take_screenshot", "parameters": {"type": "OBJECT"}},
        {"name": "ask_claude", "parameters": {"type": "OBJECT"}},   # agent-only -> excluded
        {"name": "phantom", "parameters": {"type": "OBJECT"}},      # no dispatch -> excluded
        {"name": "take_screenshot", "parameters": {"type": "OBJECT"}},  # dup -> once
    ]
    dispatch = {"take_screenshot": lambda **k: {"ok": True}, "ask_claude": lambda **k: 1}
    names = [t["name"] for t in eb.list_tools_from(decls, dispatch)]
    assert names == ["take_screenshot"]


# ---- safety-gated execution ------------------------------------------------

def test_execute_unknown_tool():
    assert eb.execute_tool("nope", {}, {})["ok"] is False


def test_execute_agent_only_blocked():
    r = eb.execute_tool("ask_claude", {}, {"ask_claude": lambda **k: 1})
    assert r["ok"] is False and "agent loop" in r["error"]


def test_execute_low_risk_runs_and_passes_args():
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return {"ok": True, "action": "listed"}

    r = eb.execute_tool("list_files", {"path": "/tmp"}, {"list_files": fake})
    assert r["ok"] is True and r["action"] == "listed"
    assert captured == {"path": "/tmp"}


def test_execute_high_risk_blocked_by_default():
    import safety
    name = sorted(safety.EXFIL_TOOLS)[0]   # e.g. send_email — classified high-risk
    r = eb.execute_tool(name, {}, {name: lambda **k: {"ok": True}})
    assert r["ok"] is False
    assert r.get("needs_confirmation") is True


def test_execute_high_risk_allowed_when_opted_in():
    import safety
    name = sorted(safety.EXFIL_TOOLS)[0]
    r = eb.execute_tool(name, {}, {name: lambda **k: {"ok": True, "sent": 1}},
                        allow_high_risk=True)
    assert r["ok"] is True


def test_execute_wraps_non_dict_result():
    r = eb.execute_tool("thing", {}, {"thing": lambda **k: "plain string"})
    assert isinstance(r, dict) and r["ok"] is True


def test_execute_strips_image_payload():
    big = "A" * 5000
    r = eb.execute_tool("take_screenshot", {}, {"take_screenshot": lambda **k: {"ok": True, "image_b64": big}})
    assert r["image_b64"] != big and "omitted" in r["image_b64"]


# ---- loopback HTTP server (end-to-end, registries faked) -------------------

_FAKE_DECLS = [{"name": "ping_tool", "description": "d", "parameters": {"type": "OBJECT", "properties": {}}}]
_FAKE_DISPATCH = {"ping_tool": lambda **k: {"ok": True, "pong": k.get("n", 0)}}


def _patch_registries():
    eb._registries = lambda: (_FAKE_DECLS, _FAKE_DISPATCH, {})


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Ember-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, payload, token=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Ember-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_bridge_http_roundtrip():
    _patch_registries()
    started = eb.start(port=8791, allow_high_risk=False)
    assert started["ok"] and started.get("started")
    token = started["token"]
    base = started["url"]
    try:
        # unauth ping works (liveness, no data)
        code, body = _get(base + "/mcp/ping")
        assert code == 200 and body["name"] == "ember"

        # tools require auth
        code, _ = _get(base + "/mcp/tools")
        assert code == 401
        code, body = _get(base + "/mcp/tools", token=token)
        assert code == 200 and [t["name"] for t in body["tools"]] == ["ping_tool"]

        # call requires auth + returns wrapped result
        code, _ = _post(base + "/mcp/call", {"name": "ping_tool", "args": {"n": 7}})
        assert code == 401
        code, body = _post(base + "/mcp/call", {"name": "ping_tool", "args": {"n": 7}}, token=token)
        assert code == 200 and body["result"]["pong"] == 7

        # bad token rejected
        code, _ = _get(base + "/mcp/tools", token="wrong")
        assert code == 401

        # DNS-rebinding defense: a forged (non-loopback) Host header is refused outright,
        # even with the right token — a malicious web page can't reach the bridge this way.
        req = urllib.request.Request(base + "/mcp/tools")
        req.add_header("X-Ember-Token", token)
        req.add_header("Host", "evil.example.com")
        try:
            urllib.request.urlopen(req, timeout=5)
            forged_code = 200
        except urllib.error.HTTPError as e:
            forged_code = e.code
        assert forged_code == 403

        # oversized body rejected on the Content-Length header (before reading the payload):
        # the server returns 413, or the client sees the connection drop — either means refused.
        req2 = urllib.request.Request(base + "/mcp/call", data=b"x" * 1_000_001, method="POST")
        req2.add_header("X-Ember-Token", token)
        req2.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req2, timeout=5)
            big_code = 200
        except urllib.error.HTTPError as e:
            big_code = e.code
        except urllib.error.URLError:
            big_code = 413  # connection reset because we rejected without draining the body
        assert big_code == 413

        # info file written for the MCP server to read
        info = eb.load_bridge_info()
        assert info and info["token"] == token
        assert eb.status()["running"] is True
    finally:
        stopped = eb.stop()
        assert stopped["ok"] and stopped["stopped"]
    assert eb.status()["running"] is False


# ---- MCP server transport helpers -----------------------------------------

def test_resolve_bridge_prefers_explicit_args():
    url, token = ems._resolve_bridge("http://127.0.0.1:9/", "TOK")
    assert url == "http://127.0.0.1:9" and token == "TOK"


def test_resolve_bridge_from_env():
    os.environ["EMBER_BRIDGE_URL"] = "http://127.0.0.1:1234"
    os.environ["EMBER_BRIDGE_TOKEN"] = "envtok"
    try:
        url, token = ems._resolve_bridge()
        assert url == "http://127.0.0.1:1234" and token == "envtok"
    finally:
        os.environ.pop("EMBER_BRIDGE_URL")
        os.environ.pop("EMBER_BRIDGE_TOKEN")


def test_bridge_client_call_against_live_server():
    _patch_registries()
    started = eb.start(port=8792)
    try:
        client = ems.BridgeClient(started["url"], started["token"])
        tools = client.list_tools()
        assert [t["name"] for t in tools] == ["ping_tool"]
        res = client.call_tool("ping_tool", {"n": 3})
        assert res["pong"] == 3
    finally:
        eb.stop()


if __name__ == "__main__":
    import importlib
    import safety
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        importlib.reload(safety)
        importlib.reload(eb)
        importlib.reload(ems)
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ember bridge tests passed")
