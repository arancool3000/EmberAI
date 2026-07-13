"""Usage dashboard: track model API calls + tokens vs the free-tier limits so the user
can see headroom. State persists across sessions in usage.json (atomic write under a lock)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

# record_call() runs from the agent's model-request path, which can fire from multiple
# threads. Guard the read-modify-write so concurrent records can't corrupt the counts.
# Reentrant so a locked helper can call another freely. Atomic _save (below) additionally
# prevents a torn file from any writer.
_LOCK = threading.RLock()

LIMIT_PER_MINUTE = 15
LIMIT_PER_DAY = 500

_MAX_DAYS = 30          # prune day entries older than this many days
_MINUTE_WINDOW_SEC = 60  # rolling window for the per-minute limit


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


USAGE_FILE = _data_dir() / "usage.json"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _empty() -> dict:
    return {"days": {}, "minute_window": []}


def _load() -> dict:
    try:
        if USAGE_FILE.exists():
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("days", {})
                data.setdefault("minute_window", [])
                if not isinstance(data["days"], dict):
                    data["days"] = {}
                if not isinstance(data["minute_window"], list):
                    data["minute_window"] = []
                return data
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return _empty()


def _save(data: dict):
    """Atomic write: serialize to a temp file then os.replace() so a crash or a concurrent
    writer can never leave a half-written usage.json (which _load would treat as corrupt
    and silently reset to empty)."""
    try:
        tmp = USAGE_FILE.with_name(USAGE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, USAGE_FILE)
    except OSError:
        pass


def _prune(data: dict, now: float) -> dict:
    """Drop day entries older than _MAX_DAYS and minute_window timestamps older than 60s.
    Keeps usage.json from growing without bound. Called on every write."""
    cutoff = now - _MINUTE_WINDOW_SEC
    window = data.get("minute_window", [])
    data["minute_window"] = [t for t in window if isinstance(t, (int, float)) and t >= cutoff]

    days = data.get("days", {})
    if days:
        # Keep only the most recent _MAX_DAYS date keys (lexicographic sort works for ISO dates).
        keep = sorted(days.keys())[-_MAX_DAYS:]
        data["days"] = {k: days[k] for k in keep}
    return data


def _day_entry(data: dict, date: str) -> dict:
    day = data["days"].setdefault(date, {})
    day.setdefault("calls", 0)
    day.setdefault("tokens", 0)
    day.setdefault("by_model", {})
    if not isinstance(day["by_model"], dict):
        day["by_model"] = {}
    return day


def record_call(model: str = "", prompt_tokens: int = 0, output_tokens: int = 0) -> None:
    """Record one model API call. Cheap and never raises — safe to call on every request.
    Increments today's calls + tokens + by_model[model] and appends now to minute_window."""
    try:
        with _LOCK:
            now = time.time()
            data = _load()
            day = _day_entry(data, _today())
            day["calls"] = int(day.get("calls", 0)) + 1
            try:
                tokens = int(prompt_tokens or 0) + int(output_tokens or 0)
            except (TypeError, ValueError):
                tokens = 0
            day["tokens"] = int(day.get("tokens", 0)) + tokens
            key = str(model or "unknown")
            day["by_model"][key] = int(day["by_model"].get(key, 0)) + 1
            data["minute_window"].append(now)
            _prune(data, now)
            _save(data)
    except Exception:
        # Swallow everything: recording usage must never break a model request.
        pass


def summary() -> dict:
    """Snapshot of current usage vs the free-tier limits, for the UI dashboard."""
    try:
        with _LOCK:
            now = time.time()
            data = _prune(_load(), now)
            today = _today()
            day = _day_entry(data, today)

            cutoff = now - _MINUTE_WINDOW_SEC
            calls_last_minute = sum(1 for t in data["minute_window"]
                                    if isinstance(t, (int, float)) and t >= cutoff)
            calls_today = int(day.get("calls", 0))
            tokens_today = int(day.get("tokens", 0))
            by_model = dict(day.get("by_model", {}))

            minute_pct = round(min(100.0, calls_last_minute / LIMIT_PER_MINUTE * 100.0), 1)
            day_pct = round(min(100.0, calls_today / LIMIT_PER_DAY * 100.0), 1)

            # Last 7 days, oldest first, filling any missing days with zeros.
            last_7 = []
            for i in range(6, -1, -1):
                d = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
                entry = data["days"].get(d, {})
                last_7.append({
                    "date": d,
                    "calls": int(entry.get("calls", 0)) if isinstance(entry, dict) else 0,
                    "tokens": int(entry.get("tokens", 0)) if isinstance(entry, dict) else 0,
                })

            return {
                "ok": True,
                "date": today,
                "calls_last_minute": calls_last_minute,
                "calls_today": calls_today,
                "tokens_today": tokens_today,
                "limit_per_minute": LIMIT_PER_MINUTE,
                "limit_per_day": LIMIT_PER_DAY,
                "minute_pct": minute_pct,
                "day_pct": day_pct,
                "minute_remaining": max(0, LIMIT_PER_MINUTE - calls_last_minute),
                "day_remaining": max(0, LIMIT_PER_DAY - calls_today),
                "by_model": by_model,
                "last_7_days": last_7,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Tools (top-level, never raise, return {"ok": ...}) -----------------------------------

def usage_summary() -> dict:
    """Show Ember's API usage vs the free-tier limits."""
    return summary()


def usage_reset() -> dict:
    """Clear all usage counters (calls, tokens, history)."""
    try:
        with _LOCK:
            _save(_empty())
        return {"ok": True, "message": "Usage counters cleared."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOL_DECLARATIONS = [
    {"name": "usage_summary",
     "description": "Show Ember's API usage: calls this minute/today and tokens vs the free-tier limits (15/min, 500/day).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "usage_reset",
     "description": "Reset Ember's API usage counters (calls, tokens, and history) back to zero.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {"usage_summary": usage_summary, "usage_reset": usage_reset}

READONLY_TOOLS = {"usage_summary"}
INTERACTION_TOOLS = {"usage_reset"}
