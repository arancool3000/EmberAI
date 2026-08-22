"""Bidirectional live chat between Ember's UI and any connected MCP client.

The MCP client remains the model host: Ember never scrapes ChatGPT/Claude and needs no model
API key for this mode. A client connects, long-polls for user messages, publishes useful status,
and sends partial or final replies back to the Ember UI. The bridge's loopback bearer token is
still the outer security boundary; session IDs prevent one local client consuming another's chat.
"""
from __future__ import annotations

import secrets
import threading
import time
from typing import Callable

_MAX_TEXT = 200_000
_MAX_EVENTS = 240
_MAX_WAIT_SECONDS = 45
_SESSION_TTL_SECONDS = 24 * 60 * 60
_SESSION_LEASE_SECONDS = 3 * 60
_ALLOWED_STATES = {
    "connected", "waiting", "thinking", "working", "using_tool", "idle", "done", "error",
}

_LOCK = threading.RLock()
_CHANGED = threading.Condition(_LOCK)
_SESSIONS: dict[str, dict] = {}
_ACTIVE_ID = ""
_EVENT_CALLBACK: Callable[[dict], None] | None = None


def _now() -> float:
    return time.time()


def _clean_text(value, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _public(session: dict) -> dict:
    return {
        "session_id": session["id"],
        "client_name": session["client_name"],
        "display_name": session["display_name"],
        "connected": bool(session["connected"]),
        "state": session["state"],
        "detail": session["detail"],
        "connected_at": session["connected_at"],
        "last_seen": session["last_seen"],
        "pending_messages": len(session["pending"]),
        "last_event_id": session["next_event_id"] - 1,
    }


def _emit(event: dict) -> None:
    callback = _EVENT_CALLBACK
    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        pass


def set_event_callback(callback: Callable[[dict], None] | None) -> None:
    """Install the UI callback. It may be called on an MCP HTTP worker thread."""
    global _EVENT_CALLBACK
    with _LOCK:
        _EVENT_CALLBACK = callback


def _prune_locked() -> None:
    global _ACTIVE_ID
    now = _now()
    lease_cutoff = now - _SESSION_LEASE_SECONDS
    for session in _SESSIONS.values():
        if session["connected"] and session["last_seen"] < lease_cutoff:
            session["connected"] = False
            session["state"] = "idle"
            session["detail"] = "Connection expired; ask the MCP client to reconnect"
    cutoff = now - _SESSION_TTL_SECONDS
    stale = [sid for sid, s in _SESSIONS.items()
             if not s["connected"] and s["last_seen"] < cutoff]
    for sid in stale:
        _SESSIONS.pop(sid, None)
    if _ACTIVE_ID not in _SESSIONS or not _SESSIONS[_ACTIVE_ID]["connected"]:
        connected = [s for s in _SESSIONS.values() if s["connected"]]
        _ACTIVE_ID = max(connected, key=lambda s: s["last_seen"])["id"] if connected else ""


def connect(client_name: str = "MCP client", display_name: str = "",
            capabilities: list | None = None) -> dict:
    """Create a live-chat session and make it Ember's selected external model."""
    global _ACTIVE_ID
    client = _clean_text(client_name, 60) or "MCP client"
    display = _clean_text(display_name, 80) or client
    session_id = secrets.token_urlsafe(18)
    now = _now()
    with _CHANGED:
        _prune_locked()
        session = {
            "id": session_id,
            "client_name": client,
            "display_name": display,
            "capabilities": [str(x)[:80] for x in (capabilities or [])[:30]],
            "connected": True,
            "connected_at": now,
            "last_seen": now,
            "state": "connected",
            "detail": "Ready for messages from Ember",
            "events": [],
            "next_event_id": 1,
            "pending": {},
            "reply_buffers": {},
        }
        _SESSIONS[session_id] = session
        _ACTIVE_ID = session_id
        _CHANGED.notify_all()
        public = _public(session)
    _emit({"kind": "connected", **public})
    return {
        "ok": True,
        **public,
        "instructions": (
            "Call ember_live_wait(session_id, after_event_id=0). When it returns a message, "
            "publish thinking/working state with ember_live_set_status, use Ember tools as "
            "needed, send the answer with ember_live_reply, then call ember_live_wait again "
            "using the returned cursor. Continue until a cancel or disconnect event."
        ),
    }


def active_session() -> dict | None:
    with _LOCK:
        _prune_locked()
        session = _SESSIONS.get(_ACTIVE_ID)
        return _public(session) if session and session["connected"] else None


def select_session(session_id: str) -> dict:
    """Select which connected client receives new UI messages (called by Ember's UI)."""
    global _ACTIVE_ID
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or not session["connected"]:
            return {"ok": False, "error": "live MCP session not found or disconnected"}
        _ACTIVE_ID = session["id"]
        session["last_seen"] = _now()
        return {"ok": True, **_public(session)}


def post_user_message(text: str, chat_id: str = "") -> dict:
    """Queue one Ember composer message for the currently selected MCP client."""
    clean = _clean_text(text)
    if not clean:
        return {"ok": False, "error": "message text is required"}
    with _CHANGED:
        _prune_locked()
        session = _SESSIONS.get(_ACTIVE_ID)
        if not session or not session["connected"]:
            return {"ok": False, "error": "no connected MCP live-chat client"}
        message_id = secrets.token_urlsafe(10)
        event_id = session["next_event_id"]
        session["next_event_id"] += 1
        event = {
            "event": "message",
            "event_id": event_id,
            "message_id": message_id,
            "text": clean,
            "chat_id": _clean_text(chat_id, 100),
            "created_at": _now(),
        }
        session["events"].append(event)
        session["events"] = session["events"][-_MAX_EVENTS:]
        session["pending"][message_id] = event
        session["state"] = "waiting"
        session["detail"] = "New message from Ember"
        session["last_seen"] = _now()
        _CHANGED.notify_all()
        public = _public(session)
    _emit({"kind": "message_queued", **public, **event})
    return {"ok": True, "session_id": public["session_id"], "client_name": public["client_name"],
            "display_name": public["display_name"], "message_id": message_id,
            "event_id": event_id}


def wait_for_message(session_id: str, after_event_id: int = 0,
                     timeout_seconds: int = 25) -> dict:
    """Long-poll until Ember sends a message/cancel event, or return a harmless timeout."""
    try:
        cursor = max(0, int(after_event_id or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "after_event_id must be an integer"}
    try:
        timeout = max(1, min(_MAX_WAIT_SECONDS, int(timeout_seconds or 25)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "timeout_seconds must be an integer"}
    deadline = time.monotonic() + timeout
    with _CHANGED:
        while True:
            session = _SESSIONS.get(str(session_id or ""))
            if not session:
                return {"ok": False, "error": "live MCP session not found; reconnect"}
            session["last_seen"] = _now()
            for event in session["events"]:
                if int(event.get("event_id", 0)) > cursor:
                    session["state"] = "connected"
                    session["detail"] = "Message delivered to MCP client"
                    return {"ok": True, "session_id": session["id"],
                            "client_name": session["client_name"], "cursor": event["event_id"],
                            **dict(event)}
            if not session["connected"]:
                return {"ok": True, "event": "disconnect", "session_id": session["id"],
                        "cursor": session["next_event_id"] - 1,
                        "reason": "Session disconnected in Ember"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                session["state"] = "waiting"
                session["detail"] = "Waiting for the next Ember message"
                return {"ok": True, "event": "timeout", "session_id": session["id"],
                        "cursor": session["next_event_id"] - 1, "call_again": True,
                        "instruction": "No message yet. Call ember_live_wait again."}
            _CHANGED.wait(timeout=remaining)


def publish_status(session_id: str, status: str, detail: str = "",
                   message_id: str = "") -> dict:
    state = _clean_text(status, 30).lower().replace(" ", "_")
    if state not in _ALLOWED_STATES:
        return {"ok": False, "error": "status must be one of: " + ", ".join(sorted(_ALLOWED_STATES))}
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or not session["connected"]:
            return {"ok": False, "error": "live MCP session not found or disconnected"}
        session["state"] = state
        session["detail"] = _clean_text(detail, 500)
        session["last_seen"] = _now()
        public = _public(session)
    _emit({"kind": "status", **public, "message_id": _clean_text(message_id, 80)})
    return {"ok": True, **public}


def publish_reply(session_id: str, text: str, message_id: str = "", final: bool = True,
                  append: bool = False) -> dict:
    chunk = str(text or "")[:_MAX_TEXT]
    if not chunk.strip():
        return {"ok": False, "error": "reply text is required"}
    with _LOCK:
        session = _SESSIONS.get(str(session_id or ""))
        if not session or not session["connected"]:
            return {"ok": False, "error": "live MCP session not found or disconnected"}
        mid = _clean_text(message_id, 80)
        if not mid:
            mid = next(reversed(session["pending"]), "")
        if mid and mid not in session["pending"] and mid not in session["reply_buffers"]:
            return {"ok": False, "error": "message_id does not belong to this session"}
        previous = session["reply_buffers"].get(mid, "")
        complete_text = (previous + chunk) if append else chunk
        complete_text = complete_text[:_MAX_TEXT]
        session["reply_buffers"][mid] = complete_text
        is_final = bool(final)
        if is_final:
            session["pending"].pop(mid, None)
            session["reply_buffers"].pop(mid, None)
            session["state"] = "done"
            session["detail"] = "Reply delivered to Ember"
        else:
            session["state"] = "working"
            session["detail"] = "Streaming a reply"
        session["last_seen"] = _now()
        public = _public(session)
    _emit({"kind": "reply", **public, "message_id": mid, "text": complete_text,
           "final": is_final})
    return {"ok": True, "message_id": mid, "final": is_final,
            "characters": len(complete_text), **public}


def cancel_message(session_id: str = "", message_id: str = "",
                   reason: str = "Stopped in Ember") -> dict:
    """Tell a waiting MCP client that the user stopped the current request."""
    with _CHANGED:
        sid = str(session_id or _ACTIVE_ID)
        session = _SESSIONS.get(sid)
        if not session or not session["connected"]:
            return {"ok": False, "error": "live MCP session not found or disconnected"}
        mid = _clean_text(message_id, 80) or next(reversed(session["pending"]), "")
        if mid:
            session["pending"].pop(mid, None)
            session["reply_buffers"].pop(mid, None)
        event_id = session["next_event_id"]
        session["next_event_id"] += 1
        event = {"event": "cancel", "event_id": event_id, "message_id": mid,
                 "reason": _clean_text(reason, 300), "created_at": _now()}
        session["events"].append(event)
        session["events"] = session["events"][-_MAX_EVENTS:]
        session["state"] = "idle"
        session["detail"] = event["reason"]
        session["last_seen"] = _now()
        _CHANGED.notify_all()
    _emit({"kind": "cancelled", "session_id": sid, **event})
    return {"ok": True, "session_id": sid, "message_id": mid, "cursor": event_id}


def session_status(session_id: str = "") -> dict:
    with _LOCK:
        _prune_locked()
        if session_id:
            session = _SESSIONS.get(str(session_id))
            if not session:
                return {"ok": False, "error": "live MCP session not found"}
            return {"ok": True, "active": session["id"] == _ACTIVE_ID, **_public(session)}
        session = _SESSIONS.get(_ACTIVE_ID)
        return {"ok": True, "active_session": _public(session) if session else None,
                "connected_sessions": [_public(s) for s in _SESSIONS.values() if s["connected"]]}


def disconnect(session_id: str, reason: str = "Client disconnected") -> dict:
    global _ACTIVE_ID
    with _CHANGED:
        session = _SESSIONS.get(str(session_id or ""))
        if not session:
            return {"ok": False, "error": "live MCP session not found"}
        session["connected"] = False
        session["state"] = "idle"
        session["detail"] = _clean_text(reason, 300)
        session["last_seen"] = _now()
        public = _public(session)
        if _ACTIVE_ID == session["id"]:
            _ACTIVE_ID = ""
            _prune_locked()
        _CHANGED.notify_all()
    _emit({"kind": "disconnected", **public})
    return {"ok": True, **public}


TOOL_DECLARATIONS = [
    {
        "name": "ember_live_connect",
        "description": ("Connect this ChatGPT, Claude, or other MCP conversation to Ember's live "
                        "chat UI. Returns a session_id and the required wait/status/reply loop. "
                        "Call once, then immediately call ember_live_wait."),
        "parameters": {"type": "OBJECT", "properties": {
            "client_name": {"type": "STRING", "description": "ChatGPT, Claude, or client name"},
            "display_name": {"type": "STRING", "description": "friendly model/session label"},
            "capabilities": {"type": "ARRAY", "items": {"type": "STRING"}},
        }, "required": []},
        "annotations": {"readOnlyHint": False, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "ember_live_wait",
        "description": ("Wait up to 45 seconds for the next message or cancellation from Ember. "
                        "After a timeout, call this again with the returned cursor. After replying, "
                        "call it again so the live chat remains connected."),
        "parameters": {"type": "OBJECT", "properties": {
            "session_id": {"type": "STRING"},
            "after_event_id": {"type": "INTEGER", "description": "last returned cursor, initially 0"},
            "timeout_seconds": {"type": "INTEGER", "description": "1-45, default 25"},
        }, "required": ["session_id"]},
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "ember_live_set_status",
        "description": ("Show live progress in Ember, such as thinking, working, using_tool, idle, "
                        "done, or error. Publish status before doing longer work."),
        "parameters": {"type": "OBJECT", "properties": {
            "session_id": {"type": "STRING"},
            "status": {"type": "STRING", "enum": sorted(_ALLOWED_STATES)},
            "detail": {"type": "STRING"}, "message_id": {"type": "STRING"},
        }, "required": ["session_id", "status"]},
    },
    {
        "name": "ember_live_reply",
        "description": ("Deliver assistant text into Ember's chat. Use final=true for a complete "
                        "reply. For streaming, send final=false and either replace the draft or set "
                        "append=true for delta chunks; finish with final=true."),
        "parameters": {"type": "OBJECT", "properties": {
            "session_id": {"type": "STRING"}, "text": {"type": "STRING"},
            "message_id": {"type": "STRING"}, "final": {"type": "BOOLEAN"},
            "append": {"type": "BOOLEAN"},
        }, "required": ["session_id", "text"]},
    },
    {
        "name": "ember_live_session",
        "description": "Inspect this or the currently selected Ember live-chat session.",
        "parameters": {"type": "OBJECT", "properties": {
            "session_id": {"type": "STRING"},
        }, "required": []},
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "ember_live_disconnect",
        "description": "Disconnect this MCP conversation from Ember's live chat UI.",
        "parameters": {"type": "OBJECT", "properties": {
            "session_id": {"type": "STRING"}, "reason": {"type": "STRING"},
        }, "required": ["session_id"]},
    },
]

TOOL_DISPATCH = {
    "ember_live_connect": connect,
    "ember_live_wait": wait_for_message,
    "ember_live_set_status": publish_status,
    "ember_live_reply": publish_reply,
    "ember_live_session": session_status,
    "ember_live_disconnect": disconnect,
}
