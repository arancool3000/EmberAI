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
    from app_data import data_dir
    return data_dir()


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
    # Strip any secrets (API keys, passwords, tokens, PII) before they hit disk.
    try:
        import redaction
        args = redaction.scrub_obj(args or {})
        result_summary = redaction.scrub_text(result_summary)[0]
    except Exception:
        pass
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


# ---------------------------------------------------------------------------
# Learning about the user — auto-extract durable facts from what they say, and
# surface the RELEVANT ones into each turn (not just the newest).
# ---------------------------------------------------------------------------

import re as _re

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "is",
    "are", "was", "were", "be", "you", "your", "my", "me", "i", "it", "this", "that",
    "please", "can", "could", "would", "should", "do", "does", "did", "have", "has",
    "want", "need", "make", "get", "got", "use", "using", "now", "then", "so", "just",
    "what", "when", "where", "why", "how", "who", "which", "ember",
}

# High-precision patterns: (regex, key-builder, value-builder, category). The capture is
# bounded to one clause — it stops at sentence enders, commas, and " and " so two facts in
# one sentence ("my name is Sam and my timezone is GMT") don't bleed into each other.
_CLAUSE = r"([^.!?;\n,]{2,120}?)(?=\s+and\s|,|[.!?;\n]|$)"
_LEARN_PATTERNS = [
    (_re.compile(r"\b(?:remember|note|keep in mind|don'?t forget|for future reference|fyi)"
                 r"(?: that)?[:,]?\s+" + _CLAUSE, _re.I),
     lambda m: "note:" + _slug(m.group(1)), lambda m: m.group(1).strip(), "note"),
    (_re.compile(r"\bmy ([a-z][\w ]{1,28}?) (?:is|are|=)\s+" + _CLAUSE, _re.I),
     lambda m: _slug(m.group(1)), lambda m: m.group(2).strip(), "identity"),
    (_re.compile(r"\b(?:i'?m|i am) (?:called|named)\s+([A-Za-z][\w'-]{1,30})", _re.I),
     lambda m: "name", lambda m: m.group(1).strip(), "identity"),
    (_re.compile(r"\bcall me\s+([A-Za-z][\w'-]{1,30})", _re.I),
     lambda m: "name", lambda m: m.group(1).strip(), "identity"),
    (_re.compile(r"\bi (?:prefer|like|love|enjoy|usually|always)\s+" + _CLAUSE, _re.I),
     lambda m: "pref:" + _slug(m.group(1)), lambda m: "prefers " + m.group(1).strip(), "preference"),
    (_re.compile(r"\bi (?:hate|dislike|don'?t like|do not like)\s+" + _CLAUSE, _re.I),
     lambda m: "pref:" + _slug(m.group(1)), lambda m: "dislikes " + m.group(1).strip(), "preference"),
    # Directive form, anchored to the clause start so "the app always crashes" doesn't match.
    (_re.compile(r"^(?:please\s+)?(?:always|never)\s+" + _CLAUSE, _re.I),
     lambda m: "pref:rule-" + _slug(m.group(1)), lambda m: "rule: " + m.group(0).strip(), "preference"),
    (_re.compile(r"\bi work (?:at|for)\s+" + _CLAUSE, _re.I),
     lambda m: "employer", lambda m: m.group(1).strip(), "identity"),
]

# Never auto-memorise anything that smells like a credential.
_SECRET_RE = _re.compile(r"\b(password|passwd|api[\s_-]?key|secret|token|credential|otp|2fa)\b", _re.I)


def _slug(s: str, words: int = 5) -> str:
    toks = [t for t in _re.split(r"[^a-z0-9]+", (s or "").lower()) if t]
    return "-".join(toks[:words])[:48] or "x"


def _tokens(s: str) -> set:
    return {t for t in _re.split(r"[^a-z0-9]+", (s or "").lower())
            if len(t) >= 3 and t not in _STOPWORDS}


def extract_facts(text: str) -> list[tuple[str, str, str]]:
    """Pure: pull durable (key, value, category) facts out of a user message. Conservative —
    only clear preference/identity/note statements, never secrets or questions."""
    out: list[tuple[str, str, str]] = []
    if not text or not text.strip():
        return out
    seen_keys = set()
    for sentence in _re.split(r"(?<=[.!?;\n])\s+", text):
        s = sentence.strip()
        if not s or "?" in s or _SECRET_RE.search(s):
            continue   # skip questions and anything credential-shaped
        for rx, keyf, valf, cat in _LEARN_PATTERNS:
            for m in rx.finditer(s):   # multiple facts can share one sentence
                value = valf(m).strip().rstrip(".,!;:")
                key = keyf(m).strip()
                if not value or len(value) < 2 or len(value) > 120 or key in seen_keys:
                    continue
                if _SECRET_RE.search(value):
                    continue
                seen_keys.add(key)
                out.append((key, value, cat))
    return out


def learn_from_message(text: str) -> dict:
    """Auto-learn durable facts from a user message (called every turn). Idempotent: an
    unchanged fact isn't re-saved, so repeats don't churn the file or reorder recency."""
    learned = []
    try:
        candidates = extract_facts(text)
        if not candidates:
            return {"ok": True, "learned": []}
        with _LOCK:
            existing = _load().get("facts", {})
            for key, value, cat in candidates:
                cur = existing.get(key)
                if isinstance(cur, dict) and cur.get("value") == value:
                    continue   # already known, unchanged
                remember(key, value, category=cat)
                learned.append(key)
    except Exception:
        pass
    return {"ok": True, "learned": learned}


def get_relevant_facts(query: str | None = None, max_facts: int = 12) -> str:
    """Compact summary of the facts most RELEVANT to `query` (token overlap), padded with the
    newest facts for general context. Falls back to newest-first when query is empty."""
    data = _load()
    facts = data.get("facts", {})
    if not facts:
        return ""
    items = list(facts.items())

    def _recency(kv):
        v = kv[1]
        return v.get("saved_at", 0) if isinstance(v, dict) else 0

    def _val(v):
        return v.get("value", "") if isinstance(v, dict) else str(v)

    if query and query.strip():
        q = _tokens(query)
        scored = []
        for k, v in items:
            overlap = len(q & _tokens(k + " " + _val(v)))
            scored.append((overlap, _recency((k, v)), k, v))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        chosen = [(k, v) for ov, _r, k, v in scored if ov > 0][:max_facts]
        if len(chosen) < min(6, len(items)):   # always carry a little general context
            for k, v in sorted(items, key=_recency, reverse=True):
                if (k, v) not in chosen:
                    chosen.append((k, v))
                if len(chosen) >= min(max_facts, max(6, len(chosen) + 1)):
                    break
        ordered = chosen[:max_facts]
    else:
        ordered = sorted(items, key=_recency, reverse=True)[:max_facts]

    lines = [f"- {k}: {_val(v)}" for k, v in ordered]
    return "\n".join(lines)


def profile() -> dict:
    """Everything Ember has learned about the user, grouped by category (for a 'what do you
    know about me?' view + the agent's recall tool)."""
    data = _load()
    facts = data.get("facts", {})
    by_cat: dict[str, list] = {}
    for k, v in facts.items():
        cat = v.get("category", "general") if isinstance(v, dict) else "general"
        val = v.get("value", "") if isinstance(v, dict) else str(v)
        by_cat.setdefault(cat, []).append({"key": k, "value": val})
    return {"ok": True, "count": len(facts), "by_category": by_cat}
