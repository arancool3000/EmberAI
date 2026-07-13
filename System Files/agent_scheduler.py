"""Background agent scheduler — runs saved agents on their schedules.

Agent profiles (agents.py) can carry a schedule (`every_minutes` / `daily_at`).
This daemon ticks on a timer, asks agents.due_agents() what's due, and runs each
through a registered runner — the piece that actually launches the agent in the
live runtime (the UI wires a runner that spawns the agent and posts a notify()).
That completes the Base44-style "always-on agents that respond to schedules."

Mirrors the other guards: one daemon thread, a bounded event log behind a lock,
stdlib-only at import, and a `_RUNNER` injection point so tests run hermetically
without the LLM. If no runner is registered it does nothing (it never marks agents
as run), so once the app wires a runner the backlog simply starts flowing.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime

_TICK = 30.0            # seconds between schedule checks
_EVENTS_MAXLEN = 200

# Injection point: callable(name:str) -> dict (the result of running that agent).
_RUNNER = None

_LOCK = threading.Lock()
_thread: "threading.Thread | None" = None
_stop_event: "threading.Event | None" = None
_running = False
_events: "deque[dict]" = deque(maxlen=_EVENTS_MAXLEN)
_runs = 0


def set_runner(fn) -> None:
    """Register the callable that actually runs an agent by name."""
    global _RUNNER
    _RUNNER = fn


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record(name: str, result: dict) -> None:
    global _runs
    ok = bool(result.get("ok"))
    detail = result.get("summary") or result.get("error") or ("ran" if ok else "failed")
    with _LOCK:
        _events.append({"time": _now_iso(), "agent": name, "ok": ok,
                        "detail": str(detail)[:300]})
        _runs += 1


def run_due_now() -> dict:
    """Run every currently-due agent once (used by the daemon and the 'run now' tool)."""
    import agents
    runner = _RUNNER
    if runner is None:
        return {"ok": False, "error": "no agent runner registered", "ran": []}
    ran = []
    for name in agents.due_agents():
        try:
            result = runner(name)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        try:
            agents.mark_ran(name)
        except Exception:
            pass
        _record(name, result if isinstance(result, dict) else {"ok": True})
        ran.append(name)
    return {"ok": True, "ran": ran}


def _loop(stop: "threading.Event") -> None:
    while not stop.is_set():
        try:
            if _RUNNER is not None:
                run_due_now()
        except Exception:
            pass  # a hiccup must never kill the scheduler
        stop.wait(_TICK)


def scheduler_start() -> dict:
    """Start the background agent scheduler. Idempotent."""
    global _thread, _stop_event, _running
    try:
        with _LOCK:
            if _running and _thread is not None and _thread.is_alive():
                return {"ok": True, "running": True, "message": "scheduler already running"}
            _stop_event = threading.Event()
            stop = _stop_event
            _thread = threading.Thread(target=_loop, args=(stop,),
                                       name="ember-agent-scheduler", daemon=True)
            _running = True
            _thread.start()
        return {"ok": True, "running": True, "message": "agent scheduler started"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scheduler_stop() -> dict:
    global _thread, _stop_event, _running
    try:
        with _LOCK:
            running = _running
            stop = _stop_event
            thread = _thread
            _running = False
            _stop_event = None
            _thread = None
        if not running or thread is None:
            return {"ok": True, "message": "scheduler was not running"}
        if stop is not None:
            stop.set()
        thread.join(timeout=5.0)
        return {"ok": True, "message": "agent scheduler stopped"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scheduler_status() -> dict:
    try:
        import agents
        upcoming = agents.list_agents().get("agents", [])
        scheduled = [a for a in upcoming if a.get("schedule")]
    except Exception:
        scheduled = []
    with _LOCK:
        running = bool(_running and _thread is not None and _thread.is_alive())
        runs = _runs
        last = _events[-1] if _events else None
    return {"ok": True, "running": running, "has_runner": _RUNNER is not None,
            "total_runs": runs, "scheduled_agents": scheduled, "last_run": last}


def scheduler_events(limit: int = 20) -> dict:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    if n <= 0:
        n = 20
    with _LOCK:
        events = list(_events)[-n:]
    return {"ok": True, "events": events}


# Plain helpers for autostart
def start() -> dict:
    return scheduler_start()


def is_running() -> bool:
    with _LOCK:
        return bool(_running and _thread is not None and _thread.is_alive())


TOOL_DECLARATIONS = [
    {"name": "scheduler_status",
     "description": "Report the background agent scheduler: running?, scheduled agents, "
                    "total runs, and the last run.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "scheduler_events",
     "description": "List recent scheduled agent runs (agent, ok, detail).",
     "parameters": {"type": "OBJECT",
                    "properties": {"limit": {"type": "INTEGER"}}, "required": []}},
    {"name": "scheduler_run_due",
     "description": "Run every agent that is currently due right now (don't wait for the timer).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "scheduler_start",
     "description": "Start the background agent scheduler (runs saved agents on schedule).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "scheduler_stop",
     "description": "Stop the background agent scheduler.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "scheduler_status": scheduler_status,
    "scheduler_events": scheduler_events,
    "scheduler_run_due": run_due_now,
    "scheduler_start": scheduler_start,
    "scheduler_stop": scheduler_stop,
}

READONLY_TOOLS = {"scheduler_status", "scheduler_events"}
INTERACTION_TOOLS = {"scheduler_run_due", "scheduler_start", "scheduler_stop"}
