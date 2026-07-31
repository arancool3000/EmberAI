"""Connect Ember to Blender through the Blender MCP addon (blender-mcp).

Ember speaks the blender-mcp addon's own JSON-over-TCP protocol directly — the same addon
people already install in Blender to use it with Claude Desktop or Cursor (N-panel →
BlenderMCP → "Connect to Claude"). No MCP server process, no extra dependency: Ember sends
`{"type": ..., "params": ...}` to the addon's loopback socket (default 127.0.0.1:9876) and
reads back one JSON reply per command, so "make me a donut in Blender" just works with
Blender + the addon running.

Everything executes inside the user's LIVE Blender session. `blender_run_python` runs
arbitrary Python (bpy) there, so safety.py classifies it high-risk (confirmation prompt);
the status/scene/object/screenshot tools are read-only.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from typing import Callable

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876            # the blender-mcp addon's default port
_CONNECT_TIMEOUT = 5.0
_RESPONSE_TIMEOUT = 90.0       # execute_code on a heavy scene can legitimately take a while
_CHUNK = 8192

_NOT_REACHABLE_HELP = (
    "Open Blender, install/enable the free 'Blender MCP' addon (blender-mcp), press N in the "
    "3D viewport, and click 'Connect' in the BlenderMCP tab — then try again."
)


def _addr() -> tuple[str, int]:
    """Where the addon listens. BLENDER_HOST / BLENDER_PORT match blender-mcp's own env vars."""
    host = os.environ.get("BLENDER_HOST") or DEFAULT_HOST
    try:
        port = int(os.environ.get("BLENDER_PORT") or DEFAULT_PORT)
    except ValueError:
        port = DEFAULT_PORT
    return host, port


def _send_command(cmd_type: str, params: dict | None = None,
                  timeout: float = _RESPONSE_TIMEOUT) -> dict:
    """Run one addon command over a fresh loopback connection and return its `result`.

    A reply is complete when the buffered bytes parse as JSON — the same rule blender-mcp's
    own client uses (the addon sends a single JSON object, no delimiter). Raises RuntimeError
    with a human-readable message on any failure, including an addon-side "status": "error".
    """
    host, port = _addr()
    try:
        sock = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
    except OSError as e:
        raise RuntimeError(f"Blender is not reachable on {host}:{port} ({e}). "
                           + _NOT_REACHABLE_HELP) from e
    try:
        sock.settimeout(timeout)
        sock.sendall(json.dumps({"type": cmd_type, "params": params or {}}).encode("utf-8"))
        buf = b""
        while True:
            try:
                chunk = sock.recv(_CHUNK)
            except socket.timeout:
                raise RuntimeError(
                    f"Blender did not answer '{cmd_type}' within {int(timeout)}s. A modal "
                    "operator or a headless Blender (blender -b) never runs addon commands.")
            if not chunk:
                raise RuntimeError(f"Blender closed the connection during '{cmd_type}'. "
                                   + _NOT_REACHABLE_HELP)
            buf += chunk
            try:
                response = json.loads(buf.decode("utf-8"))
                break                      # complete JSON — the reply is whole
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue                   # partial reply — keep reading
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if not isinstance(response, dict):
        raise RuntimeError(f"Blender sent an unexpected reply to '{cmd_type}': {response!r:.200}")
    if response.get("status") == "error":
        raise RuntimeError(str(response.get("message") or f"Blender error on '{cmd_type}'"))
    result = response.get("result")
    return result if isinstance(result, dict) else {"result": result}


# ---- tools -------------------------------------------------------------------------------

def blender_status() -> dict:
    """Is Blender + the addon reachable? Cheap probe with a short timeout."""
    host, port = _addr()
    try:
        scene = _send_command("get_scene_info", timeout=15.0)
    except RuntimeError as e:
        return {"ok": True, "connected": False, "host": host, "port": port, "error": str(e)}
    return {"ok": True, "connected": True, "host": host, "port": port,
            "scene": scene.get("name"),
            "objects": scene.get("object_count", len(scene.get("objects") or []))}


def blender_scene_info() -> dict:
    try:
        return {"ok": True, "scene": _send_command("get_scene_info", timeout=30.0)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


def blender_object_info(name: str = "") -> dict:
    if not (name or "").strip():
        return {"ok": False, "error": "name is required — an object in the scene, e.g. 'Cube' "
                                      "(blender_scene_info lists them)"}
    try:
        return {"ok": True,
                "object": _send_command("get_object_info", {"name": name}, timeout=30.0)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}


def blender_run_python(code: str = "") -> dict:
    if not (code or "").strip():
        return {"ok": False, "error": "code is required (Python using the bpy API)"}
    try:
        result = _send_command("execute_code", {"code": code})
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **result}


def blender_screenshot(max_size: int = 800) -> dict:
    """Ask Blender to save a viewport screenshot to a temp file; return the path."""
    path = os.path.join(tempfile.gettempdir(), f"ember_blender_{int(time.time() * 1000)}.png")
    try:
        _send_command("get_viewport_screenshot",
                      {"max_size": int(max_size or 800), "filepath": path, "format": "png"},
                      timeout=30.0)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    if not os.path.exists(path):
        return {"ok": False, "error": "Blender did not write the screenshot — viewport "
                                      "screenshots need a normal (non-headless) Blender window."}
    return {"ok": True, "path": path}


# ---- exports for wiring ------------------------------------------------------------------

TOOL_DECLARATIONS = [
    {"name": "blender_status",
     "description": "Check whether Ember can reach Blender (via the Blender MCP addon's local "
                    "socket). Reports the scene name and object count when connected, or how "
                    "to set the connection up when not. Use this first for any Blender task.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "blender_scene_info",
     "description": "Get the current Blender scene: its name, objects (with types/locations), "
                    "and material count. Use it to see what exists before changing anything.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "blender_object_info",
     "description": "Get one Blender object's details: location, rotation, scale, dimensions, "
                    "materials, and mesh stats.",
     "parameters": {"type": "OBJECT",
                    "properties": {"name": {"type": "STRING",
                                            "description": "the object's name, e.g. 'Cube'"}},
                    "required": ["name"]}},
    {"name": "blender_run_python",
     "description": "Run Python (the bpy API) inside the user's live Blender session — create "
                    "or edit objects, materials, modifiers, animations, lights, cameras, or "
                    "render. This is how Ember DOES things in Blender: e.g. "
                    "bpy.ops.mesh.primitive_torus_add(). Keep each snippet small and verify "
                    "with blender_scene_info or blender_screenshot after.",
     "parameters": {"type": "OBJECT",
                    "properties": {"code": {"type": "STRING",
                                            "description": "Python code using bpy"}},
                    "required": ["code"]}},
    {"name": "blender_screenshot",
     "description": "Save a screenshot of Blender's 3D viewport to a temp PNG and return its "
                    "path, so Ember can see the scene as the user does.",
     "parameters": {"type": "OBJECT",
                    "properties": {"max_size": {"type": "INTEGER",
                                                "description": "longest edge in px (default 800)"}},
                    "required": []}},
]

TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "blender_status": blender_status,
    "blender_scene_info": blender_scene_info,
    "blender_object_info": blender_object_info,
    "blender_run_python": blender_run_python,
    "blender_screenshot": blender_screenshot,
}

# Probing/reading the scene (and a viewport screenshot, like take_screenshot) never mutate
# anything — safe even in read-only mode. safety.py lists these in SAFE_READONLY and
# classifies blender_run_python high (confirmation prompt).
READONLY_TOOLS = {"blender_status", "blender_scene_info", "blender_object_info",
                  "blender_screenshot"}
