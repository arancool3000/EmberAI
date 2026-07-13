"""Per-API health tracking so Ember switches to an API it *knows* is working.

Every provider/key Ember can call — each Gemini key, Claude, ChatGPT (OpenAI), a local
Ollama, etc. — gets a health record here: a rolling log of what went wrong (rate limits,
auth failures, server errors, timeouts) and when it last succeeded. The agent consults
rank()/best() to try the healthiest API first instead of blindly hammering a rate-limited
one, and records the outcome of every attempt so the picture stays current.

State persists in api_health.json (atomic write under a lock), so what Ember learned about
each API survives restarts. Pure logic + an injectable clock => fully unit-tested, no network.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

# Outcome kinds. Non-success kinds each carry a default cooldown (seconds) during which the
# API is de-prioritised, and a penalty weight used when scoring health.
RATE_LIMIT = "rate_limit"
AUTH_ERROR = "auth_error"
SERVER_ERROR = "server_error"
TIMEOUT = "timeout"
BAD_MODEL = "bad_model"
OTHER_ERROR = "other_error"
SUCCESS = "success"

# (cooldown_seconds, penalty) per issue kind.
_ISSUE_META = {
    RATE_LIMIT:   (60.0, 6.0),     # free-tier per-minute cap clears fast — short cooldown
    AUTH_ERROR:   (900.0, 40.0),   # a bad/expired key won't fix itself — avoid it for a while
    SERVER_ERROR: (30.0, 4.0),
    TIMEOUT:      (20.0, 3.0),
    BAD_MODEL:    (3600.0, 8.0),   # a 404 model id stays wrong for the session and beyond
    OTHER_ERROR:  (20.0, 2.0),
}

_EVENTS_CAP = 40          # keep only the most recent N events per API
_HALF_LIFE = 300.0        # penalty half-life (s): a 5-minute-old rate limit counts ~half

_LOCK = threading.RLock()
_CLOCK: Callable[[], float] = time.time   # injectable so tests never wait on real time


def _now() -> float:
    return _CLOCK()


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


HEALTH_FILE = _data_dir() / "api_health.json"


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def key_fingerprint(key: str) -> str:
    """A short, stable, NON-reversible id for an API key, so the same key is tracked across
    restarts without ever storing the secret itself."""
    key = "".join((key or "").split())
    if not key:
        return "nokey"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def api_id(provider: str, key: str = "") -> str:
    """Stable id for a provider (+ optional key): 'gemini:ab12cd34', 'claude', 'openai:grok'."""
    provider = (provider or "api").strip().lower()
    return f"{provider}:{key_fingerprint(key)}" if key else provider


def classify(err: object) -> str:
    """Map a raw exception / error string to an issue kind, so every agent records consistently."""
    s = str(err).lower()
    if any(t in s for t in ("resource_exhausted", "quota", "rate limit", "rate-limit",
                            "too many requests", "requests per", "429",
                            "exceeded your current quota")):
        return RATE_LIMIT
    if any(t in s for t in ("unauthenticated", "permission_denied", "api key not valid",
                            "invalid api key", "invalid_api_key", "incorrect api key",
                            " 401", "401 ", " 403", "403 ")):
        return AUTH_ERROR
    if "not_found" in s or "not found" in s or " 404" in s or "404 " in s:
        return BAD_MODEL
    if any(t in s for t in ("500", "502", "503", "504", "internal", "unavailable",
                            "overloaded", "server error", "servererror")):
        return SERVER_ERROR
    if any(t in s for t in ("deadline_exceeded", "timeout", "timed out")):
        return TIMEOUT
    return OTHER_ERROR


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load() -> dict:
    try:
        with open(HEALTH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("apis"), dict):
            return data
    except Exception:
        pass
    return {"apis": {}}


def _save(data: dict) -> None:
    try:
        tmp = HEALTH_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(HEALTH_FILE)
    except Exception:
        pass


def _entry(data: dict, aid: str) -> dict:
    apis = data.setdefault("apis", {})
    e = apis.get(aid)
    if not isinstance(e, dict):
        e = {"events": [], "counts": {}, "last_success": 0.0,
             "last_issue": 0.0, "cooldown_until": 0.0}
        apis[aid] = e
    e.setdefault("events", [])
    e.setdefault("counts", {})
    return e


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record(aid: str, kind: str, detail: str = "", cooldown: Optional[float] = None) -> None:
    """Record one outcome for an API id. `kind` is one of the module constants; a success
    clears any active cooldown, an issue starts/extends one."""
    if not aid or not kind:
        return
    with _LOCK:
        data = _load()
        e = _entry(data, aid)
        now = _now()
        e["events"].append({"t": now, "kind": kind, "detail": (detail or "")[:200]})
        e["events"] = e["events"][-_EVENTS_CAP:]
        e["counts"][kind] = int(e["counts"].get(kind, 0)) + 1
        if kind == SUCCESS:
            e["last_success"] = now
            e["cooldown_until"] = 0.0
        else:
            e["last_issue"] = now
            cd = _ISSUE_META.get(kind, _ISSUE_META[OTHER_ERROR])[0] if cooldown is None else float(cooldown)
            e["cooldown_until"] = max(float(e.get("cooldown_until", 0.0)), now + cd)
        _save(data)


def record_error(aid: str, err: object, cooldown: Optional[float] = None) -> str:
    """Convenience: classify `err` and record it. Returns the kind recorded."""
    kind = classify(err)
    record(aid, kind, detail=str(err), cooldown=cooldown)
    return kind


def record_success(aid: str) -> None:
    record(aid, SUCCESS)


# ---------------------------------------------------------------------------
# Querying / ranking
# ---------------------------------------------------------------------------

def in_cooldown(aid: str) -> bool:
    with _LOCK:
        e = _entry(_load(), aid)
    return _now() < float(e.get("cooldown_until", 0.0))


def cooldown_remaining(aid: str) -> float:
    with _LOCK:
        e = _entry(_load(), aid)
    return max(0.0, float(e.get("cooldown_until", 0.0)) - _now())


def score(aid: str) -> float:
    """Higher = healthier. Recent successes add, recent issues subtract (both time-decayed);
    an API still in cooldown is pushed below any that isn't."""
    with _LOCK:
        e = _entry(_load(), aid)
    now = _now()
    s = 0.0
    for ev in e.get("events", []):
        age = max(0.0, now - float(ev.get("t", now)))
        decay = 0.5 ** (age / _HALF_LIFE)
        k = ev.get("kind")
        if k == SUCCESS:
            s += 3.0 * decay
        else:
            s -= _ISSUE_META.get(k, _ISSUE_META[OTHER_ERROR])[1] * decay
    if now < float(e.get("cooldown_until", 0.0)):
        s -= 1000.0
    return s


def rank(api_ids: Iterable[str]) -> list:
    """Order the given API ids healthiest-first; ties keep their original order."""
    ids = [a for a in api_ids if a]
    order = {a: i for i, a in enumerate(ids)}
    # De-duplicate while preserving first-seen order.
    seen, uniq = set(), []
    for a in ids:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return sorted(uniq, key=lambda a: (-score(a), order[a]))


def best(api_ids: Iterable[str]) -> Optional[str]:
    """The single healthiest API id (may still be the least-bad if all are struggling)."""
    ranked = rank(api_ids)
    return ranked[0] if ranked else None


def note_for(aid: str) -> str:
    """A short human status line for one API id."""
    with _LOCK:
        e = _entry(_load(), aid)
    cd = cooldown_remaining(aid)
    if cd > 0:
        last = e["events"][-1]["kind"] if e.get("events") else "an issue"
        return f"cooling down {cd:.0f}s after {last.replace('_', ' ')}"
    if not e.get("events"):
        return "untested"
    if float(e.get("last_success", 0.0)) >= float(e.get("last_issue", 0.0)):
        return "healthy"
    return "recovering"


def summary(api_ids: Optional[Iterable[str]] = None) -> dict:
    """Structured health report (for the api_health_status tool / the UI)."""
    with _LOCK:
        data = _load()
    ids = list(api_ids) if api_ids is not None else list(data.get("apis", {}).keys())
    rows = []
    for a in ids:
        rows.append({
            "api": a,
            "status": note_for(a),
            "score": round(score(a), 2),
            "in_cooldown": in_cooldown(a),
            "cooldown_s": round(cooldown_remaining(a), 1),
            "counts": dict(_entry(_load(), a).get("counts", {})),
        })
    rows.sort(key=lambda r: -r["score"])
    return {"ok": True, "apis": rows, "healthiest": (rows[0]["api"] if rows else None)}


def tool_status() -> dict:
    """Tool-friendly health report: which APIs Ember has used and how each is doing, so the
    agent can explain why it switched APIs and which one it currently prefers."""
    s = summary()
    if not s["apis"]:
        return {"ok": True, "apis": [], "healthiest": None,
                "message": "No API issues recorded yet on this install — "
                           "everything Ember has tried has worked."}
    working = [r["api"] for r in s["apis"] if not r["in_cooldown"]]
    cooling = [f"{r['api']} ({r['status']})" for r in s["apis"] if r["in_cooldown"]]
    parts = []
    if working:
        parts.append("working now: " + ", ".join(working))
    if cooling:
        parts.append("cooling down: " + ", ".join(cooling))
    s["message"] = "; ".join(parts) or "all recorded APIs are cooling down"
    return s


def reset(aid: Optional[str] = None) -> None:
    """Forget one API's history (or all of it when aid is None)."""
    with _LOCK:
        data = _load()
        if aid is None:
            data = {"apis": {}}
        else:
            data.get("apis", {}).pop(aid, None)
        _save(data)


def _set_clock(fn: Optional[Callable[[], float]]) -> None:
    """Test hook: swap the clock so unit tests advance time deterministically (no real waits)."""
    global _CLOCK
    _CLOCK = fn or time.time
