"""Ember MCP server — exposes every Ember tool to ChatGPT and other MCP clients.

This is the standalone half of Ember's MCP support (analogous to the blender-mcp *server*
that Claude Desktop launches). It speaks the Model Context Protocol over stdio and forwards
every tool call to a running Ember via its loopback bridge (ember_bridge.py). Start Ember,
turn the MCP bridge on (Settings → "MCP bridge", or ask Ember to "start the MCP bridge"),
then point your MCP client at this script.

Claude Desktop config (claude_desktop_config.json):

    {
      "mcpServers": {
        "ember": {
          "command": "python3",
          "args": ["/absolute/path/to/EmberAI/ember_mcp_server.py"]
        }
      }
    }

The bridge URL + token are read automatically from Ember's support dir (written when the
bridge starts). You can override with the EMBER_BRIDGE_URL and EMBER_BRIDGE_TOKEN env vars,
or --url / --token flags — handy when Ember runs on another machine you've SSH-forwarded.

Requires: pip install 'mcp>=1.27,<2' (the stable Model Context Protocol SDK). The import is lazy so
this module stays importable — and unit-testable — without the SDK or a running Ember.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from typing import Any
import urllib.request
import urllib.error

DEFAULT_TIMEOUT = 120


def _resolve_bridge(url: str | None = None, token: str | None = None) -> tuple[str, str]:
    """Figure out the bridge base URL + token from args → env → the support-dir info file."""
    url = url or os.environ.get("EMBER_BRIDGE_URL")
    token = token or os.environ.get("EMBER_BRIDGE_TOKEN")
    if url and token:
        return url.rstrip("/"), token
    # Fall back to the file the bridge writes on start. Import lazily so this module needs
    # nothing from the Ember app just to be imported.
    try:
        import ember_bridge
        info = ember_bridge.load_bridge_info()
    except Exception:
        info = _read_info_file()
    if info:
        url = url or info.get("url")
        token = token or info.get("token")
    if not url or not token:
        raise RuntimeError(
            "Could not find Ember's MCP bridge. Start Ember and enable the MCP bridge "
            "(Settings → MCP bridge, or ask Ember to 'start the MCP bridge'), or set "
            "EMBER_BRIDGE_URL and EMBER_BRIDGE_TOKEN."
        )
    return url.rstrip("/"), token


def _read_info_file() -> dict | None:
    """Read mcp_bridge.json directly (used when ember_bridge isn't importable)."""
    override = os.environ.get("EMBER_SUPPORT_DIR")
    from pathlib import Path
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Ember"
    try:
        return json.loads((base / "mcp_bridge.json").read_text())
    except Exception:
        return None


class BridgeClient:
    """Tiny HTTP client for the Ember bridge (stdlib only, no extra deps)."""

    def __init__(self, url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None,
                 _retried_after_auth: bool = False) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Ember-Token", self.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            # Saving MCP settings can intentionally restart the bridge and rotate its secret.
            # Refresh once from the protected local info file so a long-running ChatGPT/Claude
            # MCP process survives that normal lifecycle event instead of becoming stranded.
            if e.code == 401 and not _retried_after_auth:
                try:
                    url, token = _resolve_bridge()
                    self.url, self.token = url, token
                    return self._request(method, path, payload, _retried_after_auth=True)
                except Exception:
                    pass
            try:
                return json.loads(e.read() or b"{}")
            except Exception:
                return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": f"cannot reach Ember bridge at {self.url}: {e}"}

    def list_tools(self) -> list[dict]:
        res = self._request("GET", "/mcp/tools")
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "failed to list tools"))
        return res.get("tools", [])

    def call_tool(self, name: str, args: dict) -> dict:
        res = self._request("POST", "/mcp/call", {"name": name, "args": args or {}})
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "call failed")}
        return res.get("result", {})


def _python_type(schema: dict):
    kind = (schema or {}).get("type")
    if isinstance(kind, list):
        kind = next((value for value in kind if value != "null"), "object")
    return {"string": str, "integer": int, "number": float, "boolean": bool,
            "array": list, "object": dict}.get(kind, Any)


def _make_handler(client: BridgeClient, tool: dict):
    """Create a callable whose visible signature mirrors the MCP input schema.

    FastMCP derives inputSchema from Python signatures. A plain **kwargs forwarder loses every
    argument, which made most Ember tools appear parameterless in clients. `__signature__` keeps
    registration dynamic while preserving required names and primitive types.
    """
    name = tool["name"]
    schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])

    def handler(**kwargs) -> dict:
        return client.call_tool(name, kwargs)

    parameters = []
    for field, field_schema in properties.items():
        if not isinstance(field, str) or not field.isidentifier():
            continue
        default = inspect.Parameter.empty if field in required else (
            field_schema.get("default") if isinstance(field_schema, dict) and
            "default" in field_schema else None)
        parameters.append(inspect.Parameter(
            field, inspect.Parameter.KEYWORD_ONLY, default=default,
            annotation=_python_type(field_schema if isinstance(field_schema, dict) else {})))
    handler.__name__ = name
    handler.__doc__ = tool.get("description", "")
    # FastMCP structured output requires a parameterised JSON-object return type; the stable
    # SDK rejects bare ``dict`` as ambiguous/non-serializable.
    handler.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, Any])
    return handler


def bridge_diagnostics(client: BridgeClient) -> dict:
    tools = client.list_tools()
    invalid = []
    for tool in tools:
        if not isinstance(tool.get("inputSchema"), dict):
            invalid.append(f"{tool.get('name')}: missing inputSchema")
        annotations = tool.get("annotations") or {}
        for key in ("readOnlyHint", "openWorldHint", "destructiveHint"):
            if not isinstance(annotations.get(key), bool):
                invalid.append(f"{tool.get('name')}: missing {key}")
    return {"ok": not invalid, "bridge": client.url, "tools": len(tools),
            "invalid": invalid, "all_features_free": True}


def build_server(client: BridgeClient, host: str = "127.0.0.1", port: int = 8781):
    """Construct a FastMCP server that mirrors Ember's complete live registry."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - only when the SDK is absent
        raise RuntimeError(
            "The 'mcp' package is required to run the Ember MCP server. "
            "Install it with: pip install 'mcp>=1.27,<2'"
        ) from e

    try:
        server = FastMCP(
            "ember", host=host, port=int(port),
            instructions=("Use Ember to operate the user's local computer. Every local Ember "
                          "tool is free. Respect tool impact annotations and ask before risky "
                          "or irreversible actions."))
    except TypeError:  # older MCP SDK; stdio remains supported
        server = FastMCP("ember")
        if hasattr(server, "settings"):
            try:
                server.settings.host = host
                server.settings.port = int(port)
            except Exception:
                pass

    # Register each live Ember tool as an MCP tool. We enumerate at startup; the client sees
    # the same tool set the running Ember exposes (respecting its capability mode).
    tools = client.list_tools()

    for tool in tools:
        name = tool["name"]
        handler = _make_handler(client, tool)
        kwargs = {"name": name, "description": tool.get("description", "")}
        try:
            supported = inspect.signature(server.add_tool).parameters
        except Exception:
            supported = {}
        if "title" in supported:
            kwargs["title"] = tool.get("title") or name.replace("_", " ").title()
        if "annotations" in supported:
            annotations = tool.get("annotations") or {}
            try:
                from mcp.types import ToolAnnotations
                annotations = ToolAnnotations(**annotations)
            except Exception:
                pass
            kwargs["annotations"] = annotations
        if "structured_output" in supported:
            kwargs["structured_output"] = True
        if "meta" in supported:
            kwargs["meta"] = {"ember/allToolsFree": True, "ember/liveRegistry": True}
        server.add_tool(handler, **kwargs)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ember MCP server — every live tool, free.")
    parser.add_argument("--url", help="Ember bridge base URL (default: auto-detect)")
    parser.add_argument("--token", help="Ember bridge token (default: auto-detect)")
    parser.add_argument("--list", action="store_true",
                        help="List the tools Ember currently exposes, then exit.")
    parser.add_argument("--json", action="store_true", help="Use JSON output with --list/--doctor.")
    parser.add_argument("--doctor", action="store_true",
                        help="Check bridge connectivity, tool schemas, and ChatGPT annotations.")
    parser.add_argument("--transport", choices=("stdio", "streamable-http", "sse"),
                        default="stdio", help="MCP transport (default: stdio).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="HTTP bind host; loopback only (default 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8781,
                        help="HTTP MCP port for streamable-http/SSE (default 8781).")
    args = parser.parse_args(argv)

    url, token = _resolve_bridge(args.url, args.token)
    client = BridgeClient(url, token)

    if args.doctor:
        result = bridge_diagnostics(client)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(("OK" if result["ok"] else "FAILED") +
                  f" — {result['tools']} tools, all local features free")
            for problem in result["invalid"][:20]:
                print("  -", problem)
        return 0 if result["ok"] else 1

    if args.list:
        tools = client.list_tools()
        if args.json:
            print(json.dumps(tools, indent=2))
        else:
            for t in tools:
                print(f"{t['name']}: {t.get('description', '')[:80]}")
            print(f"\n{len(tools)} tools — all local Ember features are free.")
        return 0

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(
            "Refusing a non-loopback MCP bind. Use ChatGPT's Secure MCP Tunnel, SSH forwarding, "
            "or a separately authenticated reverse proxy instead of exposing local computer tools.")
    server = build_server(client, host=args.host, port=args.port)
    try:
        server.run(transport=args.transport)
    except TypeError:
        if args.transport != "stdio":
            raise RuntimeError("This installed MCP SDK is too old for HTTP transport. Upgrade with: "
                               f"{sys.executable} -m pip install --upgrade mcp")
        server.run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as e:
        print(f"[ember-mcp] {e}", file=sys.stderr)
        raise SystemExit(1)
