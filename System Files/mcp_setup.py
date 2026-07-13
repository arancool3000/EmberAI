"""One-click MCP setup — wire Ember into ChatGPT or another MCP client with no manual
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
import atexit
import time
from pathlib import Path


_CHATGPT_PROCESS = None
_CHATGPT_PORT = 8781
_MCP_DEPENDENCY = "mcp>=1.27,<2"


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
        # Importability alone is insufficient: older SDKs don't support the HTTP transport and
        # metadata ChatGPT needs. Check the installed distribution version without importing it.
        version_check = (
            "from importlib.metadata import version; "
            "p=version('mcp').split('+',1)[0].split('-',1)[0].split('.'); "
            "v=tuple(int(''.join(c for c in x if c.isdigit()) or 0) for x in p[:3]); "
            "raise SystemExit(0 if (1,27,0) <= v < (2,0,0) else 1)"
        )
        r = subprocess.run([py, "-c", version_check], capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return True, "compatible mcp SDK already installed"
    except Exception:
        pass
    # Install it. --user/--break-system-packages covers the common macOS "externally managed" case.
    for args in (["-m", "pip", "install", "--upgrade", _MCP_DEPENDENCY],
                 ["-m", "pip", "install", "--user", "--break-system-packages", "--upgrade",
                  _MCP_DEPENDENCY]):
        try:
            r = subprocess.run([py] + args, capture_output=True, text=True, timeout=600)
            if r.returncode == 0:
                return True, "installed the mcp SDK"
        except Exception:
            continue
    return False, ("Could not install the mcp SDK automatically. Run this yourself:\n"
                   f"  {py} -m pip install --upgrade '{_MCP_DEPENDENCY}'")


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


def _stop_chatgpt_process() -> None:
    global _CHATGPT_PROCESS
    process = _CHATGPT_PROCESS
    _CHATGPT_PROCESS = None
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


atexit.register(_stop_chatgpt_process)


def chatgpt_mcp_status() -> dict:
    running = bool(_CHATGPT_PROCESS is not None and _CHATGPT_PROCESS.poll() is None)
    return {"ok": True, "running": running, "host": "127.0.0.1", "port": _CHATGPT_PORT,
            "url": f"http://127.0.0.1:{_CHATGPT_PORT}/mcp" if running else None,
            "all_tools": True, "all_features_free": True}


def start_chatgpt_mcp(port: int = 8781, install: bool = True) -> dict:
    """Start a loopback Streamable-HTTP MCP endpoint for ChatGPT's Secure MCP Tunnel."""
    global _CHATGPT_PROCESS, _CHATGPT_PORT
    if _CHATGPT_PROCESS is not None and _CHATGPT_PROCESS.poll() is None:
        return {"ok": True, "already_running": True, **chatgpt_mcp_status()}
    try:
        port = int(port)
        if port < 1024 or port > 65535:
            return {"ok": False, "error": "port must be between 1024 and 65535"}
    except Exception:
        return {"ok": False, "error": "port must be an integer"}
    if install:
        ok, note = ensure_mcp_installed()
        if not ok:
            return {"ok": False, "error": note}
    try:
        import ember_bridge
        if not ember_bridge.status().get("running"):
            started = ember_bridge.start()
            if not started.get("ok"):
                return {"ok": False, "error": started.get("error", "could not start bridge")}
    except Exception as exc:
        return {"ok": False, "error": f"could not start Ember's local bridge: {exc}"}

    command, base_args = _client_command_and_args()
    args = [*base_args, "--transport", "streamable-http", "--host", "127.0.0.1",
            "--port", str(port)]
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen([command, *args], **kwargs)
        time.sleep(0.45)
        if process.poll() is not None:
            return {"ok": False, "error": (
                "The ChatGPT MCP endpoint exited during startup. The port may be in use or the "
                "MCP SDK may need upgrading. Run ember_mcp_server.py --doctor for details.")}
        _CHATGPT_PROCESS = process
        _CHATGPT_PORT = port
        return {"ok": True, "started": True, **chatgpt_mcp_status(),
                "connection": (
                    "In ChatGPT developer mode, create an app using Secure MCP Tunnel and point "
                    f"it at http://127.0.0.1:{port}/mcp. Refresh metadata after Ember updates.")}
    except Exception as exc:
        return {"ok": False, "error": f"could not start ChatGPT MCP endpoint: {exc}"}


def stop_chatgpt_mcp() -> dict:
    was_running = bool(_CHATGPT_PROCESS is not None and _CHATGPT_PROCESS.poll() is None)
    _stop_chatgpt_process()
    return {"ok": True, "stopped": was_running}


# --- tool surface (merged into the agent + callable from the UI button) ----------------

def _tool_setup_mcp_client(client: str = "chatgpt") -> dict:
    """Set up an external MCP client to control Ember."""
    c = (client or "chatgpt").strip().lower()
    if c in ("chatgpt", "openai", "chatgpt-app"):
        return start_chatgpt_mcp()
    if c in ("claude", "claude_desktop", "claude-desktop", "desktop"):
        return setup_claude_desktop()
    return {"ok": False, "error": f"unknown MCP client '{client}'. Supported: chatgpt, claude"}


TOOL_DECLARATIONS = [
    {
        "name": "setup_mcp_client",
        "description": ("One-click: expose every free Ember tool to ChatGPT or Claude Desktop "
                        "— installs the mcp SDK into Ember's Python and writes the client's config "
                        "with the correct paths. No manual setup needed. Remember to turn on the "
                        "MCP bridge (start_mcp_bridge) too."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "client": {"type": "STRING", "description": "chatgpt (default) or claude"},
            },
            "required": [],
        },
    },
    {"name": "start_chatgpt_mcp",
     "description": "Start Ember's free loopback Streamable-HTTP MCP endpoint for ChatGPT.",
     "parameters": {"type": "OBJECT", "properties": {
         "port": {"type": "INTEGER", "description": "local port, default 8781"},
         "install": {"type": "BOOLEAN", "description": "install/verify MCP SDK first"}},
         "required": []}},
    {"name": "stop_chatgpt_mcp",
     "description": "Stop Ember's ChatGPT Streamable-HTTP MCP endpoint.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "chatgpt_mcp_status",
     "description": "Report the local ChatGPT MCP endpoint and confirm all tools are free.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "setup_mcp_client": _tool_setup_mcp_client,
    "start_chatgpt_mcp": start_chatgpt_mcp,
    "stop_chatgpt_mcp": stop_chatgpt_mcp,
    "chatgpt_mcp_status": chatgpt_mcp_status,
}
