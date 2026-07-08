"""Regression test for the MCP bridge argument-handling bug.

The old build_server registered a bare ``def _handler(**kwargs)`` per tool; FastMCP
modelled ``**kwargs`` as a field named ``kwargs``, so a call like move_mouse(x=1,y=2)
was forwarded to Ember as {"kwargs": {"x": 1, "y": 2}} and rejected with
"unexpected keyword argument 'kwargs'". These tests fake the mcp SDK and assert that
build_server now advertises the real inputSchema and forwards arguments VERBATIM.
"""
import asyncio
import sys
import types as _t


def _install_fake_mcp():
    mcp = _t.ModuleType("mcp")
    server_mod = _t.ModuleType("mcp.server")
    lowlevel = _t.ModuleType("mcp.server.lowlevel")
    typesmod = _t.ModuleType("mcp.types")

    class Server:
        def __init__(self, name):
            self.name = name
            self.list_tools_fn = None
            self.call_tool_fn = None
        def list_tools(self):
            def deco(fn):
                self.list_tools_fn = fn
                return fn
            return deco
        def call_tool(self):
            def deco(fn):
                self.call_tool_fn = fn
                return fn
            return deco

    class Tool:
        def __init__(self, name, description="", inputSchema=None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class TextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text

    lowlevel.Server = Server
    typesmod.Tool = Tool
    typesmod.TextContent = TextContent
    server_mod.lowlevel = lowlevel
    mcp.server = server_mod
    mcp.types = typesmod
    sys.modules.update({
        "mcp": mcp, "mcp.server": server_mod,
        "mcp.server.lowlevel": lowlevel, "mcp.types": typesmod,
    })


class _FakeClient:
    def __init__(self):
        self.calls = []
    def list_tools(self):
        return [{
            "name": "move_mouse",
            "description": "move the pointer",
            "inputSchema": {"type": "object",
                            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                            "required": ["x", "y"]},
        }]
    def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "echo": args}


def _build():
    _install_fake_mcp()
    import ember_mcp_server as ems
    client = _FakeClient()
    return ems.build_server(client), client


def test_list_tools_uses_real_schema():
    server, _ = _build()
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools_fn())
    assert tools[0].name == "move_mouse"
    # The REAL schema is advertised — not a single 'kwargs' field.
    assert tools[0].inputSchema["properties"]["x"]["type"] == "integer"
    assert "kwargs" not in tools[0].inputSchema.get("properties", {})


def test_call_tool_forwards_args_verbatim():
    server, client = _build()
    out = asyncio.new_event_loop().run_until_complete(
        server.call_tool_fn("move_mouse", {"x": 100, "y": 200}))
    # The regression: args reach Ember UNWRAPPED (not nested under 'kwargs').
    assert client.calls == [("move_mouse", {"x": 100, "y": 200})]
    assert "kwargs" not in client.calls[0][1]
    assert out[0].text and "echo" in out[0].text


if __name__ == "__main__":  # allow running without pytest
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("PASS", name)
            except Exception:
                failed += 1; print("FAIL", name); traceback.print_exc()
    raise SystemExit(1 if failed else 0)
