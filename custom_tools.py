"""AI-authored custom tools — Ember can build, save, reuse and share its own tools.

A *custom tool* is a named, parameterised RECIPE: an ordered list of calls to tools
Ember already has (organize_folder, read_file, browser_fill, run_shell, …) with the
caller's arguments substituted in. Because every step is dispatched through the SAME
executor + safety layer as a normal tool call, a custom tool can never do anything
Ember couldn't already do — each risky step is still gated/confirmed individually.

Stored per-user (survives re-clones / rebuilds) in ``custom_tools.json``:

    {
      "name": "tidy_downloads",
      "description": "Dry-run organize Downloads, then list what changed.",
      "parameters": {"type":"OBJECT","properties":{"folder":{"type":"STRING"}},"required":["folder"]},
      "steps": [
        {"tool":"organize_folder","args":{"path":"{{folder}}","dry_run":true}},
        {"tool":"list_directory","args":{"path":"{{folder}}"}}
      ]
    }

Placeholders ``{{name}}`` in a step's args are replaced with the matching run argument.
A whole-value placeholder ("{{folder}}") keeps the argument's real type; an embedded one
("in {{folder}}/old") is string-interpolated.

The module is pure + host-agnostic: it persists/validates/resolves recipes but never
executes them — agent.py runs the resolved steps through its own _execute_fc so the
events + confirmation + audit all apply. Exposes the standard
TOOL_DECLARATIONS / TOOL_DISPATCH / READONLY_TOOLS / INTERACTION_TOOLS contract.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

# Optional allow-list of tool names a recipe step may call. agent.py populates this with
# the full live tool registry so create_custom_tool can reject typos up-front. Left empty
# in unit tests (no validation) so the module stays standalone-testable.
KNOWN_TOOLS: set[str] = set()

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_WHOLE_RE = re.compile(r"^\s*\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}\s*$")

# Names Ember reserves for its own control verbs — a custom tool can't shadow these.
_RESERVED = {"run_custom_tool", "create_custom_tool", "list_custom_tools",
             "get_custom_tool", "delete_custom_tool", "export_custom_tool",
             "import_custom_tool", "ask_claude", "pause_for_human", "spawn_agent",
             "agent_run"}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        d = Path.home() / ".ember"
    # Tests redirect everything via EMBER_SUPPORT_DIR.
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        d = Path(override)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return _dir() / "custom_tools.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _path().write_text(json.dumps(d, indent=2), "utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_steps(steps: Any) -> tuple[bool, str]:
    if not isinstance(steps, (list, tuple)) or not steps:
        return False, "steps must be a non-empty list of {tool, args} entries"
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return False, f"step {i} is not an object"
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            return False, f"step {i} is missing a 'tool' name"
        if "args" in step and not isinstance(step["args"], dict):
            return False, f"step {i} 'args' must be an object"
        if KNOWN_TOOLS and tool not in KNOWN_TOOLS:
            return False, (f"step {i} calls unknown tool '{tool}'. Use list_* tools to find a real "
                           "tool name (custom-tool steps can only call tools Ember already has).")
    return True, ""


def _normalize_params(parameters: Any) -> dict:
    """Coerce a parameters schema to the OBJECT shape the model expects; default to no params."""
    if not isinstance(parameters, dict) or not parameters:
        return {"type": "OBJECT", "properties": {}, "required": []}
    out = dict(parameters)
    out.setdefault("type", "OBJECT")
    out.setdefault("properties", {})
    out.setdefault("required", [])
    return out


# ---------------------------------------------------------------------------
# CRUD tools (pure — exposed to the model)
# ---------------------------------------------------------------------------

def create_custom_tool(name: str, description: str = "", parameters: Any = None,
                       steps: Any = None, overwrite: bool = False) -> dict:
    """Build and save a reusable custom tool from a recipe of existing tool calls.

    name: snake_case, e.g. 'tidy_downloads'. description: one line of what it does.
    parameters: an OBJECT JSON-schema for the tool's inputs (optional).
    steps: ordered [{ "tool": <existing tool>, "args": {... with {{param}} placeholders} }].
    Run it later with run_custom_tool(name=..., args={...})."""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        return {"ok": False, "error": "name must be snake_case: a letter, then letters/digits/_ (2-49 chars)"}
    if name in _RESERVED:
        return {"ok": False, "error": f"'{name}' is reserved — pick another name"}
    if KNOWN_TOOLS and name in KNOWN_TOOLS:
        return {"ok": False, "error": f"'{name}' is already a built-in Ember tool — pick another name"}
    ok, reason = _validate_steps(steps)
    if not ok:
        return {"ok": False, "error": reason}
    d = _load()
    if name in d and not overwrite:
        return {"ok": False, "error": f"custom tool '{name}' exists — pass overwrite=true to replace it"}
    d[name] = {
        "name": name,
        "description": (description or "").strip() or f"Custom tool {name}",
        "parameters": _normalize_params(parameters),
        "steps": [{"tool": s["tool"], "args": dict(s.get("args") or {})} for s in steps],
        "created": int(time.time()),
    }
    _save(d)
    return {"ok": True, "saved": name, "steps": len(d[name]["steps"]),
            "message": f"Custom tool '{name}' saved. Run it with run_custom_tool(name='{name}', args={{…}})."}


def list_custom_tools() -> dict:
    """List the custom tools Ember has built (name, description, step count)."""
    d = _load()
    tools = [{"name": k, "description": v.get("description", ""),
              "steps": len(v.get("steps", [])),
              "parameters": v.get("parameters", {})} for k, v in sorted(d.items())]
    return {"ok": True, "count": len(tools), "tools": tools}


def get_custom_tool(name: str) -> dict:
    """Show a custom tool's full recipe (steps + parameters)."""
    t = _load().get((name or "").strip())
    if not t:
        return {"ok": False, "error": f"no custom tool '{name}'"}
    return {"ok": True, "tool": t}


def delete_custom_tool(name: str) -> dict:
    """Delete a saved custom tool."""
    d = _load()
    key = (name or "").strip()
    if key not in d:
        return {"ok": False, "error": f"no custom tool '{name}'"}
    d.pop(key)
    _save(d)
    return {"ok": True, "deleted": key}


def export_custom_tool(name: str) -> dict:
    """Export a custom tool as shareable JSON (give the string to someone to import_custom_tool)."""
    t = _load().get((name or "").strip())
    if not t:
        return {"ok": False, "error": f"no custom tool '{name}'"}
    return {"ok": True, "name": name, "json": json.dumps(t, indent=2)}


def import_custom_tool(json_text: str = "", overwrite: bool = False) -> dict:
    """Import a custom tool shared as JSON (from export_custom_tool). Validates before saving."""
    try:
        spec = json.loads(json_text)
    except Exception as e:
        return {"ok": False, "error": f"not valid JSON: {e}"}
    if not isinstance(spec, dict):
        return {"ok": False, "error": "JSON must be a custom-tool object"}
    return create_custom_tool(
        name=spec.get("name", ""), description=spec.get("description", ""),
        parameters=spec.get("parameters"), steps=spec.get("steps"), overwrite=overwrite)


# ---------------------------------------------------------------------------
# Resolution (pure) — agent.py executes the returned steps itself
# ---------------------------------------------------------------------------

def _substitute(value: Any, params: dict) -> Any:
    """Replace {{name}} placeholders in value with params. Whole-value placeholders keep the
    argument's real type; embedded ones are string-interpolated."""
    if isinstance(value, str):
        m = _WHOLE_RE.match(value)
        if m:
            key = m.group(1)
            return params.get(key, value)          # whole-value -> real typed arg
        return _PLACEHOLDER_RE.sub(lambda mm: str(params.get(mm.group(1), mm.group(0))), value)
    if isinstance(value, dict):
        return {k: _substitute(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params) for v in value]
    return value


def resolve_steps(name: str, args: dict | None = None) -> dict:
    """Return a saved tool's steps with run arguments substituted in. Pure; no execution."""
    t = _load().get((name or "").strip())
    if not t:
        return {"ok": False, "error": f"no custom tool '{name}'"}
    params = args if isinstance(args, dict) else {}
    steps = [{"tool": s["tool"], "args": _substitute(dict(s.get("args") or {}), params)}
             for s in t.get("steps", [])]
    return {"ok": True, "name": name, "steps": steps}


# ---------------------------------------------------------------------------
# Host wiring
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict] = [
    {
        "name": "create_custom_tool",
        "description": (
            "Build and SAVE a new reusable tool for yourself from a recipe of tools you already "
            "have. Use this whenever the user asks you to remember a repeatable multi-step "
            "procedure, or you notice you keep doing the same sequence. The recipe's steps each "
            "call an existing tool; put {{placeholders}} in step args for inputs. Persisted across "
            "restarts. Run it later with run_custom_tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "snake_case tool name, e.g. tidy_downloads"},
                "description": {"type": "STRING", "description": "one line: what the tool does"},
                "parameters": {"type": "OBJECT", "description": "OBJECT JSON-schema of the tool's inputs (optional)"},
                "steps": {
                    "type": "ARRAY",
                    "description": "ordered steps; each {\"tool\": <existing tool>, \"args\": {...{{param}}...}}",
                    "items": {"type": "OBJECT", "properties": {
                        "tool": {"type": "STRING"},
                        "args": {"type": "OBJECT"},
                    }},
                },
                "overwrite": {"type": "BOOLEAN", "description": "replace an existing tool of the same name"},
            },
            "required": ["name", "steps"],
        },
    },
    {
        "name": "run_custom_tool",
        "description": (
            "Run a custom tool you built earlier (see list_custom_tools). Pass its name and an "
            "args object matching its parameters; Ember executes the recipe's steps in order, with "
            "each step still gated by the normal safety/confirmation rules."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "the custom tool's name"},
                "args": {"type": "OBJECT", "description": "arguments for the tool's parameters"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_custom_tools",
        "description": "List the custom tools you've built (name, description, step count). Read-only.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "get_custom_tool",
        "description": "Show a custom tool's full recipe (steps + parameters). Read-only.",
        "parameters": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"}}, "required": ["name"]},
    },
    {
        "name": "delete_custom_tool",
        "description": "Delete a custom tool you built.",
        "parameters": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"}}, "required": ["name"]},
    },
    {
        "name": "export_custom_tool",
        "description": "Export a custom tool as shareable JSON (others import_custom_tool it). Read-only.",
        "parameters": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"}}, "required": ["name"]},
    },
    {
        "name": "import_custom_tool",
        "description": "Import a custom tool shared as JSON (from export_custom_tool).",
        "parameters": {"type": "OBJECT", "properties": {
            "json_text": {"type": "STRING", "description": "the exported JSON"},
            "overwrite": {"type": "BOOLEAN"}}, "required": ["json_text"]},
    },
]

# run_custom_tool is executed by the host (agent.py special-cases it) so it can run each
# step through the agent's own executor; it is intentionally NOT in TOOL_DISPATCH.
TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "create_custom_tool": create_custom_tool,
    "list_custom_tools": list_custom_tools,
    "get_custom_tool": get_custom_tool,
    "delete_custom_tool": delete_custom_tool,
    "export_custom_tool": export_custom_tool,
    "import_custom_tool": import_custom_tool,
}

READONLY_TOOLS = {"list_custom_tools", "get_custom_tool", "export_custom_tool"}
INTERACTION_TOOLS = {"create_custom_tool", "delete_custom_tool", "import_custom_tool",
                     "run_custom_tool"}
