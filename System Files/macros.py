"""Saved task macros — store a natural-language workflow once, replay it any time.

A macro is just a named task description. `run_macro` returns the task so the agent
re-executes it; macros can also be scheduled via the existing scheduled-tasks system.
Persisted per-user so they survive re-clones / rebuilds.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _dir() -> Path:
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        d = Path.home() / ".ember"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return _dir() / "macros.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text("utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _path().write_text(json.dumps(d, indent=2), "utf-8")
    except Exception:
        pass


def save_macro(name: str, task: str) -> dict:
    """Save a reusable task under a name (e.g. 'morning' -> 'organize Downloads and summarize unread tabs')."""
    name = (name or "").strip()
    if not name or not (task or "").strip():
        return {"ok": False, "error": "both a name and a task are required"}
    d = _load()
    d[name] = {"task": task.strip(), "created": int(time.time())}
    _save(d)
    return {"ok": True, "saved": name}


def list_macros() -> dict:
    """List saved macros."""
    d = _load()
    return {"ok": True, "count": len(d),
            "macros": [{"name": k, "task": v.get("task", "")[:140]} for k, v in d.items()]}


def get_macro(name: str) -> dict:
    """Get a saved macro's full task text."""
    m = _load().get((name or "").strip())
    return {"ok": True, "name": name, "task": m["task"]} if m else {"ok": False, "error": f"no macro '{name}'"}


def run_macro(name: str) -> dict:
    """Return a saved macro's task so it can be executed now. The agent should carry out
    the returned 'task' as if the user had just asked for it."""
    m = _load().get((name or "").strip())
    if not m:
        return {"ok": False, "error": f"no macro '{name}'"}
    return {"ok": True, "name": name, "task": m["task"], "action": "execute_this_task"}


def delete_macro(name: str) -> dict:
    """Delete a saved macro."""
    d = _load()
    if (name or "").strip() in d:
        d.pop(name.strip())
        _save(d)
        return {"ok": True, "deleted": name}
    return {"ok": False, "error": f"no macro '{name}'"}
