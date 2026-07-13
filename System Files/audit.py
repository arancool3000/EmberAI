"""Tamper-evident audit log of every action Ember takes.

Each action is appended as one JSON line to <support>/audit/audit.log with a
SHA-256 hash chain: every entry's hash covers the previous entry's hash, so any
edit, deletion, or reordering of past entries is detectable via verify().

Arguments and result summaries are redacted (redaction.scrub_obj) before they are
written, so the audit trail itself never stores secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_GENESIS = "0" * 64


def _support_dir() -> Path:
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Ember"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _log_path() -> Path:
    d = _support_dir() / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d / "audit.log"


def _entry_hash(payload: dict) -> str:
    """Hash of an entry (which already includes its 'prev' field)."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _last_hash() -> str:
    p = _log_path()
    if not p.exists():
        return _GENESIS
    last = b""
    try:
        with open(p, "rb") as f:
            for line in f:
                if line.strip():
                    last = line
        if last:
            return json.loads(last).get("hash", _GENESIS)
    except Exception:
        pass
    return _GENESIS


def record(name: str, args: dict | None = None, risk: str = "",
           result_brief: str = "", actor: str = "agent") -> dict:
    """Append a redacted, hash-chained record of one action. Never raises."""
    try:
        import redaction
        safe_args = redaction.scrub_obj(
            {k: (v if isinstance(v, (int, float, bool)) else str(v)[:200])
             for k, v in (args or {}).items()})
        brief = redaction.scrub_text(str(result_brief)[:300])[0]
    except Exception:
        safe_args = {k: str(v)[:200] for k, v in (args or {}).items()}
        brief = str(result_brief)[:300]
    payload = {"ts": time.time(), "actor": actor, "name": name, "risk": risk,
               "args": safe_args, "result": brief}
    try:
        with _LOCK:
            payload["prev"] = _last_hash()
            h = _entry_hash(payload)
            line = dict(payload, hash=h)
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return {"ok": True, "hash": h}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify() -> dict:
    """Walk the chain and confirm no entry was altered, removed, or reordered."""
    p = _log_path()
    if not p.exists():
        return {"ok": True, "valid": True, "entries": 0}
    prev = _GENESIS
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for i, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                e = json.loads(raw)
                stored = e.pop("hash", None)
                if e.get("prev") != prev:
                    return {"ok": True, "valid": False, "broken_at": i,
                            "reason": "chain link mismatch (entry inserted/removed/reordered)"}
                if _entry_hash(e) != stored:
                    return {"ok": True, "valid": False, "broken_at": i,
                            "reason": "entry contents were modified"}
                prev = stored
                n += 1
        return {"ok": True, "valid": True, "entries": n}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def tail(n: int = 20) -> dict:
    """Return the most recent n audit entries (newest last)."""
    p = _log_path()
    if not p.exists():
        return {"ok": True, "count": 0, "entries": []}
    try:
        lines = [ln for ln in p.read_text("utf-8").splitlines() if ln.strip()]
        entries = []
        for ln in lines[-int(n):]:
            e = json.loads(ln)
            entries.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("ts", 0))),
                "actor": e.get("actor"), "name": e.get("name"),
                "risk": e.get("risk"), "args": e.get("args"), "result": e.get("result"),
            })
        return {"ok": True, "count": len(entries), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}
