"""Persistent memory: facts the AI learns about the user's system and preferences."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# Read-modify-write of memory.json happens from the agent's parallel read-only tool pool
# (up to 6 worker threads via log_action), so guard it. Reentrant so a locked RMW can call
# helpers freely. Atomic _save (below) additionally prevents a torn file from any writer.
_LOCK = threading.RLock()
_MAX_FACTS = 500


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _data_dir() -> Path:
    """Writable runtime dir. In a frozen app, never write inside the bundle (it breaks the
    code signature -> slow relaunch / read-only /Applications); use the OS user-data dir."""
    if not getattr(sys, "frozen", False):
        return _base_dir()
    home = Path.home()
    if sys.platform == "darwin":
        d = home / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = home / "AppData" / "Roaming" / "Ember"
    else:
        d = home / ".ember"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


MEMORY_PATH = _data_dir() / "memory.json"


def _load() -> dict:
    if MEMORY_PATH.exists():
        try:
            return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"facts": {}, "actions_log": []}


def _save(data: dict):
    """Atomic write: serialize to a temp file then os.replace() so a crash or a concurrent
    writer can never leave a half-written memory.json (which _load would treat as corrupt
    and silently reset to empty — wiping every saved fact)."""
    try:
        tmp = MEMORY_PATH.with_name(MEMORY_PATH.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, MEMORY_PATH)
    except OSError:
        pass


def remember(key: str, value: str, category: str = "general") -> dict:
    if not key or not value:
        return {"ok": False, "error": "key and value required"}
    with _LOCK:
        data = _load()
        data["facts"][key] = {
            "value": value,
            "category": category,
            # Sub-second resolution so facts saved within the same second still order
            # correctly for newest-first summary + oldest-eviction.
            "saved_at": time.time(),
        }
        # Cap total facts — evict the oldest by saved_at so the file (re-read on every
        # logged action) doesn't grow without bound.
        facts = data["facts"]
        if len(facts) > _MAX_FACTS:
            for old in sorted(facts, key=lambda k: facts[k].get("saved_at", 0)
                              if isinstance(facts[k], dict) else 0)[:len(facts) - _MAX_FACTS]:
                del facts[old]
        _save(data)
    return {"ok": True, "remembered": key, "value": value}


def recall(query: str | None = None) -> dict:
    data = _load()
    facts = data.get("facts", {})
    if not query:
        return {"ok": True, "facts": facts}
    q = query.lower()
    matched = {k: v for k, v in facts.items() if q in k.lower() or q in str(v).lower()}
    return {"ok": True, "facts": matched, "matched_count": len(matched)}


def forget(key: str) -> dict:
    with _LOCK:
        data = _load()
        if key in data.get("facts", {}):
            del data["facts"][key]
            _save(data)
            return {"ok": True, "forgot": key}
    return {"ok": False, "error": "no such fact"}


def forget_all() -> dict:
    """Clear every saved fact (keeps the action log). Locked + atomic."""
    with _LOCK:
        data = _load()
        count = len(data.get("facts", {}))
        data["facts"] = {}
        _save(data)
    return {"ok": True, "forgot_count": count}


def get_facts_summary(max_facts: int = 30) -> str:
    """Returns a compact text summary for injection into the system prompt.
    Newest facts first, so a busy session's recent facts aren't dropped past the cap."""
    data = _load()
    facts = data.get("facts", {})
    if not facts:
        return ""
    ordered = sorted(facts.items(),
                     key=lambda kv: kv[1].get("saved_at", 0) if isinstance(kv[1], dict) else 0,
                     reverse=True)
    lines = []
    for k, v in ordered[:max_facts]:
        val = v.get("value", "") if isinstance(v, dict) else str(v)
        lines.append(f"- {k}: {val}")
    return "\n".join(lines)


def log_action(name: str, args: dict, result_summary: str):
    """Append a brief action record so the AI can recall what it just did."""
    with _LOCK:
        data = _load()
        log = data.setdefault("actions_log", [])
        log.append({
            "t": int(time.time()),
            "name": name,
            "args": {k: (str(v)[:120] if not isinstance(v, (int, float, bool)) else v) for k, v in (args or {}).items()},
            "result": result_summary[:200],
        })
        data["actions_log"] = log[-50:]
        _save(data)


def get_recent_actions(n: int = 10) -> list:
    data = _load()
    return data.get("actions_log", [])[-n:]
