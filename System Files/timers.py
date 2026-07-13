"""Countdown timers for Ember — "set a 10 minute timer", "remind me in 90 seconds".

In-process timers (threading.Timer) that fire a desktop notification + alert sound when they
elapse, and call a registered UI callback so Ember can also pop a chat bubble / speak. Lives
alongside scheduled_tasks.py (which schedules persistent shell commands at a wall-clock time);
this module is the lightweight, conversational "kitchen timer" people expect from an assistant.

Pure/testable: the duration parser and the timer registry need no GUI and no native deps. The
notification + sound are best-effort with lazy imports, so the module imports with only stdlib.

Every tool returns {"ok": True, ...} or {"ok": False, "error": "..."} — never raises.
"""
from __future__ import annotations

import re
import threading
import time

# ---- module state (guarded by _lock) ----------------------------------------
_lock = threading.Lock()
_timers: dict[str, dict] = {}     # id -> {id,label,duration,created,due,status,_timer,...}
_seq = 0
_fire_callback = None             # set by the UI to surface a fired timer (bubble/sound/voice)

_MAX_DURATION = 24 * 60 * 60      # cap a single timer at 24h so a typo can't hang forever
_MAX_ACTIVE = 50                  # sanity cap on concurrent timers


def set_fire_callback(fn) -> None:
    """Register (or clear with None) a callback invoked when any timer elapses. It receives a
    dict {id,label,message,seconds} and is called from a background thread — the UI should
    marshal to its main thread (e.g. via a Qt signal)."""
    global _fire_callback
    _fire_callback = fn


# ---- duration parsing (pure) -------------------------------------------------
_UNIT_SECONDS = {
    "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}
_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def parse_duration(text) -> float | None:
    """Parse a human duration into seconds. Accepts '5m', '90s', '1h30m', '2 minutes',
    'an hour'-style is NOT supported, but a bare number is treated as SECONDS. Returns the
    number of seconds (float) or None if nothing usable was found."""
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    s = (text or "").strip().lower()
    if not s:
        return None
    total = 0.0
    found = False
    for num, unit in _PART_RE.findall(s):
        mult = _UNIT_SECONDS.get(unit)
        if mult is None:
            # tolerate the long forms we didn't enumerate, e.g. "minutes." with trailing punct
            unit = unit.rstrip(".")
            mult = _UNIT_SECONDS.get(unit)
        if mult is None:
            continue
        total += float(num) * mult
        found = True
    if not found:
        # A bare number with no unit -> seconds (e.g. "30").
        m = re.fullmatch(r"\d+(?:\.\d+)?", s)
        if m:
            total = float(s)
            found = total > 0
    return total if (found and total > 0) else None


def human_duration(seconds: float) -> str:
    """Render seconds as a compact human string, e.g. 3690 -> '1h 1m 30s'."""
    seconds = int(round(max(0, seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


# ---- firing ------------------------------------------------------------------
def _play_alert() -> None:
    """Best-effort short alert sound when a timer elapses."""
    import subprocess
    import sys
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep(getattr(winsound, "MB_ICONASTERISK", 0x40))
        else:
            # Linux: try paplay/aplay if present, else a terminal bell.
            for player, snd in (("paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"),):
                try:
                    subprocess.Popen([player, snd], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
                except Exception:
                    pass
            print("\a", end="", flush=True)
    except Exception:
        pass


def _fire(tid: str) -> None:
    with _lock:
        t = _timers.get(tid)
        if not t or t.get("status") != "running":
            return
        t["status"] = "fired"
        t["fired_at"] = time.time()
        label = t.get("label") or "Timer"
        seconds = t.get("duration", 0)
        cb = _fire_callback
    message = f"{label} — time's up! ({human_duration(seconds)})"
    try:
        import extra_tools
        extra_tools.show_notification("⏰ Ember Timer", message)
    except Exception:
        pass
    _play_alert()
    if cb:
        try:
            cb({"id": tid, "label": label, "message": message, "seconds": seconds})
        except Exception:
            pass


# ---- tools -------------------------------------------------------------------
def set_timer(duration, label: str = "") -> dict:
    """Start a countdown timer. `duration` is a human string like '5m', '90s', '1h30m', or a
    bare number of seconds; `label` is an optional name. When it elapses Ember shows a desktop
    notification, plays an alert, and tells you in chat. Returns the timer id + due time."""
    global _seq
    seconds = parse_duration(duration)
    if seconds is None:
        return {"ok": False, "error": "couldn't read that duration — try e.g. '5m', '90s', or '1h30m'"}
    if seconds > _MAX_DURATION:
        return {"ok": False, "error": f"timer too long (max {human_duration(_MAX_DURATION)})"}

    label = (label or "").strip()
    with _lock:
        active = sum(1 for t in _timers.values() if t.get("status") == "running")
        if active >= _MAX_ACTIVE:
            return {"ok": False, "error": f"too many active timers (max {_MAX_ACTIVE})"}
        _seq += 1
        tid = f"timer-{_seq}"
        now = time.time()
        timer = threading.Timer(seconds, _fire, args=(tid,))
        timer.daemon = True
        rec = {
            "id": tid,
            "label": label or f"Timer {_seq}",
            "duration": seconds,
            "created": now,
            "due": now + seconds,
            "status": "running",
            "_timer": timer,
        }
        _timers[tid] = rec
        timer.start()
    return {"ok": True, "id": tid, "label": rec["label"],
            "duration_seconds": seconds, "duration_text": human_duration(seconds),
            "fires_in": human_duration(seconds),
            "message": f"Timer '{rec['label']}' set for {human_duration(seconds)}."}


def list_timers() -> dict:
    """List active (and recently fired) timers with the time remaining."""
    now = time.time()
    out = []
    with _lock:
        for t in _timers.values():
            remaining = max(0.0, t["due"] - now) if t["status"] == "running" else 0.0
            out.append({
                "id": t["id"],
                "label": t.get("label", ""),
                "status": t["status"],
                "duration_text": human_duration(t.get("duration", 0)),
                "remaining_seconds": int(round(remaining)),
                "remaining_text": human_duration(remaining) if t["status"] == "running" else "—",
            })
    active = [t for t in out if t["status"] == "running"]
    return {"ok": True, "count": len(active), "active": len(active), "timers": out}


def cancel_timer(timer_id: str = "") -> dict:
    """Cancel a running timer by id (from list_timers), or pass 'all' to cancel every timer."""
    target = (timer_id or "").strip()
    if not target:
        return {"ok": False, "error": "a timer id is required (or 'all')"}
    cancelled = []
    with _lock:
        if target.lower() == "all":
            ids = list(_timers)
        else:
            ids = [target] if target in _timers else []
        if not ids:
            return {"ok": False, "error": f"no timer '{target}'"}
        for tid in ids:
            t = _timers.get(tid)
            if not t:
                continue
            tm = t.get("_timer")
            if tm is not None:
                try:
                    tm.cancel()
                except Exception:
                    pass
            if t.get("status") == "running":
                t["status"] = "cancelled"
                cancelled.append(tid)
    return {"ok": True, "cancelled": cancelled, "count": len(cancelled)}


def clear_all() -> None:
    """Cancel and forget all timers (used by tests + on shutdown)."""
    with _lock:
        for t in _timers.values():
            tm = t.get("_timer")
            if tm is not None:
                try:
                    tm.cancel()
                except Exception:
                    pass
        _timers.clear()


# ---- exports for wiring ------------------------------------------------------
TOOL_DECLARATIONS = [
    {"name": "set_timer",
     "description": "Start a countdown timer (a kitchen-style timer). When it elapses Ember "
                    "shows a desktop notification, plays an alert sound, and says so in chat. "
                    "Use this whenever the user asks to be reminded after a relative amount of "
                    "time (e.g. 'set a 10 minute timer', 'remind me in 90 seconds'). For an "
                    "action at a specific clock time or date, use schedule_shell_command instead.",
     "parameters": {"type": "OBJECT",
                    "properties": {
                        "duration": {"type": "STRING",
                                     "description": "how long, e.g. '5m', '90s', '1h30m', or a "
                                                    "bare number of seconds. Always include a unit."},
                        "label": {"type": "STRING",
                                  "description": "optional name, e.g. 'tea', 'laundry'"}},
                    "required": ["duration"]}},
    {"name": "list_timers",
     "description": "List active countdown timers and how much time is left on each.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "cancel_timer",
     "description": "Cancel a running countdown timer by its id (from list_timers), or pass 'all'.",
     "parameters": {"type": "OBJECT",
                    "properties": {"timer_id": {"type": "STRING",
                                                "description": "the timer id, or 'all'"}},
                    "required": ["timer_id"]}},
]

TOOL_DISPATCH = {
    "set_timer": set_timer,
    "list_timers": list_timers,
    "cancel_timer": cancel_timer,
}

# Setting/cancelling a timer is a harmless, instant local action (no input injection, no writes
# to the user's files) — treat them as safe so Ember can act without a confirm prompt.
READONLY_TOOLS = {"list_timers"}
INTERACTION_TOOLS = {"set_timer", "cancel_timer"}
