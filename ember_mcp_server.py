"""Ember MCP server — exposes Ember's tools to any MCP client (Claude Desktop, Cursor, …).

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

Requires: pip install mcp   (the official Model Context Protocol SDK). The import is lazy so
this module stays importable — and unit-testable — without the SDK or a running Ember.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Ember-Token", self.token)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
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


def build_server(client: BridgeClient):
    """Construct a FastMCP server that mirrors Ember's tools. Imports mcp lazily."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - only when the SDK is absent
        raise RuntimeError(
            "The 'mcp' package is required to run the Ember MCP server. "
            "Install it with: pip install mcp"
        ) from e

    server = FastMCP("ember")

    # Register each live Ember tool as an MCP tool. We enumerate at startup; the client sees
    # the same tool set the running Ember exposes (respecting its capability mode).
    tools = client.list_tools()

    def _make_handler(tool_name: str):
        def _handler(**kwargs) -> str:
            result = client.call_tool(tool_name, kwargs)
            return json.dumps(result, ensure_ascii=False, default=str)
        return _handler

    for tool in tools:
        name = tool["name"]
        handler = _make_handler(name)
        handler.__name__ = name
        server.add_tool(
            handler,
            name=name,
            description=tool.get("description", ""),
        )
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ember MCP server (stdio).")
    parser.add_argument("--url", help="Ember bridge base URL (default: auto-detect)")
    parser.add_argument("--token", help="Ember bridge token (default: auto-detect)")
    parser.add_argument("--list", action="store_true",
                        help="List the tools Ember currently exposes, then exit.")
    args = parser.parse_args(argv)

    url, token = _resolve_bridge(args.url, args.token)
    client = BridgeClient(url, token)

    if args.list:
        for t in client.list_tools():
            print(f"{t['name']}: {t.get('description', '')[:80]}")
        return 0

    server = build_server(client)
    server.run()  # stdio transport
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as e:
        print(f"[ember-mcp] {e}", file=sys.stderr)
        raise SystemExit(1)
