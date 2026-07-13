"""Ember integrations — let agents and the Security Center push updates to the
places people actually watch: Slack, Telegram, Discord, or a generic webhook.

This is the "agents that send you updates / live in your chat tools" half of the
Base44 Superagent idea, kept deliberately lightweight: every channel here needs
only a webhook URL or a bot token (no OAuth dance, no server), so it works out of
the box once the user pastes one value.

  set_integration("slack", webhook_url=...)        # Slack incoming webhook
  set_integration("telegram", bot_token=..., chat_id=...)
  set_integration("discord", webhook_url=...)
  set_integration("webhook", url=...)              # POSTs {"text": ...}
  notify("Backup finished ✅")                      # -> every configured channel
  notify("...", channel="slack")                   # -> just one

Config lives in the shared Ember support dir (EMBER_SUPPORT_DIR honored).
``_HTTP`` is an injection point so tests run fully offline.
"""
from __future__ import annotations

import json
import os
import sys
import threading

# Injection point for tests: callable(method, url, **kw) -> (status:int, text:str).
# Default None -> use requests.
_HTTP = None
_LOCK = threading.RLock()

# channel -> required credential fields + which of them are secret (masked in listings)
CHANNELS: dict[str, dict] = {
    "slack":    {"fields": ["webhook_url"], "secret": ["webhook_url"],
                 "label": "Slack (incoming webhook)"},
    "discord":  {"fields": ["webhook_url"], "secret": ["webhook_url"],
                 "label": "Discord (webhook)"},
    "telegram": {"fields": ["bot_token", "chat_id"], "secret": ["bot_token"],
                 "label": "Telegram bot"},
    "webhook":  {"fields": ["url"], "secret": [],
                 "label": "Generic webhook (POST JSON {text})"},
}


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


def _store_path():
    return _support_dir() / "integrations.json"


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


def _http(method: str, url: str, **kw):
    if _HTTP is not None:
        return _HTTP(method, url, **kw)
    import requests  # lazy
    r = requests.request(method, url, timeout=kw.pop("timeout", 15), **kw)
    return r.status_code, (r.text or "")


def _mask(value: str) -> str:
    s = str(value or "")
    if len(s) <= 8:
        return "•" * len(s)
    return s[:4] + "…" + s[-4:]


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------

def set_integration(channel: str, **fields) -> dict:
    """Configure (or update) a channel. Returns {ok, channel}."""
    channel = (channel or "").strip().lower()
    if channel not in CHANNELS:
        return {"ok": False, "error": f"unknown channel '{channel}'. "
                f"Choose: {', '.join(CHANNELS)}"}
    spec = CHANNELS[channel]
    missing = [f for f in spec["fields"] if not str(fields.get(f, "")).strip()]
    if missing:
        return {"ok": False, "error": f"{channel} needs: {', '.join(missing)}"}
    clean = {f: str(fields[f]).strip() for f in spec["fields"]}
    with _LOCK:
        store = _load()
        store[channel] = clean
        _save(store)
    return {"ok": True, "channel": channel, "label": spec["label"]}


def remove_integration(channel: str) -> dict:
    channel = (channel or "").strip().lower()
    with _LOCK:
        store = _load()
        if channel not in store:
            return {"ok": False, "error": f"'{channel}' is not configured"}
        store.pop(channel, None)
        _save(store)
    return {"ok": True, "removed": channel}


def list_integrations() -> dict:
    """List configured channels with secrets masked."""
    store = _load()
    out = []
    for channel, cfg in store.items():
        spec = CHANNELS.get(channel, {"secret": [], "label": channel})
        shown = {}
        for k, v in cfg.items():
            shown[k] = _mask(v) if k in spec.get("secret", []) else v
        out.append({"channel": channel, "label": spec.get("label", channel), "config": shown})
    out.sort(key=lambda x: x["channel"])
    return {"ok": True, "count": len(out), "channels": out,
            "available": [{"channel": c, "label": s["label"], "fields": s["fields"]}
                          for c, s in CHANNELS.items()]}


def is_configured(channel: str | None = None) -> bool:
    store = _load()
    return (channel in store) if channel else bool(store)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _send_one(channel: str, cfg: dict, text: str) -> dict:
    try:
        if channel == "slack":
            status, _ = _http("POST", cfg["webhook_url"], json={"text": text})
        elif channel == "discord":
            status, _ = _http("POST", cfg["webhook_url"], json={"content": text})
        elif channel == "telegram":
            url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
            status, _ = _http("POST", url, json={"chat_id": cfg["chat_id"], "text": text})
        elif channel == "webhook":
            status, _ = _http("POST", cfg["url"], json={"text": text})
        else:
            return {"channel": channel, "ok": False, "error": "unknown channel"}
        ok = 200 <= int(status) < 300
        return {"channel": channel, "ok": ok, "status": int(status)}
    except Exception as e:
        return {"channel": channel, "ok": False, "error": str(e)}


def notify(text: str, channel: str | None = None) -> dict:
    """Send `text` to one channel (if named) or to every configured channel."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "nothing to send (empty text)"}
    store = _load()
    if channel:
        channel = channel.lower()
        if channel not in store:
            return {"ok": False, "error": f"'{channel}' is not configured"}
        targets = {channel: store[channel]}
    else:
        targets = store
    if not targets:
        return {"ok": False, "error": "no integrations configured — set one first"}
    results = [_send_one(c, cfg, text) for c, cfg in targets.items()]
    sent = [r["channel"] for r in results if r.get("ok")]
    errors = [r for r in results if not r.get("ok")]
    return {"ok": bool(sent), "sent": sent, "results": results,
            "errors": errors or None}


# ---------------------------------------------------------------------------
# Tool wrappers + wiring
# ---------------------------------------------------------------------------

def integration_set(channel: str, webhook_url: str = "", url: str = "",
                    bot_token: str = "", chat_id: str = "") -> dict:
    fields = {}
    for k, v in (("webhook_url", webhook_url), ("url", url),
                 ("bot_token", bot_token), ("chat_id", chat_id)):
        if v:
            fields[k] = v
    return set_integration(channel, **fields)


def integration_list() -> dict:
    return list_integrations()


def integration_remove(channel: str) -> dict:
    return remove_integration(channel)


TOOL_DECLARATIONS = [
    {"name": "notify",
     "description": "Send a short update/notification to the user's connected channels "
                    "(Slack / Telegram / Discord / webhook). Use after finishing a task or "
                    "to report a finding. Sends to all configured channels unless one is named.",
     "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"},
        "channel": {"type": "STRING", "description": "optional: slack|telegram|discord|webhook"},
     }, "required": ["text"]}},
    {"name": "integration_set",
     "description": "Connect a notification channel: Slack/Discord (webhook_url), "
                    "Telegram (bot_token + chat_id), or a generic webhook (url).",
     "parameters": {"type": "OBJECT", "properties": {
        "channel": {"type": "STRING", "description": "slack | telegram | discord | webhook"},
        "webhook_url": {"type": "STRING"}, "url": {"type": "STRING"},
        "bot_token": {"type": "STRING"}, "chat_id": {"type": "STRING"},
     }, "required": ["channel"]}},
    {"name": "integration_list",
     "description": "List connected notification channels (secrets masked) and what's available.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "integration_remove",
     "description": "Disconnect a notification channel by name.",
     "parameters": {"type": "OBJECT",
                    "properties": {"channel": {"type": "STRING"}}, "required": ["channel"]}},
]

TOOL_DISPATCH = {
    "notify": notify,
    "integration_set": integration_set,
    "integration_list": integration_list,
    "integration_remove": integration_remove,
}

READONLY_TOOLS = {"integration_list"}
INTERACTION_TOOLS = {"notify", "integration_set", "integration_remove"}
