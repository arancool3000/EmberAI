"""Ember extends itself — the AI can give itself new capabilities at runtime, and edit its own
source (in dev/source builds).

Two mechanisms:

1) RUNTIME TOOLS — works in the shipped/packaged app.
   create_python_tool(name, description, code) writes a small Python module into the user-data
   ``ai_tools/`` dir, VALIDATES it (py_compile + structural checks), imports it, and HOT-REGISTERS
   its function as a live agent tool — usable in the same conversation (the agent re-inits its
   chat so the model sees the new tool). This is the "it lacked a skill, wrote one, and used it"
   loop. Because the code lives in user-data (NOT inside the PyInstaller bundle) it survives
   restarts AND works in a frozen build. load_ai_tools() re-registers them at startup.

2) SOURCE EDIT — dev/source runs only.
   read_own_source / self_edit_source edit one of Ember's own .py files with a backup +
   py_compile validation; self_edit_undo restores it. In a frozen build the running app imports
   from the bundle, so a source edit can't take effect — the tool SAYS so instead of silently
   doing nothing, and points at create_python_tool instead.

Safety: create_python_tool and self_edit_source are classified HIGH in safety.py, so Ember shows
you the code and asks once before it runs (you approve at authoring; the created tool then runs
automatically — "approve first time only"). Code that fails py_compile/import is rejected and
never registered.

Standard library only. A registrar callback (set by agent.py via set_registrar) does the live
hot-registration, avoiding an agent<->self_extend circular import.
"""
from __future__ import annotations

import importlib.util
import json
import keyword
import os
import py_compile
import re
import sys
import time
from pathlib import Path

# Set by agent.py: registrar(declaration: dict, fn: callable, read_only: bool) -> None
# Hot-adds a tool to the LIVE agent so a just-authored tool is usable this session.
_REGISTRAR = None

# Names Ember already uses / must not be shadowed by an authored tool.
_RESERVED = {"create_python_tool", "self_edit_source", "self_edit_undo", "read_own_source",
             "list_ai_tools", "remove_ai_tool", "run_shell", "write_file"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
_HEADER = "# Ember AI-authored tool. Created by the assistant at the user's request.\n"


def set_registrar(fn) -> None:
    global _REGISTRAR
    _REGISTRAR = fn


# ---------------------------------------------------------------------------
# Storage (user-data — survives restarts, works in a frozen build)
# ---------------------------------------------------------------------------
def _support_dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        d = Path.home() / ".ember"
    override = os.environ.get("EMBER_SUPPORT_DIR")   # tests redirect here
    if override:
        d = Path(override)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tools_dir() -> Path:
    d = _support_dir() / "ai_tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return _tools_dir() / "_manifest.json"


def _load_manifest() -> dict:
    try:
        return json.loads(_manifest_path().read_text("utf-8")) or {}
    except Exception:
        return {}


def _save_manifest(m: dict) -> None:
    try:
        _manifest_path().write_text(json.dumps(m, indent=2), "utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Authored-tool validation + import
# ---------------------------------------------------------------------------
def _validate_name(name: str) -> str | None:
    if not name or not _NAME_RE.match(name):
        return ("tool name must be lower_snake_case, 3-40 chars, start with a letter "
                f"(got {name!r})")
    if keyword.iskeyword(name) or name in _RESERVED:
        return f"'{name}' is reserved — pick another name"
    return None


def _wrap(fn):
    """Wrap an authored function so a tool call always returns a JSON-able dict and never
    raises into the agent loop."""
    def _call(**kwargs):
        try:
            out = fn(**kwargs)
        except Exception as e:  # authored code must not crash the agent
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if isinstance(out, dict):
            out.setdefault("ok", True)
            return out
        return {"ok": True, "result": out}
    return _call


def _import_tool_module(name: str, path: Path):
    """Import ai_tools/<name>.py as a uniquely-named module and return it."""
    mod_name = f"ember_ai_tool_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry_fn(mod, name):
    """The tool's callable: a function named after the tool, or a `run` fallback."""
    fn = getattr(mod, name, None) or getattr(mod, "run", None)
    if not callable(fn):
        raise AttributeError(f"module must define a function named '{name}' (or 'run')")
    return fn


def _declaration(name: str, description: str, mod) -> dict:
    params = getattr(mod, "PARAMETERS", None)
    if not isinstance(params, dict) or params.get("type") != "OBJECT":
        # Permissive default: accept an optional free-form object.
        params = {"type": "OBJECT", "properties": {}, "required": []}
    return {"name": name,
            "description": (getattr(mod, "DESCRIPTION", None) or description
                            or f"AI-authored tool '{name}'")[:1024],
            "parameters": params}


def _register(name: str, description: str, path: Path) -> dict:
    """Import + hot-register an authored tool file. Returns a result dict."""
    try:
        mod = _import_tool_module(name, path)
        fn = _entry_fn(mod, name)
    except Exception as e:
        return {"ok": False, "error": f"tool didn't load: {e}"}
    decl = _declaration(name, description, mod)
    dispatch_fn = _wrap(fn)
    read_only = bool(getattr(mod, "READ_ONLY", False))
    if _REGISTRAR is not None:
        try:
            _REGISTRAR(decl, dispatch_fn, read_only)
            live = True
        except Exception as e:
            return {"ok": False, "error": f"couldn't register with the agent: {e}"}
    else:
        live = False   # no live agent (e.g. loaded at import) — startup merge will pick it up
    return {"ok": True, "live": live, "declaration": decl, "dispatch": dispatch_fn,
            "read_only": read_only}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def create_python_tool(name: str = "", description: str = "", code: str = "",
                       overwrite: bool = False) -> dict:
    """Give Ember a NEW capability at runtime by writing a Python tool. `code` must define a
    function named `name` (or `run`) that takes keyword args and returns a dict/JSON-able value;
    it may also set module-level DESCRIPTION and PARAMETERS (a Gemini schema). The tool is
    validated, saved to the user's ai_tools/ folder, and registered live so you can call it right
    away. Use this when you lack a capability the user is asking for."""
    name = (name or "").strip()
    err = _validate_name(name)
    if err:
        return {"ok": False, "error": err}
    if not code or not code.strip():
        return {"ok": False, "error": "no code provided"}
    path = _tools_dir() / f"{name}.py"
    if path.exists() and not overwrite:
        return {"ok": False, "error": f"a tool named '{name}' already exists; pass overwrite=true "
                                      "to replace it"}
    body = _HEADER + code if not code.startswith(_HEADER) else code
    # Write to a temp file first and py_compile it, so a broken tool never lands as a real file.
    tmp = _tools_dir() / f".{name}.tmp.py"
    try:
        tmp.write_text(body, "utf-8")
        py_compile.compile(str(tmp), doraise=True)
    except py_compile.PyCompileError as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return {"ok": False, "error": f"the tool has a syntax error: {e.msg if hasattr(e, 'msg') else e}"}
    except Exception as e:
        return {"ok": False, "error": f"couldn't validate the tool: {e}"}
    os.replace(str(tmp), str(path))
    reg = _register(name, description, path)
    if not reg.get("ok"):
        # Registration/import failed — don't leave a dead file behind.
        try:
            path.unlink()
        except Exception:
            pass
        return reg
    man = _load_manifest()
    man[name] = {"description": reg["declaration"]["description"], "created": int(time.time()),
                 "read_only": reg["read_only"]}
    _save_manifest(man)
    return {"ok": True, "name": name, "live": reg["live"],
            "message": (f"New capability '{name}' is ready" + (" and available now."
                        if reg["live"] else " (will load fully on the next message)."))}


def list_ai_tools() -> dict:
    """List the capabilities Ember has written for itself."""
    man = _load_manifest()
    tools = [{"name": k, "description": v.get("description", ""), "created": v.get("created")}
             for k, v in sorted(man.items())]
    # Also surface orphan files not in the manifest.
    for p in sorted(_tools_dir().glob("*.py")):
        if p.stem not in man and not p.name.startswith("_"):
            tools.append({"name": p.stem, "description": "(no manifest entry)", "created": None})
    return {"ok": True, "count": len(tools), "tools": tools}


def remove_ai_tool(name: str = "") -> dict:
    """Delete a capability Ember previously wrote for itself. Takes effect for new tool listings
    immediately; a tool already registered this session stays until restart."""
    name = (name or "").strip()
    path = _tools_dir() / f"{name}.py"
    if not path.exists():
        return {"ok": False, "error": f"no AI tool named '{name}'"}
    try:
        path.unlink()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    man = _load_manifest()
    man.pop(name, None)
    _save_manifest(man)
    return {"ok": True, "removed": name,
            "message": f"Removed '{name}'. Restart Ember to fully unload it from this session."}


def load_ai_tools() -> dict:
    """Import every saved ai_tools/*.py and return {declarations, dispatch, read_only_names} for
    the agent to merge at startup (mirrors plugin_system.load_plugins). A broken tool is skipped,
    never fatal."""
    decls, dispatch, read_only, errors = [], {}, set(), []
    for p in sorted(_tools_dir().glob("*.py")):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        name = p.stem
        if _validate_name(name):
            continue
        try:
            mod = _import_tool_module(name, p)
            fn = _entry_fn(mod, name)
        except Exception as e:
            errors.append({"name": name, "error": str(e)})
            continue
        man = _load_manifest().get(name, {})
        decls.append(_declaration(name, man.get("description", ""), mod))
        dispatch[name] = _wrap(fn)
        if bool(getattr(mod, "READ_ONLY", False)):
            read_only.add(name)
    return {"ok": True, "declarations": decls, "dispatch": dispatch,
            "read_only_names": read_only, "errors": errors}


# ---------------------------------------------------------------------------
# Source editing (dev/source builds)
# ---------------------------------------------------------------------------
def _source_root() -> Path:
    return Path(__file__).resolve().parent


def _resolve_source(path: str) -> Path | None:
    """Resolve `path` to a .py inside Ember's own source tree, or None if it escapes it."""
    root = _source_root()
    p = (root / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p


def read_own_source(path: str = "", max_bytes: int = 20000) -> dict:
    """Read one of Ember's own source files (relative to the Ember install dir) so you can see
    the code before proposing an edit. Read-only."""
    p = _resolve_source(path)
    if p is None:
        return {"ok": False, "error": "path must be inside Ember's own source folder"}
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"no such file: {path}"}
    try:
        data = p.read_text("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    truncated = len(data) > max_bytes
    return {"ok": True, "path": str(p), "truncated": truncated,
            "content": data[:max_bytes], "lines": data.count("\n") + 1}


def self_edit_source(path: str = "", find: str = "", replace: str = "",
                     new_content: str = "") -> dict:
    """Edit one of Ember's OWN source files. Either give `find`+`replace` (an exact substring
    swap that must match once) or `new_content` (full file). The original is backed up and the
    result is py_compile-validated; a broken edit is auto-reverted. NOTE: in a packaged build the
    running app imports from the bundle, so edits only take effect when Ember runs from source —
    the tool tells you which case you're in. Restart Ember to apply. Use self_edit_undo to revert."""
    p = _resolve_source(path)
    if p is None:
        return {"ok": False, "error": "path must be inside Ember's own source folder"}
    if not p.exists() and not new_content:
        return {"ok": False, "error": f"no such file: {path}"}
    if p.suffix != ".py":
        return {"ok": False, "error": "self-edit is limited to .py source files"}
    frozen = bool(getattr(sys, "frozen", False))

    try:
        original = p.read_text("utf-8") if p.exists() else ""
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if new_content:
        updated = new_content
    else:
        if not find:
            return {"ok": False, "error": "provide either find+replace or new_content"}
        n = original.count(find)
        if n == 0:
            return {"ok": False, "error": "the `find` text wasn't found in the file"}
        if n > 1:
            return {"ok": False, "error": f"`find` matched {n} times — make it unique"}
        updated = original.replace(find, replace)

    backup = p.with_suffix(p.suffix + ".ember.bak")
    try:
        p.write_text(updated, "utf-8")
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as e:
        try:
            p.write_text(original, "utf-8")   # auto-revert a broken edit; leave any backup intact
        except Exception:
            pass
        return {"ok": False, "reverted": True,
                "error": f"edit rejected — it broke Python syntax: {getattr(e, 'msg', e)}"}
    except Exception as e:
        try:
            p.write_text(original, "utf-8")
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    # Success: only NOW record the backup (of the pre-edit content), so a FAILED edit never
    # clobbers the backup and self_edit_undo always restores the last good state.
    try:
        backup.write_text(original, "utf-8")
    except Exception:
        pass

    msg = "Saved and syntax-checked. Restart Ember to apply."
    if frozen:
        msg = ("Saved to the source file, but THIS is a packaged build that runs from a bundle, "
               "so the change won't take effect here. Run Ember from source to self-edit — or use "
               "create_python_tool to add a runtime capability that works in this build.")
    return {"ok": True, "path": str(p), "backup": str(backup), "frozen": frozen, "message": msg}


def self_edit_undo(path: str = "") -> dict:
    """Revert the last self_edit_source on a file from its .ember.bak backup."""
    p = _resolve_source(path)
    if p is None:
        return {"ok": False, "error": "path must be inside Ember's own source folder"}
    backup = p.with_suffix(p.suffix + ".ember.bak")
    if not backup.exists():
        return {"ok": False, "error": "no backup to restore for that file"}
    try:
        p.write_text(backup.read_text("utf-8"), "utf-8")
        backup.unlink()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(p), "message": "Reverted. Restart Ember to apply."}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "create_python_tool",
        "description": ("Give Ember a NEW capability by writing a Python tool at runtime. `code` "
                        "defines a function named `name` (or `run`) taking keyword args and "
                        "returning a dict; optional module-level DESCRIPTION and PARAMETERS (a "
                        "Gemini OBJECT schema) describe it. Saved to the user's ai_tools folder "
                        "and registered live so you can call it immediately. Use it when you're "
                        "missing a capability the user wants (e.g. a new integration)."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "lower_snake_case tool name (3-40 chars)"},
                "description": {"type": "STRING", "description": "what the tool does"},
                "code": {"type": "STRING", "description": "the Python source defining the function"},
                "overwrite": {"type": "BOOLEAN", "description": "replace an existing tool of this name"},
            },
            "required": ["name", "code"],
        },
    },
    {
        "name": "list_ai_tools",
        "description": "List the capabilities Ember has written for itself.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "remove_ai_tool",
        "description": "Delete a capability Ember previously wrote for itself.",
        "parameters": {"type": "OBJECT",
                       "properties": {"name": {"type": "STRING", "description": "tool to remove"}},
                       "required": ["name"]},
    },
    {
        "name": "read_own_source",
        "description": ("Read one of Ember's OWN source files (path relative to the Ember install "
                        "dir) to inspect the code before proposing a self-edit. Read-only."),
        "parameters": {"type": "OBJECT",
                       "properties": {"path": {"type": "STRING", "description": "e.g. 'ui.py'"},
                                      "max_bytes": {"type": "INTEGER", "description": "cap (default 20000)"}},
                       "required": ["path"]},
    },
    {
        "name": "self_edit_source",
        "description": ("Edit one of Ember's OWN .py source files (find+replace, or full "
                        "new_content). Backed up + syntax-validated; auto-reverts a broken edit. "
                        "Only takes effect when Ember runs from source (not a packaged build); "
                        "restart to apply. For a capability that works in the shipped app, prefer "
                        "create_python_tool."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "source file, e.g. 'ember_browser.py'"},
                "find": {"type": "STRING", "description": "exact text to replace (must match once)"},
                "replace": {"type": "STRING", "description": "replacement text"},
                "new_content": {"type": "STRING", "description": "OR the full new file content"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "self_edit_undo",
        "description": "Revert the last self_edit_source on a file from its backup.",
        "parameters": {"type": "OBJECT",
                       "properties": {"path": {"type": "STRING", "description": "file to revert"}},
                       "required": ["path"]},
    },
]

TOOL_DISPATCH = {
    "create_python_tool": create_python_tool,
    "list_ai_tools": list_ai_tools,
    "remove_ai_tool": remove_ai_tool,
    "read_own_source": read_own_source,
    "self_edit_source": self_edit_source,
    "self_edit_undo": self_edit_undo,
}

READONLY_TOOLS = {"list_ai_tools", "read_own_source"}
INTERACTION_TOOLS = {"create_python_tool", "remove_ai_tool", "self_edit_source", "self_edit_undo"}
