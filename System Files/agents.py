"""Ember agents — run modes + named, scoped, schedulable agent profiles.

Two related capabilities live here:

1. Run modes (like Claude Code's modes) — how Ember should behave on a task:
     chat       — converse / answer only; take no actions on the computer.
     plan       — investigate, propose a numbered plan, then STOP for approval.
     auto       — work the task end-to-end autonomously with the full toolset.
     read_only  — investigate only, using safe read-only tools.
   Each maps to a safety capability mode and contributes a directive to the
   system prompt so the behavior is real, not cosmetic.

2. Agent profiles (like Base44 Superagents) — named agents the user defines in
   natural language: a goal/instructions, a default run mode, a tool scope
   (permission control), an optional model, optional memory, and an optional
   schedule so they can run in the background on a timer. CRUD + scheduling +
   tool-scope resolution all live here; the live agent runtime consumes
   build_run_request() to launch a scoped run.

This module is pure/stdlib and persists to the shared Ember support dir
(EMBER_SUPPORT_DIR honored, so tests stay hermetic). The runtime wiring (system
prompt, sub-agent spawning, background ticker) lives in agent.py / ui.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _support_dir():
    from pathlib import Path
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        base = Path(local) / "Ember"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "Ember"
    base.mkdir(parents=True, exist_ok=True)
    return base


_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

RUN_MODES: dict[str, dict] = {
    "auto": {
        "label": "Autopilot",
        "capability": "full",
        "autonomous": True,
        "plan_first": False,
        "description": "Work the task end-to-end autonomously, using every tool "
                       "(asks only before genuinely risky actions).",
    },
    "plan": {
        "label": "Plan first",
        "capability": "restricted",
        "autonomous": False,
        "plan_first": True,
        "description": "Investigate, then propose a numbered step-by-step plan and "
                       "WAIT for the user's approval before taking any action.",
    },
    "chat": {
        "label": "Chat only",
        "capability": "read_only",
        "autonomous": False,
        "plan_first": False,
        "description": "Just talk: answer and advise. Do NOT control the computer "
                       "or run actions unless the user switches modes.",
    },
    "read_only": {
        "label": "Read-only",
        "capability": "read_only",
        "autonomous": False,
        "plan_first": False,
        "description": "Investigate only with safe, read-only tools; make no changes.",
    },
}

_DEFAULT_RUN_MODE = "auto"


def list_run_modes() -> dict:
    return {"ok": True, "modes": [{"id": k, **v} for k, v in RUN_MODES.items()],
            "current": get_run_mode()}


def _run_mode_path():
    return _support_dir() / "run_mode.json"


def get_run_mode() -> str:
    with _LOCK:
        try:
            p = _run_mode_path()
            if p.exists():
                m = json.loads(p.read_text("utf-8")).get("mode")
                if m in RUN_MODES:
                    return m
        except Exception:
            pass
        return _DEFAULT_RUN_MODE


def set_run_mode(mode: str) -> dict:
    """Persist the run mode and apply its safety capability. Returns the mode info."""
    mode = (mode or "").strip().lower()
    if mode not in RUN_MODES:
        return {"ok": False, "error": f"unknown run mode '{mode}'. "
                f"Choose one of: {', '.join(RUN_MODES)}"}
    with _LOCK:
        try:
            _run_mode_path().write_text(json.dumps({"mode": mode}), "utf-8")
        except Exception:
            pass
    applied = None
    try:
        import safety
        applied = safety.set_mode(RUN_MODES[mode]["capability"])
    except Exception:
        pass
    return {"ok": True, "mode": mode, **RUN_MODES[mode], "capability_applied": applied}


def run_mode_directive(mode: str | None = None) -> str:
    """A system-prompt snippet describing how to behave in the given run mode."""
    mode = mode or get_run_mode()
    info = RUN_MODES.get(mode)
    if not info:
        return ""
    lines = [f"\n# Run mode: {info['label']} ({mode})", info["description"]]
    if info.get("plan_first"):
        lines.append("Produce a concise numbered plan FIRST and stop; do not execute "
                     "steps until the user approves. Use pause_for_human to ask.")
    if mode in ("chat", "read_only"):
        lines.append("If the task truly needs actions, say so and ask the user to switch "
                     "to Autopilot or Plan mode rather than acting now.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Tool scoping (permission control)
# ---------------------------------------------------------------------------

# Category bundles -> substrings matched against tool names. Lets a profile say
# "this agent may only use the browser + web", etc.
CATEGORIES: dict[str, list[str]] = {
    "screen": ["take_screenshot", "capture_window", "zoom_screenshot", "click", "type_text",
               "paste_text", "press_key", "scroll", "move_mouse", "drag", "read_screen_text",
               "locate_text", "find_ui_elements", "mouse_position"],
    "browser": ["browser_", "open_url"],
    "files": ["read_file", "write_file", "list_directory", "search_files", "folder",
              "file_", "move_", "copy_", "rename", "unzip", "find_large_files",
              "find_duplicate_files", "grep_files", "diff_files", "disk_usage"],
    "web": ["http_", "web_search", "wikipedia_summary", "public_ip", "dns_", "download_file",
            "weather_lookup", "define_word", "currency_convert", "stock_quote"],
    "security": ["scan_", "security_", "fileless_", "quarantine", "check_url", "sandbox",
                 "run_full_scan", "download_guard"],
    "system": ["get_system_info", "get_running_processes", "get_performance",
               "list_open_ports", "system_health", "get_event_logs", "list_startup_items"],
    "comms": ["send_email", "http_post"],
}


def _expand_categories(all_names, categories) -> set:
    out = set()
    for cat in categories or []:
        for pat in CATEGORIES.get(cat, []):
            for n in all_names:
                if n == pat or n.startswith(pat) or pat in n:
                    out.add(n)
    return out


def filter_tools(all_names, scope: dict | None) -> list:
    """Resolve an agent's tool scope to a concrete allowed tool list.

    scope = {"mode": "all"|"read_only"|"custom", "categories": [...],
             "allow": [...], "deny": [...]}"""
    names = set(all_names or [])
    scope = scope or {"mode": "all"}
    mode = (scope.get("mode") or "all").lower()
    deny = set(scope.get("deny") or [])
    allow = set(scope.get("allow") or [])
    cats = scope.get("categories") or []

    if mode == "read_only":
        ro = _readonly_names() & names
        base = ro | (allow & names)
    elif mode == "custom":
        if cats or allow:
            base = _expand_categories(names, cats) | (allow & names)
        else:
            base = set(names)
    else:  # "all"
        base = set(names)
    return sorted(base - deny)


def _readonly_names() -> set:
    try:
        import safety
        return set(safety.SAFE_READONLY)
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Agent profile store
# ---------------------------------------------------------------------------

def _store_path():
    return _support_dir() / "agents.json"


def _load() -> dict:
    with _LOCK:
        try:
            p = _store_path()
            if p.exists():
                d = json.loads(p.read_text("utf-8"))
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        return {}


def _save(store: dict) -> None:
    with _LOCK:
        try:
            _store_path().write_text(json.dumps(store, indent=2), "utf-8")
        except Exception:
            pass


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")


def create_agent(name: str, instructions: str = "", description: str = "",
                 run_mode: str = "auto", tool_scope: dict | None = None,
                 model: str = "", schedule: dict | None = None,
                 memory_key: str = "", enabled: bool = True) -> dict:
    """Define (or overwrite) a named agent profile."""
    key = _slug(name)
    if not key:
        return {"ok": False, "error": "a non-empty agent name is required"}
    rm = (run_mode or "auto").lower()
    if rm not in RUN_MODES:
        return {"ok": False, "error": f"unknown run_mode '{run_mode}'"}
    if schedule and not _valid_schedule(schedule):
        return {"ok": False, "error": "schedule must be {'every_minutes': N} or {'daily_at': 'HH:MM'}"}
    now = time.time()
    with _LOCK:
        store = _load()
        prev = store.get(key) or {}
        profile = {
            "name": key,
            "display_name": name.strip(),
            "description": description.strip(),
            "instructions": instructions.strip(),
            "run_mode": rm,
            "tool_scope": tool_scope or {"mode": "all"},
            "model": (model or "").strip(),
            "schedule": schedule or None,
            "memory_key": (memory_key or key),
            "enabled": bool(enabled),
            "created_at": prev.get("created_at", now),
            "updated_at": now,
            "last_run": prev.get("last_run"),
        }
        store[key] = profile
        _save(store)
    return {"ok": True, "agent": profile}


def update_agent(name: str, **changes) -> dict:
    key = _slug(name)
    with _LOCK:
        store = _load()
        if key not in store:
            return {"ok": False, "error": f"no agent named '{name}'"}
        profile = store[key]
        allowed = {"display_name", "description", "instructions", "run_mode", "tool_scope",
                   "model", "schedule", "memory_key", "enabled"}
        for k, v in changes.items():
            if k not in allowed:
                continue
            if k == "run_mode" and v not in RUN_MODES:
                return {"ok": False, "error": f"unknown run_mode '{v}'"}
            if k == "schedule" and v and not _valid_schedule(v):
                return {"ok": False, "error": "invalid schedule"}
            profile[k] = v
        profile["updated_at"] = time.time()
        store[key] = profile
        _save(store)
    return {"ok": True, "agent": profile}


def delete_agent(name: str) -> dict:
    key = _slug(name)
    with _LOCK:
        store = _load()
        if key not in store:
            return {"ok": False, "error": f"no agent named '{name}'"}
        store.pop(key, None)
        _save(store)
    return {"ok": True, "deleted": key}


def get_agent(name: str) -> dict:
    key = _slug(name)
    store = _load()
    if key not in store:
        return {"ok": False, "error": f"no agent named '{name}'"}
    return {"ok": True, "agent": store[key]}


def list_agents() -> dict:
    store = _load()
    items = []
    for p in store.values():
        items.append({"name": p["name"], "display_name": p.get("display_name", p["name"]),
                      "description": p.get("description", ""), "run_mode": p.get("run_mode"),
                      "enabled": p.get("enabled", True), "schedule": p.get("schedule"),
                      "next_run": _next_run_iso(p)})
    items.sort(key=lambda x: x["name"])
    return {"ok": True, "count": len(items), "agents": items}


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _valid_schedule(schedule: dict) -> bool:
    if not isinstance(schedule, dict):
        return False
    if "every_minutes" in schedule:
        try:
            return int(schedule["every_minutes"]) > 0
        except Exception:
            return False
    if "daily_at" in schedule:
        return bool(re.match(r"^\d{1,2}:\d{2}$", str(schedule["daily_at"])))
    return False


def next_run_at(profile: dict, now: float | None = None) -> float | None:
    """Epoch seconds of the profile's next scheduled run, or None if it has no schedule."""
    now = time.time() if now is None else now
    sched = profile.get("schedule") or {}
    if not sched:
        return None
    last = profile.get("last_run")
    if "every_minutes" in sched:
        interval = int(sched["every_minutes"]) * 60
        if not last:
            return now            # never ran -> due now
        return last + interval
    if "daily_at" in sched:
        hh, mm = (int(x) for x in str(sched["daily_at"]).split(":"))
        dt = datetime.fromtimestamp(now)
        target = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target.timestamp() <= now:
            target = target + timedelta(days=1)
        # if it already ran today after the target time, the +1 day above is correct
        return target.timestamp()
    return None


def _next_run_iso(profile: dict):
    nr = next_run_at(profile)
    return datetime.fromtimestamp(nr).isoformat(timespec="minutes") if nr else None


def is_due(profile: dict, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if not profile.get("enabled", True) or not profile.get("schedule"):
        return False
    sched = profile["schedule"]
    last = profile.get("last_run")
    if "every_minutes" in sched:
        interval = int(sched["every_minutes"]) * 60
        return last is None or (now - last) >= interval
    if "daily_at" in sched:
        hh, mm = (int(x) for x in str(sched["daily_at"]).split(":"))
        dt = datetime.fromtimestamp(now)
        target = dt.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
        if now < target:
            return False
        # due if we haven't run since today's target time
        return last is None or last < target
    return False


def due_agents(now: float | None = None) -> list:
    now = time.time() if now is None else now
    return [p["name"] for p in _load().values() if is_due(p, now)]


def mark_ran(name: str, now: float | None = None) -> dict:
    key = _slug(name)
    now = time.time() if now is None else now
    with _LOCK:
        store = _load()
        if key not in store:
            return {"ok": False, "error": f"no agent named '{name}'"}
        store[key]["last_run"] = now
        _save(store)
    return {"ok": True, "name": key, "last_run": now}


# ---------------------------------------------------------------------------
# Build a concrete run request the runtime can execute
# ---------------------------------------------------------------------------

def build_run_request(name: str, task: str = "", all_tool_names=None) -> dict:
    """Assemble everything the agent runtime needs to launch a scoped run of a
    named agent: the composed instructions, run mode + capability, allowed tools
    and model. `task` (optional) is the concrete ask layered on the agent's goal."""
    g = get_agent(name)
    if not g.get("ok"):
        return g
    p = g["agent"]
    rm = p.get("run_mode", "auto")
    mode_info = RUN_MODES.get(rm, RUN_MODES["auto"])
    prompt_parts = []
    if p.get("instructions"):
        prompt_parts.append(p["instructions"])
    prompt_parts.append(run_mode_directive(rm).strip())
    if task:
        prompt_parts.append(f"\n# Task\n{task}")
    allowed = filter_tools(all_tool_names or [], p.get("tool_scope")) if all_tool_names else None
    return {
        "ok": True,
        "name": p["name"],
        "run_mode": rm,
        "capability": mode_info["capability"],
        "autonomous": mode_info["autonomous"],
        "instructions": "\n\n".join(x for x in prompt_parts if x).strip(),
        "model": p.get("model") or "",
        "memory_key": p.get("memory_key") or p["name"],
        "allowed_tools": allowed,
    }


# ---------------------------------------------------------------------------
# Tool-facing wrappers (thin, JSON-friendly)
# ---------------------------------------------------------------------------

def agent_create(name: str, instructions: str = "", description: str = "",
                 run_mode: str = "auto", tool_scope: dict | None = None,
                 model: str = "", schedule: dict | None = None) -> dict:
    return create_agent(name, instructions=instructions, description=description,
                        run_mode=run_mode, tool_scope=tool_scope, model=model,
                        schedule=schedule)


def agent_list() -> dict:
    return list_agents()


def agent_get(name: str) -> dict:
    return get_agent(name)


def agent_update(name: str, **changes) -> dict:
    return update_agent(name, **changes)


def agent_delete(name: str) -> dict:
    return delete_agent(name)


def agent_due() -> dict:
    return {"ok": True, "due": due_agents()}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {"name": "list_run_modes",
     "description": "List Ember's run modes (auto / plan / chat / read_only) and the "
                    "current one. Run modes control how autonomously Ember acts.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "set_run_mode",
     "description": "Set how Ember runs: 'auto' (autonomous, full tools), 'plan' "
                    "(propose a plan and wait for approval), 'chat' (talk only, no "
                    "actions), or 'read_only' (investigate with safe tools only).",
     "parameters": {"type": "OBJECT",
                    "properties": {"mode": {"type": "STRING"}}, "required": ["mode"]}},
    {"name": "agent_create",
     "description": "Create/define a named agent (a saved goal + run mode + tool scope, "
                    "optionally on a schedule) the user can run later. Like a Base44 "
                    "Superagent.",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"},
        "instructions": {"type": "STRING", "description": "the agent's goal / standing instructions"},
        "description": {"type": "STRING"},
        "run_mode": {"type": "STRING", "description": "auto | plan | chat | read_only"},
        "tool_scope": {"type": "OBJECT", "description": "permission scope, e.g. "
                       "{'mode':'custom','categories':['browser','web']} or {'mode':'read_only'}"},
        "model": {"type": "STRING"},
        "schedule": {"type": "OBJECT", "description": "optional, e.g. {'every_minutes':60} "
                     "or {'daily_at':'09:00'}"},
     }, "required": ["name"]}},
    {"name": "agent_list",
     "description": "List the user's saved agents (name, goal, run mode, schedule, next run).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "agent_get",
     "description": "Show one saved agent's full definition.",
     "parameters": {"type": "OBJECT",
                    "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "agent_delete",
     "description": "Delete a saved agent by name.",
     "parameters": {"type": "OBJECT",
                    "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "agent_run",
     "description": "Run a saved agent now on an optional concrete task. Ember launches "
                    "it as a scoped sub-agent using that agent's run mode + tool scope.",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"},
        "task": {"type": "STRING", "description": "the concrete ask for this run (optional)"},
     }, "required": ["name"]}},
]

# agent_run + spawn_agent are handled specially by the runtime (they launch a
# sub-agent), so they are NOT in TOOL_DISPATCH — agent.py routes them.
TOOL_DISPATCH = {
    "list_run_modes": list_run_modes,
    "set_run_mode": lambda mode: set_run_mode(mode),
    "agent_create": agent_create,
    "agent_list": agent_list,
    "agent_get": agent_get,
    "agent_delete": agent_delete,
}

READONLY_TOOLS = {"list_run_modes", "agent_list", "agent_get"}
INTERACTION_TOOLS = {"set_run_mode", "agent_create", "agent_delete", "agent_run"}
