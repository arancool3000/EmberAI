"""Ember plugin system.

Drop a ``*.py`` file in the ``plugins/`` folder next to this module and its tools
auto-register the next time the app loads (or you call the ``reload_plugins`` tool).
No core edits required.

A plugin module declares a module-level list ``EMBER_TOOLS`` — see ``plugins/README.md``
for the full contract. Each entry is a dict:

    EMBER_TOOLS = [
        {
            "name": "hello_plugin",                 # globally-unique snake_case
            "description": "Say hello from a plugin.",
            "parameters": {"type": "OBJECT",
                "properties": {"who": {"type": "STRING"}}, "required": []},
            "handler": hello_plugin,                # callable(**kwargs) -> {"ok": ...}
            "read_only": True,                      # optional; default False (medium-risk)
        },
    ]

This file is loaded by the host (agent.py): at startup the integrator calls
``load_plugins()`` and merges the returned ``declarations`` into ``TOOL_DECLARATIONS``,
``dispatch`` into ``TOOL_DISPATCH`` and ``read_only_names`` into the safety classifier's
read-only set. The three management tools below (list/reload/create) are themselves
exported via this module's own ``TOOL_DECLARATIONS`` / ``TOOL_DISPATCH`` for wiring.
"""
from __future__ import annotations

import importlib.util
import re
import traceback
from pathlib import Path
from typing import Any, Callable

# Directory scanned for plugin files. It lives in the stable support directory so an app
# upgrade cannot erase user-authored plugins.
# Tests monkeypatch this to a temporary directory.
from app_data import data_dir as _support_dir

PLUGINS_DIR: Path = _support_dir() / "plugins"

# Cache of the most recent load_plugins() summary so the management tools can report it.
_LAST_LOAD: dict | None = None


def _unique_module_name(path: Path) -> str:
    """A stable, collision-resistant module name for a plugin file."""
    stem = re.sub(r"[^0-9A-Za-z_]", "_", path.stem)
    return f"ember_plugin__{stem}"


def _plugin_files(directory: Path) -> list[Path]:
    """Plugin *.py files in ``directory`` (skip __init__.py and files starting with '_')."""
    try:
        files = sorted(directory.glob("*.py"))
    except Exception:
        return []
    out = []
    for f in files:
        if f.name == "__init__.py":
            continue
        if f.name.startswith("_"):
            continue
        out.append(f)
    return out


def _import_plugin(path: Path):
    """Import a single plugin file in isolation. Raises on failure (caller catches)."""
    mod_name = _unique_module_name(path)
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not create import spec for {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # may raise — caller captures
    return module


def _validate_tool(entry: Any) -> tuple[bool, str]:
    """Return (ok, reason). A valid tool has name/description/parameters and a callable handler."""
    if not isinstance(entry, dict):
        return False, "tool entry is not a dict"
    for key in ("name", "description", "parameters", "handler"):
        if key not in entry:
            return False, f"missing required key '{key}'"
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "name must be a non-empty string"
    if not callable(entry.get("handler")):
        return False, f"handler for '{name}' is not callable"
    if not isinstance(entry.get("parameters"), dict):
        return False, f"parameters for '{name}' must be a dict"
    return True, ""


def load_plugins() -> dict:
    """Scan PLUGINS_DIR and collect every valid plugin tool.

    Robust by design: a broken or exception-raising plugin is SKIPPED, its error
    captured, and the loader never crashes. Invalid tool entries are skipped.
    Duplicate tool names (across plugins) keep the first and skip later ones.

    Returns a summary dict::

        {
          "ok": True,
          "declarations": [ {"name","description","parameters"}, ... ],  # no handler/read_only
          "dispatch": {name: handler, ...},
          "read_only_names": {name, ...},
          "loaded": [ {"name","plugin": filename}, ... ],
          "errors": [ {"plugin": filename, "error": str}, ... ],
        }

    The summary is also cached in the module global ``_LAST_LOAD``.
    """
    global _LAST_LOAD

    declarations: list[dict] = []
    dispatch: dict[str, Callable[..., dict]] = {}
    read_only_names: set[str] = set()
    loaded: list[dict] = []
    errors: list[dict] = []
    seen_names: set[str] = set()

    directory = Path(PLUGINS_DIR)

    for path in _plugin_files(directory):
        fname = path.name
        try:
            module = _import_plugin(path)
        except Exception as e:
            errors.append({"plugin": fname, "error": f"{type(e).__name__}: {e}",
                           "trace": traceback.format_exc(limit=4)})
            continue

        tools = getattr(module, "EMBER_TOOLS", None)
        if tools is None:
            errors.append({"plugin": fname, "error": "no module-level EMBER_TOOLS list"})
            continue
        if not isinstance(tools, (list, tuple)):
            errors.append({"plugin": fname, "error": "EMBER_TOOLS must be a list"})
            continue

        for entry in tools:
            ok, reason = _validate_tool(entry)
            if not ok:
                errors.append({"plugin": fname, "error": f"invalid tool: {reason}"})
                continue
            name = entry["name"]
            if name in seen_names:
                errors.append({"plugin": fname,
                               "error": f"duplicate tool name '{name}' — skipped"})
                continue

            seen_names.add(name)
            declarations.append({
                "name": name,
                "description": entry["description"],
                "parameters": entry["parameters"],
            })
            dispatch[name] = entry["handler"]
            if entry.get("read_only"):
                read_only_names.add(name)
            loaded.append({"name": name, "plugin": fname})

    summary = {
        "ok": True,
        "declarations": declarations,
        "dispatch": dispatch,
        "read_only_names": read_only_names,
        "loaded": loaded,
        "errors": errors,
    }
    _LAST_LOAD = summary
    return summary


# ---------------------------------------------------------------------------
# Management tools exposed to the LLM
# ---------------------------------------------------------------------------

def list_plugins() -> dict:
    """List plugin files and the tools each one contributes (fresh scan)."""
    summary = load_plugins()
    by_file: dict[str, list[str]] = {}
    for item in summary["loaded"]:
        by_file.setdefault(item["plugin"], []).append(item["name"])
    # include files that loaded zero tools (e.g. errored) so the user can see them
    for path in _plugin_files(Path(PLUGINS_DIR)):
        by_file.setdefault(path.name, [])
    plugins = [{"file": f, "tools": sorted(by_file[f])} for f in sorted(by_file)]
    return {
        "ok": True,
        "plugins": plugins,
        "tool_count": len(summary["dispatch"]),
        "errors": summary["errors"],
    }


def reload_plugins() -> dict:
    """Re-scan and reload all plugins.

    NOTE: this refreshes plugins.py's own view (``_LAST_LOAD``) and returns the new
    tool set, but a FULL runtime effect requires the host to re-merge the returned
    declarations/dispatch/read_only_names. The integrator merges at startup; runtime
    reloads report the new set here.
    """
    summary = load_plugins()
    return {
        "ok": True,
        "loaded": len(summary["loaded"]),
        "tools": [item["name"] for item in summary["loaded"]],
        "errors": summary["errors"],
    }


_TEMPLATE = '''\
"""Ember plugin: {slug}

Drop this file in the plugins/ folder. Tools auto-register on app start
(or call the reload_plugins tool). See plugins/README.md for the full contract.
"""


def {slug}_demo(text: str = "") -> dict:
    """Echo the input back. Replace with your own logic."""
    return {{"ok": True, "echo": text, "from": "{slug}"}}


EMBER_TOOLS = [
    {{
        "name": "{slug}_demo",
        "description": "Demo tool from the {slug} plugin: echoes its input back.",
        "parameters": {{
            "type": "OBJECT",
            "properties": {{
                "text": {{"type": "STRING", "description": "anything to echo back"}},
            }},
            "required": [],
        }},
        "handler": {slug}_demo,
        "read_only": True,
    }},
]
'''


def _slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-z]+", "_", (name or "").strip().lower()).strip("_")
    return s


def create_plugin_template(name: str) -> dict:
    """Write a minimal, valid starter plugin file so a user can begin fast.

    Refuses if a file with that slug already exists.
    """
    slug = _slugify(name)
    if not slug:
        return {"ok": False, "error": "provide a name with at least one letter/number"}
    if slug.startswith("_") or slug == "__init__":
        return {"ok": False, "error": "name cannot start with '_'"}

    directory = Path(PLUGINS_DIR)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"cannot create plugins dir: {e}"}

    path = directory / f"{slug}.py"
    if path.exists():
        return {"ok": False, "error": f"plugin file already exists: {path.name}"}

    try:
        path.write_text(_TEMPLATE.format(slug=slug), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"failed to write file: {e}"}

    return {
        "ok": True,
        "path": str(path),
        "message": (f"Created plugin template '{path.name}' with tool '{slug}_demo'. "
                    f"Edit it, then run reload_plugins (or restart Ember) to register it."),
    }


# ---------------------------------------------------------------------------
# Exports for host wiring (agent.py / safety.py)
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS: list[dict] = [
    {
        "name": "list_plugins",
        "description": (
            "List installed Ember plugins (files in the plugins/ folder) and the tools each "
            "one contributes. Read-only; also reports any plugins that failed to load."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "reload_plugins",
        "description": (
            "Re-scan the plugins/ folder and reload every plugin's tools. Use after adding or "
            "editing a plugin file. Returns the freshly loaded tool names and any load errors."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "create_plugin_template",
        "description": (
            "Create a starter plugin file in the plugins/ folder so the user can build a new "
            "tool fast. Writes a minimal valid plugin named '<slug>_demo' that echoes its input. "
            "Refuses if a plugin with that name already exists."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "name for the new plugin (turned into a file slug)"},
            },
            "required": ["name"],
        },
    },
]

TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "list_plugins": list_plugins,
    "reload_plugins": reload_plugins,
    "create_plugin_template": create_plugin_template,
}

# Risk classification for the management tools themselves.
READONLY_TOOLS = {"list_plugins"}
INTERACTION_TOOLS = {"reload_plugins", "create_plugin_template"}
