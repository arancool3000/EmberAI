"""AI-built browser extensions (userscripts) for the Ember Browser.

A tiny, dependency-free store of user-authored "extensions" — really userscripts: a
chunk of JavaScript plus a URL match pattern. The Ember Browser injects every enabled,
matching script after a page finishes loading. The headline feature is that you can
DESCRIBE what you want in plain English ("hide the comments section on YouTube",
"add a dark background to every page") and Ember's AI writes the JavaScript for you.

This module is pure logic — storage, URL matching, prompt-building and output-cleaning —
so it's fully unit-testable without Qt or any LLM. The browser does the actual page
injection and the one AI call (via its existing _model_text helper).

Storage: a JSON list at <ember-data-dir>/browser_extensions.json, each entry:
    {id, name, description, match, js, enabled, created}
Tests redirect the file via the EMBER_EXT_FILE env var.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urlparse

_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_path() -> Path:
    """Where extensions are persisted. EMBER_EXT_FILE overrides (used by tests)."""
    override = os.environ.get("EMBER_EXT_FILE")
    if override:
        p = Path(override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        import remote_server   # reuse the app's data dir if available
        d = remote_server._data_dir()
    except Exception:
        d = Path.home() / ".ember"
        d.mkdir(parents=True, exist_ok=True)
    return Path(d) / "browser_extensions.json"


def _load() -> list[dict]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def _save(items: list[dict]) -> bool:
    """Atomic write (temp + replace) so a crash can't truncate the store."""
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_extensions() -> list[dict]:
    with _LOCK:
        return _load()


def get_extension(ext_id: str) -> dict | None:
    with _LOCK:
        for e in _load():
            if e.get("id") == ext_id:
                return e
    return None


def save_extension(name: str, match: str, js: str, description: str = "",
                   ext_id: str | None = None, enabled: bool = True) -> dict:
    """Create or update (when ext_id matches an existing one) an extension. Returns it."""
    name = (name or "Untitled").strip() or "Untitled"
    match = (match or "*").strip() or "*"
    js = js or ""
    with _LOCK:
        items = _load()
        if ext_id:
            for e in items:
                if e.get("id") == ext_id:
                    e.update(name=name, match=match, js=js,
                             description=description, enabled=bool(enabled))
                    _save(items)
                    return e
        entry = {"id": ext_id or uuid.uuid4().hex[:10], "name": name, "match": match,
                 "js": js, "description": description, "enabled": bool(enabled),
                 "created": int(time.time())}
        items.append(entry)
        _save(items)
        return entry


def delete_extension(ext_id: str) -> bool:
    with _LOCK:
        items = _load()
        kept = [e for e in items if e.get("id") != ext_id]
        if len(kept) == len(items):
            return False
        return _save(kept)


def set_enabled(ext_id: str, enabled: bool) -> bool:
    with _LOCK:
        items = _load()
        hit = False
        for e in items:
            if e.get("id") == ext_id:
                e["enabled"] = bool(enabled)
                hit = True
        return _save(items) if hit else False


# ---------------------------------------------------------------------------
# URL matching + injection
# ---------------------------------------------------------------------------

def match_url(pattern: str, url: str) -> bool:
    """Does `url` match `pattern`? Supports '*' (all sites), a bare domain
    ('example.com' matches the host and its subdomains), and glob patterns
    ('https://*.example.com/*', '*youtube*')."""
    pattern = (pattern or "").strip().lower()
    url = (url or "").strip()
    if not pattern or pattern == "*":
        return True
    if not url:
        return False
    u = url.lower()
    host = (urlparse(url).netloc or "").lower()
    if "*" in pattern or "?" in pattern:
        return fnmatch(u, pattern) or (bool(host) and fnmatch(host, pattern))
    if "/" not in pattern:   # a bare domain — exact host or a subdomain of it
        return host == pattern or host.endswith("." + pattern)
    return pattern in u      # a path/url substring


def scripts_for_url(url: str) -> list[dict]:
    """Enabled extensions whose match pattern applies to `url` (injection order)."""
    return [e for e in list_extensions()
            if e.get("enabled", True) and (e.get("js") or "").strip()
            and match_url(e.get("match", "*"), url)]


def wrap_for_injection(js: str) -> str:
    """Wrap user JS in an isolated IIFE + try/catch so a broken script can't take down
    the page (the error just goes to the console)."""
    return ("(function(){try{\n" + (js or "") +
            "\n}catch(e){console.warn('[Ember extension] '+e);}})();")


# ---------------------------------------------------------------------------
# AI generation helpers (the browser does the actual model call)
# ---------------------------------------------------------------------------

def build_userscript_prompt(description: str, url: str = "") -> str:
    """Prompt that makes the model emit ONLY runnable JavaScript for a userscript."""
    ctx = f"\nThe user is currently on this page: {url}\n" if url else ""
    return (
        "You write browser userscripts. Output ONLY JavaScript — no Markdown, no code "
        "fences, no commentary — that will be injected into a web page after it loads, "
        "running in the page's context. It must accomplish this request:\n\n"
        f"\"{description.strip()}\"\n{ctx}\n"
        "Requirements:\n"
        "- Self-contained vanilla JS (no imports, no external libraries, no network calls).\n"
        "- Defensive: guard every DOM lookup (elements may be missing) and never throw.\n"
        "- Idempotent where possible (safe if the page re-runs it).\n"
        "- Do NOT exfiltrate data, add tracking, or contact remote servers.\n"
        "Return just the script body.")


def extract_js(model_output: str) -> str:
    """Pull clean JS out of a model reply, tolerating ```js fences / stray prose."""
    text = (model_output or "").strip()
    if "```" in text:
        # Take the contents of the first fenced block.
        parts = text.split("```")
        if len(parts) >= 3:
            block = parts[1]
            # Drop an optional language tag on the first line (```js / ```javascript).
            lines = block.split("\n", 1)
            if lines and lines[0].strip().lower() in ("js", "javascript", ""):
                block = lines[1] if len(lines) > 1 else ""
            return block.strip()
    return text
