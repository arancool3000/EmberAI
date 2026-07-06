"""Ember MCP bridge — a loopback-only control server that exposes Ember's tools for RPC.

This is the in-app half of Ember's MCP support (analogous to the blender-mcp *addon* that
runs inside Blender). It lets an external process — `ember_mcp_server.py`, launched by an MCP
client such as Claude Desktop or Cursor — list and invoke Ember's ~290 tools **in the live,
running Ember app**, so browser/screen/memory tools operate on the real session. The MCP
server never imports Ember; it just talks HTTP+JSON to this bridge.

Security model (this endpoint can run shell commands, so it is deliberately locked down):
  * Binds to 127.0.0.1 ONLY — never 0.0.0.0, never tunnelled. Non-loopback peers are refused.
  * Every request needs a random bearer token (persisted to Ember's support dir, 0600).
  * Ember's capability MODE is enforced (safety.mode_allows) — read-only / restricted modes
    still apply, exactly as in the agent loop.
  * High-risk tools (the ones that would pop a human confirmation in the app) are BLOCKED by
    default, because there is no human at the MCP layer to approve them. The user can opt in
    per-session with allow_high_risk=True (surfaced as an explicit, off-by-default setting).
  * Off by default — the user must start the bridge.

The heavy `agent` import (tool registries) happens lazily inside start()/the request handler,
so this module — and its pure helpers (translate_schema, tool_to_mcp, execute_tool) — import
with only the standard library, keeping the hermetic tests and any non-GUI use working.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
BRIDGE_NAME = "ember"

# Host-special tools that need the live agent turn loop (not a plain dispatch entry); never
# exposed over the bridge even if they appear in declarations.
_AGENT_ONLY_TOOLS = {"ask_claude", "pause_for_human", "spawn_agent", "agent_run", "run_custom_tool"}

# Gemini's uppercase JSON-Schema type names → standard lowercase for MCP inputSchema.
_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "BOOLEAN": "boolean",
    "INTEGER": "integer", "NUMBER": "number", "ARRAY": "array", "NULL": "null",
}


def _support_dir() -> Path:
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Ember"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _info_path() -> Path:
    return _support_dir() / "mcp_bridge.json"


# --- pure helpers (unit-testable without importing agent) ------------------------------

def translate_schema(node):
    """Recursively translate a Gemini parameters dict to a JSON-Schema inputSchema.

    Lowercases the uppercase Gemini type names so MCP clients accept the schema.
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = _TYPE_MAP.get(v, v.lower())
            else:
                out[k] = translate_schema(v)
        return out
    if isinstance(node, list):
        return [translate_schema(x) for x in node]
    return node


def tool_to_mcp(decl: dict) -> dict:
    """Map one Ember tool declaration to an MCP tool descriptor."""
    schema = translate_schema(decl.get("parameters", {"type": "object"}))
    if not isinstance(schema, dict) or "type" not in schema:
        schema = {"type": "object", "properties": {}}
    schema.setdefault("properties", {})
    return {
        "name": decl["name"],
        "description": decl.get("description", ""),
        "inputSchema": schema,
    }


def list_tools_from(declarations: list, dispatch: dict) -> list:
    """Build the MCP tool list — only tools that are actually dispatchable and not agent-only."""
    out = []
    seen = set()
    for decl in declarations:
        name = decl.get("name")
        if not name or name in seen or name in _AGENT_ONLY_TOOLS:
            continue
        if name not in dispatch:
            continue
        seen.add(name)
        out.append(tool_to_mcp(decl))
    return out


def execute_tool(name: str, args: dict, dispatch: dict, *,
                 allow_high_risk: bool = False,
                 param_types: dict | None = None) -> dict:
    """Safety-gated tool execution shared by the bridge. Mirrors the agent dispatch guard.

    Order: reject agent-only/unknown → classify risk → enforce capability mode →
    block high-risk (unless opted in) → coerce arg types → dispatch. Always returns a dict.
    """
    import safety
    args = args if isinstance(args, dict) else {}
    if name in _AGENT_ONLY_TOOLS:
        return {"ok": False, "error": f"tool '{name}' is only available inside Ember's agent loop"}
    fn = dispatch.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown tool {name}"}

    try:
        risk, reason = safety.classify(name, args)
    except Exception:
        risk, reason = "medium", "unclassified"
    allowed, mode_reason = safety.mode_allows(name, risk)
    if not allowed:
        return {"ok": False, "error": mode_reason, "blocked_by_mode": safety.current_mode()}
    if safety.needs_confirmation(risk) and not allow_high_risk:
        return {"ok": False, "needs_confirmation": True,
                "error": (f"'{name}' is high-risk ({reason}) and needs confirmation. It is "
                          "blocked over MCP unless you enable 'allow high-risk over MCP' in "
                          "Ember. Run it in the Ember app to approve interactively.")}

    if param_types:
        try:
            import tool_args
            args = tool_args.coerce(param_types.get(name, {}), args)
        except Exception:
            pass

    try:
        result = fn(**args)
    except TypeError as e:
        return {"ok": False, "error": f"bad args: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    # Drop base64 image payloads — MCP text transport can't use them and they bloat responses.
    if result.get("image_b64"):
        result = {**result, "image_b64": "[omitted: retrieve via the Ember app]"}
    try:
        import audit
        audit.record(name, args, risk, str(result.get("error") or "")[:200])
    except Exception:
        pass
    return result


# --- live registries (lazy: importing agent pulls the whole app) -----------------------

def _registries():
    """Return (declarations, dispatch, param_types) from the running app."""
    import agent
    param_types = {}
    try:
        import tool_args
        param_types = tool_args.build_param_types(agent.TOOL_DECLARATIONS)
    except Exception:
        param_types = {}
    return agent.TOOL_DECLARATIONS, agent.TOOL_DISPATCH, param_types


# --- HTTP server -----------------------------------------------------------------------

class _State:
    server: "ThreadingHTTPServer | None" = None
    thread: "threading.Thread | None" = None
    token: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_high_risk: bool = False


_STATE = _State()
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # silence stdlib request logging
        pass

    def _client_is_local(self) -> bool:
        addr = (self.client_address or ["", 0])[0]
        return addr in ("127.0.0.1", "::1", "localhost")

    def _authed(self) -> bool:
        if not self._client_is_local():
            return False
        tok = self.headers.get("X-Ember-Token") or ""
        if not tok:
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                tok = auth[7:].strip()
        return bool(_STATE.token) and secrets.compare_digest(tok, _STATE.token)

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path.rstrip("/") == "/mcp/ping":
            # Unauthenticated liveness probe (no data disclosed) so the MCP server can wait
            # for the bridge to come up.
            return self._send(200, {"ok": True, "name": BRIDGE_NAME})
        if not self._authed():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if self.path.rstrip("/") == "/mcp/tools":
            try:
                decls, dispatch, _pt = _registries()
                return self._send(200, {"ok": True, "tools": list_tools_from(decls, dispatch)})
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if self.path.rstrip("/") != "/mcp/call":
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        name = (body or {}).get("name")
        args = (body or {}).get("args") or {}
        if not name:
            return self._send(400, {"ok": False, "error": "missing tool name"})
        try:
            _decls, dispatch, param_types = _registries()
            result = execute_tool(name, args, dispatch,
                                  allow_high_risk=_STATE.allow_high_risk,
                                  param_types=param_types)
            return self._send(200, {"ok": True, "result": result})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})


def _write_info():
    info = {"host": _STATE.host, "port": _STATE.port, "token": _STATE.token,
            "url": f"http://{_STATE.host}:{_STATE.port}"}
    try:
        p = _info_path()
        p.write_text(json.dumps(info, indent=2))
        try:
            os.chmod(p, 0o600)  # token is a secret
        except Exception:
            pass
    except Exception:
        pass
    return info


def load_bridge_info() -> dict | None:
    """Read the persisted {host, port, token, url}. Used by ember_mcp_server.py."""
    try:
        return json.loads(_info_path().read_text())
    except Exception:
        return None


def start(port: int = DEFAULT_PORT, allow_high_risk: bool = False,
          token: str | None = None) -> dict:
    """Start the loopback bridge. Idempotent; returns a status dict with the token to paste
    into the MCP client's config."""
    with _LOCK:
        if _STATE.server is not None:
            _STATE.allow_high_risk = bool(allow_high_risk)
            return {"ok": True, "already_running": True, **status()}
        _STATE.host = DEFAULT_HOST  # loopback only — not configurable, by design
        _STATE.port = int(port or DEFAULT_PORT)
        _STATE.token = token or secrets.token_urlsafe(24)
        _STATE.allow_high_risk = bool(allow_high_risk)
        try:
            server = ThreadingHTTPServer((_STATE.host, _STATE.port), _Handler)
        except OSError as e:
            _STATE.server = None
            return {"ok": False, "error": f"could not bind {_STATE.host}:{_STATE.port}: {e}"}
        server.daemon_threads = True
        _STATE.server = server
        t = threading.Thread(target=server.serve_forever, name="ember-mcp-bridge", daemon=True)
        _STATE.thread = t
        t.start()
        info = _write_info()
        return {"ok": True, "started": True, "host": _STATE.host, "port": _STATE.port,
                "url": info["url"], "token": _STATE.token,
                "allow_high_risk": _STATE.allow_high_risk}


def stop() -> dict:
    with _LOCK:
        srv = _STATE.server
        if srv is None:
            return {"ok": True, "stopped": False, "note": "bridge was not running"}
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
        _STATE.server = None
        _STATE.thread = None
        try:
            _info_path().unlink()
        except Exception:
            pass
        return {"ok": True, "stopped": True}


def status() -> dict:
    running = _STATE.server is not None
    return {"ok": True, "running": running, "host": _STATE.host, "port": _STATE.port,
            "url": f"http://{_STATE.host}:{_STATE.port}" if running else None,
            "allow_high_risk": _STATE.allow_high_risk,
            "token": _STATE.token if running else None}


# --- tools exposed to Ember's own agent (merged in agent.py) ---------------------------

def _tool_start_mcp_bridge(port: int = DEFAULT_PORT, allow_high_risk: bool = False) -> dict:
    return start(port=int(port or DEFAULT_PORT), allow_high_risk=bool(allow_high_risk))


def _tool_stop_mcp_bridge() -> dict:
    return stop()


def _tool_mcp_bridge_status() -> dict:
    return status()


TOOL_DECLARATIONS = [
    {
        "name": "start_mcp_bridge",
        "description": ("Start Ember's MCP bridge so an external MCP client (Claude Desktop, "
                        "Cursor) can control Ember's tools. Loopback-only + token-secured. "
                        "Returns the token to paste into the client's config."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "port": {"type": "INTEGER", "description": f"local port (default {DEFAULT_PORT})"},
                "allow_high_risk": {"type": "BOOLEAN",
                    "description": "allow high-risk tools over MCP with no human confirmation (default false)"},
            },
            "required": [],
        },
    },
    {
        "name": "stop_mcp_bridge",
        "description": "Stop Ember's MCP bridge.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "mcp_bridge_status",
        "description": "Report whether Ember's MCP bridge is running, plus its URL/port.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
]

TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "start_mcp_bridge": _tool_start_mcp_bridge,
    "stop_mcp_bridge": _tool_stop_mcp_bridge,
    "mcp_bridge_status": _tool_mcp_bridge_status,
}

# These management tools are safe/low-risk toggles — mark read-only-ish so capability modes
# don't block simply checking status. (start/stop still respect the user's intent.)
READ_ONLY_TOOLS = {"mcp_bridge_status"}
