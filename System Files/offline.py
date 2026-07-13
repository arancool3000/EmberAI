"""Offline Mode — run Ember with NO internet.

What "offline" means in practice:
  • The brain runs locally (Ollama) — cloud models (Gemini/Claude) can't reach the network.
  • Voice in/out stay local: offline speech recognition (PocketSphinx) + the system voice.
  • Every LOCAL tool works normally (files, shell, screen/OCR, mouse/keyboard, system info,
    calculations, hashing, the antivirus heuristics, etc.).
  • Tools that ARE the internet (web search, fetch a URL, weather, email, …) can't return
    real data with no connection — in Offline Mode they fail FAST with a clear notice instead
    of hanging on a timeout.
  • Ember makes NO outbound calls of its own (update checks, cloud sync, VirusTotal, blocklist
    fetches) while Offline Mode is on.

This module is the small, pure, testable core: the flag, the network-tool classification, a
uniform offline error, and a quick connectivity probe. It imports nothing heavy.
"""
from __future__ import annotations

import socket
import threading

_LOCK = threading.RLock()
_OFFLINE = False   # toggled by the UI's Offline Mode setting


# Tools that make their OWN outbound request — they cannot produce real results offline.
NETWORK_TOOLS = frozenset({
    "web_search", "http_get", "fetch_url", "public_ip", "dns_lookup", "network_ping",
    "wikipedia_summary", "weather_lookup", "currency_convert", "stock_quote",
    "github_search_repos", "define_word", "speed_test", "send_email", "ask_claude",
    "adblock_update_from_url",
    # Browser tools load remote pages, so they need the network too.
    "browser_open", "browser_navigate", "browser_get_page", "browser_get_text",
    "browser_click_text", "browser_click_selector", "browser_fill", "browser_evaluate",
    "browser_scroll", "browser_back", "browser_forward", "browser_reload",
})


def set_offline(on: bool) -> None:
    global _OFFLINE
    with _LOCK:
        _OFFLINE = bool(on)


def is_offline() -> bool:
    with _LOCK:
        return _OFFLINE


def requires_network(tool_name: str) -> bool:
    return tool_name in NETWORK_TOOLS


def offline_error(tool_name: str) -> dict:
    """A uniform, fast failure for a network tool while Offline Mode is on."""
    return {
        "ok": False, "offline": True,
        "error": (f"'{tool_name}' needs the internet, but Ember is in Offline Mode. "
                  "Turn Offline Mode off in Settings to use it — or ask for something a local "
                  "tool can do (files, shell, screen, system info, calculations, etc.)."),
    }


def network_ok(timeout: float = 1.5) -> bool:
    """Best-effort connectivity probe with no DNS dependency: TCP-connect to a public resolver.
    Returns True if reachable. Used to auto-detect 'no internet' so tools can fail fast."""
    for host, port in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except OSError:
            continue
    return False
