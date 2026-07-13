"""Background automation engine: simple trigger->action rules that fire without the AI.

Rules persist to automations.json next to the executable. Example rule:
  {
    "name": "Auto-accept UAC popups",
    "trigger": {"type": "new_window", "title_contains": "User Account Control"},
    "action": {"type": "press_key", "args": {"keys": "alt+y"}},
    "enabled": true
  }

Triggers:
  new_window      - fires once when a top-level window with the matching title appears
  window_visible  - fires every N seconds while a matching window is visible

Actions (mirror existing tool names):
  press_key, click, click_element_by_text, type_text, run_powershell, focus_window
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _data_dir() -> Path:
    """Writable runtime dir. In a frozen app, never write inside the bundle (it breaks the
    code signature -> slow relaunch / read-only /Applications); use the OS user-data dir."""
    if not getattr(sys, "frozen", False):
        return _base_dir()
    home = Path.home()
    if sys.platform == "darwin":
        d = home / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = home / "AppData" / "Roaming" / "Ember"
    else:
        d = home / ".ember"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


RULES_PATH = _data_dir() / "automations.json"


DEFAULT_RULES = [
    {
        "name": "Accept UAC prompts (Yes)",
        "trigger": {"type": "new_window", "title_contains": "User Account Control"},
        "action": {"type": "press_key", "args": {"keys": "alt+y"}},
        "enabled": False,
    },
    {
        "name": "Press Enter on generic confirm dialogs",
        "trigger": {"type": "new_window", "title_contains": "Confirm"},
        "action": {"type": "press_key", "args": {"keys": "enter"}},
        "enabled": False,
    },
    {
        "name": "Dismiss Windows notification toasts",
        "trigger": {"type": "new_window", "title_contains": "Notification"},
        "action": {"type": "press_key", "args": {"keys": "esc"}},
        "enabled": False,
    },
]


def load_rules() -> list[dict]:
    if RULES_PATH.exists():
        try:
            data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    save_rules(DEFAULT_RULES)
    return list(DEFAULT_RULES)


def save_rules(rules: list[dict]):
    try:
        RULES_PATH.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    except OSError:
        pass


POPUP_KEYWORDS = (
    "confirm", "are you sure", "save changes", "do you want",
    "warning", "question", "alert", "message", "notice", "do you",
    "exit", "close", "unsaved",
)

# Titles that LOOK like popups but require deliberate human judgment.
# Auto-confirm must NEVER press Enter on these.
EXCLUDE_KEYWORDS = (
    # Destructive operations - user must vet
    "delete", "remove", "permanently", "wipe", "erase",
    "uninstall", "format", "factory reset", "destroy",
    "overwrite", "replace existing", "merge",
    # Identity / payment
    "sign in", "log in", "login", "password", "credentials",
    "payment", "pay", "purchase", "credit card", "billing",
    "verify", "authenticate", "2fa", "two-factor", "captcha",
    # Ember's own UI - we should never click our own confirm/handoff dialogs
    "ember", "hand-off", "handoff", "approve risky", "manual mode",
    "gemini is consulting", "claude",
    # Common destructive Windows / browser dialogs
    "send to recycle bin", "move to trash",
    "are you sure you want to leave", "leave site",
    "discard", "unsaved changes",
)


def _is_excluded_popup(title_lower: str) -> bool:
    """True if this looks like a popup that needs deliberate human judgment."""
    return any(k in title_lower for k in EXCLUDE_KEYWORDS)


class AutomationEngine:
    """Runs in a daemon thread; polls window titles and fires actions."""

    def __init__(self, on_fire: Callable[[str, dict, dict], None] | None = None,
                 poll_interval: float = 1.5):
        self.on_fire = on_fire  # (rule_name, trigger, action) callback for UI logging
        self.poll_interval = poll_interval
        self.enabled = True
        self.auto_confirm_popups = False  # set by EmberWindow from settings
        self.rules: list[dict] = load_rules()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_window_titles: set[str] = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def set_rules(self, rules: list[dict]):
        self.rules = list(rules or [])
        save_rules(self.rules)

    def add_rule(self, rule: dict):
        self.rules.append(rule)
        save_rules(self.rules)

    def remove_rule(self, index: int):
        if 0 <= index < len(self.rules):
            self.rules.pop(index)
            save_rules(self.rules)

    def toggle_rule(self, index: int):
        if 0 <= index < len(self.rules):
            self.rules[index]["enabled"] = not self.rules[index].get("enabled", False)
            save_rules(self.rules)

    def _snapshot_windows(self) -> set[str]:
        """Return set of currently visible top-level window titles - EXCLUDES Ember's own windows
        so the engine never auto-acts on the agent's own confirmation/handoff dialogs."""
        import os
        my_pid = os.getpid()
        titles: set[str] = set()
        try:
            import tools
            r = tools.list_windows()
            if r.get("ok"):
                for w in r.get("windows", []):
                    if w.get("process_id") == my_pid:
                        continue  # skip our own windows
                    title = (w.get("title") or "").strip()
                    if title:
                        titles.add(title)
        except Exception:
            pass
        return titles

    def _fire(self, rule: dict):
        action = rule.get("action") or {}
        atype = action.get("type")
        args = action.get("args") or {}
        try:
            import tools
            fn = getattr(tools, atype, None)
            if not callable(fn):
                return
            fn(**args)
            if self.on_fire:
                self.on_fire(rule.get("name", "(unnamed)"), rule.get("trigger") or {}, action)
        except Exception:
            pass

    def _loop(self):
        # Initial snapshot - don't fire on windows that were already there at startup.
        try:
            self._seen_window_titles = self._snapshot_windows()
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                if self.enabled:
                    current = self._snapshot_windows()
                    new_titles = current - self._seen_window_titles

                    # Built-in auto-confirm rule (no need to add it manually). Isolated so a
                    # failure here can't skip the user's own rules below.
                    if self.auto_confirm_popups:
                        try:
                            for title in new_titles:
                                tlow = title.lower()
                                if not any(k in tlow for k in POPUP_KEYWORDS):
                                    continue
                                if _is_excluded_popup(tlow):
                                    continue  # destructive / human-required - skip
                                self._fire({
                                    "name": f"Auto-confirm: {title[:40]}",
                                    "trigger": {"type": "new_window", "title_contains": title},
                                    "action": {"type": "press_key", "args": {"keys": "enter"}},
                                })
                                break
                        except Exception:
                            pass

                    for rule in self.rules:
                        # Per-rule isolation: one malformed rule must not abort the whole
                        # cycle (which would also stop later rules from ever seeing new windows).
                        try:
                            if not rule.get("enabled"):
                                continue
                            trig = rule.get("trigger") or {}
                            ttype = trig.get("type")
                            substr = (trig.get("title_contains") or "").strip().lower()
                            if not substr:
                                continue
                            if ttype == "new_window":
                                for title in new_titles:
                                    if substr in title.lower():
                                        self._fire(rule)
                                        break
                            elif ttype == "window_visible":
                                for title in current:
                                    if substr in title.lower():
                                        self._fire(rule)
                                        break
                        except Exception:
                            continue

                    # Commit the snapshot only AFTER processing, so a mid-cycle error never
                    # silently consumes a new window (its new_window rule would never fire).
                    self._seen_window_titles = current
            except Exception:
                pass
            self._stop.wait(self.poll_interval)
