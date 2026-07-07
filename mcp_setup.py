"""One-click MCP setup — wire Ember into an MCP client (Claude Desktop / Cursor) with no manual
Python-path hunting or JSON editing.

Ember knows the exact Python it's running on (sys.executable), so it can:
  1. install the `mcp` SDK into that interpreter,
  2. write/merge the client's config file with the correct absolute paths,
so the user just presses a button (or asks Ember "set up MCP for Claude Desktop").

Everything is best-effort and returns a structured result; it never raises into the caller.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _server_script() -> str:
    return str(Path(__file__).resolve().parent / "ember_mcp_server.py")


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _client_command_and_args() -> tuple[str, list]:
    """How the MCP client should launch the Ember MCP server.

    - Frozen .app: run the app binary itself with --mcp-server. No separate Python needed — the
      app bundles the mcp SDK and main.py routes --mcp-server to the server. This is what makes
      one-click setup work for users who have no system Python at all.
    - From source: Ember's own interpreter (sys.executable, guaranteed 3.10+ with our deps)
      running ember_mcp_server.py directly.
    """
    if _is_frozen():
        return sys.executable, ["--mcp-server"]
    return sys.executable, [_server_script()]


# --- client config locations -----------------------------------------------------------

def claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


# --- steps -----------------------------------------------------------------------------

def ensure_mcp_installed() -> tuple[bool, str]:
    """Make sure the `mcp` SDK is available to the client's launcher. Installs it when running
    from source; in a frozen .app it's bundled at build time (can't pip into the app)."""
    if _is_frozen():
        # Verify the bundled interpreter can import mcp by launching the server in --help/list-less
        # mode is overkill; trust the build. If it's missing, --mcp-server surfaces a clear error.
        return True, "mcp SDK is bundled in the Ember app"
    py = sys.executable
    try:
        r = subprocess.run([py, "-c", "import mcp"], capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, "mcp SDK already installed"
    except Exception:
        pass
    # Install it. --user/--break-system-packages covers the common macOS "externally managed" case.
    for args in (["-m", "pip", "install", "mcp"],
                 ["-m", "pip", "install", "--user", "--break-system-packages", "mcp"]):
        try:
            r = subprocess.run([py] + args, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                return True, "installed the mcp SDK"
        except Exception:
            continue
    return False, ("Could not install the mcp SDK automatically. Run this yourself:\n"
                   f"  {py} -m pip install mcp")


def configure_claude_desktop() -> tuple[bool, str]:
    """Add (or update) the 'ember' server in Claude Desktop's config, preserving everything else."""
    command, args = _client_command_and_args()
    if not command:
        return False, "Could not determine how to launch the Ember MCP server."
    cfg = claude_desktop_config_path()
    try:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text())
            except Exception:
                # Don't lose a corrupt/hand-edited file — back it up, then start clean.
                bak = cfg.with_suffix(".json.bak")
                shutil.copyfile(cfg, bak)
                data = {}
        if not isinstance(data, dict):
            data = {}
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            data["mcpServers"] = servers
        servers["ember"] = {"command": command, "args": args}
        cfg.write_text(json.dumps(data, indent=2))
        return True, str(cfg)
    except Exception as e:
        return False, f"could not write {cfg}: {e}"


def setup_claude_desktop(install: bool = True) -> dict:
    """Full one-click setup: install mcp (optional) + write the Claude Desktop config.
    Returns {ok, steps, config, python, server, note}."""
    steps = []
    if install:
        ok, msg = ensure_mcp_installed()
        steps.append(msg)
        if not ok:
            return {"ok": False, "steps": steps, "error": msg}
    ok, where = configure_claude_desktop()
    if not ok:
        steps.append(where)
        return {"ok": False, "steps": steps, "error": where}
    steps.append(f"wrote Claude Desktop config: {where}")
    command, args = _client_command_and_args()
    return {
        "ok": True,
        "steps": steps,
        "config": where,
        "launcher": " ".join([command] + args),
        "frozen": _is_frozen(),
        "note": ("Now quit Claude Desktop (Cmd/Ctrl+Q) and reopen it, with Ember running and the "
                 "MCP bridge on. Ember will appear under the tools icon."),
    }


# --- tool surface (merged into the agent + callable from the UI button) ----------------

def _tool_setup_mcp_client(client: str = "claude") -> dict:
    """Set up an external MCP client to control Ember. Currently supports Claude Desktop."""
    c = (client or "claude").strip().lower()
    if c in ("claude", "claude_desktop", "claude-desktop", "desktop"):
        return setup_claude_desktop()
    return {"ok": False, "error": f"unknown MCP client '{client}'. Supported: claude"}


TOOL_DECLARATIONS = [
    {
        "name": "setup_mcp_client",
        "description": ("One-click: wire an external MCP client (Claude Desktop) to control Ember "
                        "— installs the mcp SDK into Ember's Python and writes the client's config "
                        "with the correct paths. No manual setup needed. Remember to turn on the "
                        "MCP bridge (start_mcp_bridge) too."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "client": {"type": "STRING", "description": "which client (default 'claude')"},
            },
            "required": [],
        },
    },
]

TOOL_DISPATCH = {
    "setup_mcp_client": _tool_setup_mcp_client,
}
