"""Floating chat window UI for Ember."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

# Set EMBER_SAFE_MODE=1 to skip native / permission-sensitive startup extras
# (desktop-blur, global hotkey, accessibility auto-prompt, phone-remote autostart).
# Useful for isolating launch crashes: if Ember starts in safe mode but not
# normally, one of those native integrations is the culprit.
_SAFE_MODE = os.environ.get("EMBER_SAFE_MODE", "").strip().lower() not in ("", "0", "false", "no")

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QSize, pyqtSignal, QObject, QTimer,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QAction, QPainter, QColor, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextEdit, QLineEdit, QFrame, QScrollArea, QDialog, QFormLayout,
    QMessageBox, QPlainTextEdit, QSizePolicy, QSystemTrayIcon, QMenu,
    QComboBox, QCheckBox, QTabWidget, QListWidget, QListWidgetItem,
    QInputDialog, QFileDialog, QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
    QSlider, QLayout,
)

import models as model_catalog
import automation as automation_mod
import manual_mode as manual_mod

# NOTE: `agent` (which pulls in google.genai, ~1.7s) is imported lazily in _init_agent so the
# window can paint immediately instead of blocking ~10s on startup. All references to Agent /
# AgentEvent / Pending* in this file are annotation-only (safe under `from __future__ import
# annotations`), and the EventBridge signal is pyqtSignal(object) — so no runtime import needed here.


SLASH_COMMANDS = {
    "/autopilot": "Take over the next computer task end-to-end. Use the screen, apps, browser, files, shell, and automation tools as needed. Ask only for credentials, payments, CAPTCHA/2FA, or irreversible decisions.",
    "/do": "Take over the next computer task end-to-end. Use the screen, apps, browser, files, shell, and automation tools as needed. Ask only for credentials, payments, CAPTCHA/2FA, or irreversible decisions.",
    "/apps": "Look at my screen, identify the current app, and be ready to operate it like a careful human user. Read visible controls, use smart_click/keyboard navigation, and verify each visible change.",
    "/research": "Open the browser, research the topic I give you, compare credible sources, keep notes, and return a concise answer with links or source names when available.",
    "/create": "Help me create the file or asset I describe. Ask what format only if it is unclear; otherwise choose the right local tools, save the result, and show the path.",
    "/automate": "Create or improve a background automation for a repetitive desktop task. Ask what should trigger it and what action it should perform, then create a safe rule.",
    "/schedule": "Schedule a future computer task. Ask for the command/action and exact local time if missing, then use schedule_shell_command or list/cancel existing Ember scheduled tasks.",
    "/diagnose": "Diagnose my system thoroughly. Use get_reliability_events, get_minidumps, get_event_logs for the past 48 hours (System & Application), and check for problem drivers. Summarize the top issues and propose fixes.",
    "/diag": "Diagnose my system thoroughly. Use get_reliability_events, get_minidumps, get_event_logs for the past 48 hours (System & Application), and check for problem drivers. Summarize the top issues and propose fixes.",
    "/web": "Open the automation browser (browser_open) and tell me the page title and any cookie banner you see.",
    "/browser": "Open the automation browser (browser_open) and tell me the page title and any cookie banner you see.",
    "/info": "Show me my system info: OS, CPU, GPU, RAM, BIOS, uptime. Use get_system_info.",
    "/sys": "Show me my system info: OS, CPU, GPU, RAM, BIOS, uptime. Use get_system_info.",
    "/perf": "Show current performance snapshot - CPU, RAM, disks, network. Use get_performance.",
    "/shot": "Take a screenshot and describe what you see.",
    "/screenshot": "Take a screenshot and describe what you see.",
    "/windows": "List my open windows using list_windows.",
    "/crashes": "Look for recent crashes: get_reliability_events(days=7) and get_minidumps. Summarize findings.",
    "/updates": "Show recent Windows updates with get_windows_updates.",
    "/drivers": "List installed drivers - get_installed_drivers. Highlight any that look outdated or third-party.",
    "/fixes": "List the quick_fix recipes available with list_quick_fixes.",
    "/organize": "Help me organize a folder. Ask me which folder, then run a dry-run organize_folder with mode='type' and show what would move.",
    "/dedupe": "Find duplicate files in a folder. Ask me which one first, then use find_duplicate_files.",
    "/biggest": "Find the biggest files on this PC. Ask me which folder to scan (default %USERPROFILE%), then use find_large_files with min_mb=500.",
    "/downloads": "Organize my Downloads folder. Run organize_folder on %USERPROFILE%/Downloads with mode='type' dry_run=true first, then show the plan and ask before committing.",
    "/voice": "__voice_chat__",
    "/remote": "__remote__",
    "/link": "__remote__",
    "/manual": "__manual__",
    "/help": "__help__",
    "/clear": "__clear__",
    "/reset": "__clear__",
    "/forget": "__forget_all__",
    "/update": "__update__",
}

HELP_TEXT = """Slash commands

Autonomy
  /autopilot  take over a computer task end-to-end
  /apps       operate the current desktop app
  /research   browse, compare sources, and report back
  /create     create a local file or asset
  /automate   build a background rule
  /schedule   create/list/cancel timed tasks

System
  /diagnose   full crash/error scan
  /info       OS / CPU / GPU / RAM
  /perf       live performance snapshot
  /crashes    recent crashes only
  /updates    recent Windows updates
  /drivers    installed drivers
  /fixes      list quick-fix recipes

Files
  /organize   organize a folder by type
  /dedupe     find duplicate files
  /biggest    find space-hog files
  /downloads  organize my Downloads

Web
  /web        open automation browser
  /shot       take a screenshot

Session
  /voice      toggle hands-free voice chat
  /remote     start Ember Link for phone control
  /manual     bridge an external AI
  /windows    list open windows
  /clear      clear chat
  /forget     wipe saved facts
  /update     install the latest Ember version
  /help       this list

Global hotkey: configurable in Settings -> Performance (default Ctrl+Shift+Space).
Drop a file/folder onto the chat to discuss it.

Tip: just say "organize my Downloads", "find duplicates in Pictures",
"why is my PC slow", "open my GoPro folder" -- no command needed.
"""


COMMAND_CENTER_ACTIONS = [
    ("Autopilot", "/autopilot"),
    ("Use App", "/apps"),
    ("Research", "/research"),
    ("Create", "/create"),
    ("Screen", "/shot"),
    ("Browser", "/web"),
    ("Files", "/organize"),
    ("Downloads", "/downloads"),
    ("Duplicates", "/dedupe"),
    ("Performance", "/perf"),
    ("Diagnose", "/diagnose"),
    ("Automate", "/automate"),
    ("Schedule", "/schedule"),
    ("Phone Link", "__remote__"),
    ("Manual", "__manual__"),
]


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


SETTINGS_PATH = _data_dir() / "settings.json"
CHAT_HISTORY_PATH = _data_dir() / "chat_history.json"


_MD_RE_FENCE = None
_MD_RE_CODE = None
_MD_RE_BOLD = None
_MD_RE_ITAL = None
_MD_RE_HEAD = None


def _md_to_html(text: str) -> str:
    """Tiny, safe Markdown -> HTML for chat bubbles."""
    import html, re
    global _MD_RE_FENCE, _MD_RE_CODE, _MD_RE_BOLD, _MD_RE_ITAL, _MD_RE_HEAD
    if _MD_RE_FENCE is None:
        _MD_RE_FENCE = re.compile(r"```(?:\w+)?\n?(.*?)```", re.DOTALL)
        _MD_RE_CODE = re.compile(r"`([^`\n]+)`")
        _MD_RE_BOLD = re.compile(r"\*\*(.+?)\*\*")
        _MD_RE_ITAL = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
        _MD_RE_HEAD = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    s = html.escape(text)
    s = _MD_RE_FENCE.sub(
        lambda m: ('<pre style="background:#16161e;padding:6px;border-radius:4px;'
                   'color:#9ece6a;margin:4px 0;white-space:pre-wrap">'
                   + m.group(1).rstrip() + "</pre>"),
        s,
    )
    s = _MD_RE_CODE.sub(
        r'<code style="background:#16161e;color:#9ece6a;padding:0 4px;border-radius:3px">\1</code>',
        s,
    )
    s = _MD_RE_BOLD.sub(r"<b>\1</b>", s)
    s = _MD_RE_ITAL.sub(r"<i>\1</i>", s)
    s = _MD_RE_HEAD.sub(lambda m: f'<b style="color:#7aa2f7">{m.group(2)}</b>', s)
    s = s.replace("\n", "<br>")
    return s


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "gemini_api_key": "",
        "gemini_api_key_secondary": "",
        "gemini_model": "gemini-3.1-flash-lite",
        "model_id": "gemini-3.1-flash-lite",
        "provider": "gemini",
        "anthropic_api_key": "",
        "anthropic_model": "claude-opus-4-8",
        "auto_screenshot": True,
        "autocorrect_chat": True,
        "voice_output": False,
        "voice_chat_spoken_replies": True,
        "voice_chat_auto_send": True,
        "voice_chat_continue_after_silence": True,
        "voice_chat_phrase_timeout": 8,
        "ai_chat_titles": True,
        "dual_api_failover": True,
        "automation_enabled": True,
        "auto_confirm_popups": False,
        "remote_autostart": True,
        "auto_update": True,
        "hotkey": "ctrl+shift+space",
        "request_timeout_seconds": 15,
        "animations_enabled": True,
        "glow_enabled": True,
        "font_size": 12,
        "accent_color": "#7aa2f7",
        "liquid_glass": True,
        "glass_opacity": 75,
        "glass_native_blur": True,
        "window_x": 100,
        "window_y": 100,
    }


def save_settings(settings: dict):
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError as e:
        # Never let a failed write crash the app or silently lose the API key.
        print(f"[settings save failed: {e}] path={SETTINGS_PATH}")


def _new_chat_id() -> str:
    return f"chat_{int(time.time() * 1000)}"


def _make_chat(title: str = "New chat") -> dict:
    now = int(time.time())
    return {"id": _new_chat_id(), "title": title, "created": now, "updated": now, "messages": []}


def load_chat_history() -> dict:
    try:
        data = json.loads(CHAT_HISTORY_PATH.read_text(encoding="utf-8"))
        sessions = data.get("sessions") or []
        if sessions:
            active = data.get("active_id") or sessions[0].get("id")
            return {"active_id": active, "sessions": sessions[:80]}
    except Exception:
        pass
    first = _make_chat("Ember workspace")
    return {"active_id": first["id"], "sessions": [first]}


def save_chat_history(history: dict):
    try:
        CHAT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        sessions = history.get("sessions") or []
        CHAT_HISTORY_PATH.write_text(
            json.dumps({"active_id": history.get("active_id"), "sessions": sessions[:80]}, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[chat history save failed: {e}] path={CHAT_HISTORY_PATH}")


_AUTOCORRECT_WORDS = {
    "teh": "the",
    "adn": "and",
    "recieve": "receive",
    "adress": "address",
    "seperate": "separate",
    "definately": "definitely",
    "occured": "occurred",
    "wierd": "weird",
    "becuase": "because",
    "thier": "their",
    "dont": "don't",
    "doesnt": "doesn't",
    "cant": "can't",
    "wont": "won't",
    "im": "I'm",
    "ive": "I've",
    "idk": "I don't know",
}


def autocorrect_chat_text(text: str) -> tuple[str, bool]:
    """Tiny local autocorrect for chat prompts. Avoids touching code blocks, URLs, and paths."""
    if not text or "```" in text or "http://" in text or "https://" in text:
        return text, False
    if re.search(r"(^|\s)(/|~?/|[A-Za-z]:\\)", text):
        return text, False

    changed = False

    def repl(match):
        nonlocal changed
        word = match.group(0)
        fixed = _AUTOCORRECT_WORDS.get(word.lower())
        if not fixed:
            return word
        changed = True
        if word.isupper():
            return fixed.upper()
        if word[:1].isupper():
            return fixed[:1].upper() + fixed[1:]
        return fixed

    text2 = re.sub(r"\b[A-Za-z']+\b", repl, text)
    text2 = re.sub(r" {2,}", " ", text2)
    return text2, changed or text2 != text


def _glass_style(alpha: int = 180, accent: str = "#ffffff", see_through: int = 70,
                 blurred: bool = False) -> str:
    """Neutral Liquid Glass stylesheet.

    Dark *frosted* glass (light text needs a dark-ish veil), but dressed with real glass
    cues so it reads as glass instead of a flat tint: a top-down light-falloff gradient,
    a bright specular rim ("water-droplet" edge), and a generous corner radius. When a
    native NSVisualEffectView blur is mounted behind (blurred=True) the veil is thinned so
    the real blur shows through.
    """
    win_a = max(12, int((100 - see_through) * 0.9))   # higher glass_opacity => clearer glass
    if blurred:
        win_a = int(win_a * 0.5)                       # let the real blur do the obscuring
    top_a = max(8, win_a - 8)                          # glass catches light at the top…
    mid_a = win_a
    bot_a = min(235, win_a + 36)                       # …and deepens at the bottom for legibility
    bubble_a = max(118, int(alpha * 0.70))
    input_a = max(145, int(alpha * 0.82))
    bg = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
          f" stop:0 rgba(40, 43, 54, {top_a}),"
          f" stop:0.5 rgba(17, 19, 26, {mid_a}),"
          f" stop:1 rgba(9, 10, 14, {bot_a}))")
    bg_bubble = f"rgba(255, 255, 255, {bubble_a})"
    bg_input = f"rgba(255, 255, 255, {input_a})"
    bg_control = "rgba(255, 255, 255, 34)"
    bg_control_hover = "rgba(255, 255, 255, 56)"
    rim = "rgba(255, 255, 255, 145)"                    # bright specular edge — the droplet rim
    edge = "rgba(255, 255, 255, 72)"
    edge_soft = "rgba(255, 255, 255, 36)"
    return f"""
QMessageBox, QInputDialog, QDialog {{ background-color: rgba(18, 18, 20, 236); }}
QMessageBox QLabel, QInputDialog QLabel {{ color: #f6f6f4; background-color: transparent; font-size: 13px; }}
QWidget#root {{
    background: {bg};
    border: 1.5px solid {rim};
    border-radius: 26px;
}}
QFrame#historyPanel {{
    background-color: rgba(255, 255, 255, 28);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 16px;
}}
QFrame#commandPanel {{
    background-color: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 16px;
}}
QLabel#sideTitle {{
    color: #f6f6f4;
    font-size: 13px;
    font-weight: 800;
    padding: 2px 4px;
}}
QLabel#sectionTitle {{
    color: #f6f6f4;
    font-size: 12px;
    font-weight: 850;
    padding: 4px 4px 2px 4px;
}}
QLabel#sideHint {{
    color: rgba(246, 246, 244, 145);
    font-size: 10px;
    padding: 2px 4px;
}}
QLabel#panelHint {{
    color: rgba(246, 246, 244, 150);
    font-size: 10px;
    padding: 0 4px 4px 4px;
}}
QFrame#statusStrip {{
    background-color: rgba(0, 0, 0, 34);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 12px;
}}
QLabel#statusMetric {{
    color: rgba(246, 246, 244, 210);
    font-size: 10px;
    font-weight: 700;
}}
QListWidget#historyList {{
    background-color: rgba(255, 255, 255, 20);
    color: #f6f6f4;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 12px;
    padding: 4px;
    outline: none;
}}
QListWidget#historyList::item {{
    padding: 8px 7px;
    border-radius: 9px;
    margin: 2px;
}}
QListWidget#historyList::item:selected {{
    background-color: rgba(255, 255, 255, 76);
}}
QListWidget#historyList::item:hover {{
    background-color: rgba(255, 255, 255, 46);
}}
QLabel#title {{
    color: #f6f6f4;
    font-weight: 750;
    font-size: 15px;
    padding: 6px 8px;
    letter-spacing: 0.2px;
}}
QLabel#statusBar {{
    color: rgba(246, 246, 244, 170);
    font-size: 11px;
    padding: 0 12px 6px 12px;
    font-weight: 600;
}}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255, 255, 255, 60);
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255, 255, 255, 110); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: rgba(255, 255, 255, 60); border-radius: 3px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: rgba(255, 255, 255, 110); }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTextEdit, QPlainTextEdit {{
    background-color: {bg_input};
    color: #f7f7f5;
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 10px 12px;
    font-family: -apple-system, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    selection-background-color: rgba(255, 255, 255, 82);
}}
QLineEdit {{
    background-color: {bg_input};
    color: #f7f7f5;
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 8px 12px;
    font-size: 13px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid rgba(255, 255, 255, 132);
}}
QPushButton {{
    background-color: {bg_control};
    color: #f6f6f4;
    border: 1px solid {edge_soft};
    border-radius: 12px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 650;
}}
QPushButton:hover {{
    background-color: {bg_control_hover};
    border-color: rgba(255, 255, 255, 100);
}}
QPushButton:pressed {{ background-color: rgba(255, 255, 255, 180); color: #08080a; }}
QPushButton#send {{
    background-color: rgba(255, 255, 255, 218);
    color: #08080a;
    font-weight: 700;
    font-size: 13px;
    border: 1px solid rgba(255, 255, 255, 190);
}}
QPushButton#send:hover {{
    background-color: rgba(255, 255, 255, 238);
}}
QPushButton#approve {{ background-color: #3fb950; color: #ffffff; font-weight: bold; }}
QPushButton#deny    {{ background-color: #f85149; color: #ffffff; font-weight: bold; }}
QPushButton#titleBtn {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 220);
    border: 1px solid {edge_soft};
    border-radius: 10px;
    padding: 0;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#titleBtn:hover {{
    background-color: {bg_control_hover};
    color: #ffffff;
    border-color: rgba(255, 255, 255, 120);
}}
QPushButton#closeBtn {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 220);
    border: 1px solid {edge_soft};
    border-radius: 10px;
    padding: 0;
    font-size: 14px;
    font-weight: 700;
}}
QPushButton#closeBtn:hover {{
    background-color: #f85149;
    color: #ffffff;
    border-color: #f85149;
}}
QPushButton#chip {{
    background-color: {bg_control};
    color: rgba(246, 246, 244, 210);
    border: 1px solid {edge_soft};
    border-radius: 15px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 650;
}}
QPushButton#chip:hover {{
    background-color: {bg_control_hover};
    color: #ffffff;
    border-color: rgba(255, 255, 255, 100);
}}
QPushButton#commandAction {{
    background-color: rgba(255, 255, 255, 30);
    color: rgba(246, 246, 244, 225);
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 11px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 750;
    text-align: left;
}}
QPushButton#commandAction:hover {{
    background-color: rgba(255, 255, 255, 58);
    color: #ffffff;
    border-color: rgba(255, 255, 255, 106);
}}
QPushButton#voiceToggle {{
    background-color: rgba(255, 255, 255, 220);
    color: #08080a;
    border: 1px solid rgba(255, 255, 255, 180);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 850;
}}
QPushButton#voiceToggleOn {{
    background-color: rgba(46, 160, 120, 230);
    color: #ffffff;
    border: 1px solid rgba(155, 255, 210, 180);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 850;
}}
QFrame#bubble {{
    background-color: {bg_bubble};
    border: 1px solid {edge_soft};
    border-radius: 18px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleUser {{
    background-color: rgba(255, 255, 255, 218);
    border: 1px solid rgba(255, 255, 255, 190);
    border-radius: 18px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleUser QLabel {{ color: #08080a; }}
QFrame#bubbleTool {{
    background-color: rgba(255, 255, 255, 24);
    border: 1px solid rgba(255, 255, 255, 34);
    border-radius: 12px;
    padding: 6px 10px;
    margin: 2px 4px;
}}
QFrame#bubbleError {{
    background-color: rgba(56, 32, 32, 200);
    border: 1px solid #f85149;
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#bubbleConfirm {{
    background-color: rgba(56, 48, 22, 200);
    border: 1px solid #d29922;
    border-radius: 12px;
    padding: 10px 14px;
    margin: 4px 2px;
}}
QFrame#typingIndicator {{
    background-color: {bg_bubble};
    border: 1px solid {edge_soft};
    border-radius: 18px;
    padding: 8px 14px;
    margin: 4px 2px;
}}
QLabel#typingDots {{
    color: rgba(255, 255, 255, 220);
    font-size: 14px;
    font-weight: bold;
    letter-spacing: 3px;
}}
QLabel {{ color: #f6f6f4; font-size: 13px; }}
QLabel#meta {{ color: rgba(246, 246, 244, 160); font-size: 10px; font-weight: 650; }}
QFrame#pillRoot {{
    background-color: {bg};
    border: 1px solid {edge};
    border-radius: 19px;
}}
QFrame#pillRoot:hover {{ border-color: rgba(255, 255, 255, 130); }}
QTabBar::tab {{
    background-color: transparent;
    color: rgba(246, 246, 244, 150);
    padding: 8px 14px;
    min-width: 92px;
    border: none;
    font-size: 12px;
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: #ffffff;
    border-bottom: 2px solid rgba(255, 255, 255, 210);
}}
QTabBar::tab:hover {{ color: #f6f6f4; }}
QTabWidget::pane {{ border: none; }}
QCheckBox {{ color: #f6f6f4; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {edge_soft};
    background: rgba(255, 255, 255, 26);
    border-radius: 4px;
}}
QCheckBox::indicator:checked {{
    background: rgba(255, 255, 255, 220);
    border-color: rgba(255, 255, 255, 220);
}}
QComboBox {{
    background-color: {bg_input};
    color: #f6f6f4;
    border: 1px solid {edge_soft};
    border-radius: 12px;
    padding: 6px 10px;
    font-size: 12px;
}}
QComboBox:focus, QComboBox:hover {{ border-color: rgba(255, 255, 255, 120); }}
QComboBox::drop-down {{ border: none; width: 20px; }}
"""


STYLE = """
/* ===== Ember — neutral liquid interface fallback ===== */
/* Palette: graphite glass, frosted white controls, no colored glass tint. */

/* Dialogs: dark panel + light text so native QMessageBox text is always readable. */
QMessageBox, QInputDialog, QDialog { background-color: #161926; }
QMessageBox QLabel, QInputDialog QLabel {
    color: #eef1f8; background-color: transparent; font-size: 13px;
}

QWidget#root {
    background-color: #0c0e16;
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 20px;
}
QFrame#historyPanel {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}
QFrame#commandPanel {
    background-color: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
}
QLabel#sideTitle {
    color: #eef1f8;
    font-size: 13px;
    font-weight: 800;
    padding: 2px 4px;
}
QLabel#sectionTitle {
    color: #eef1f8;
    font-size: 12px;
    font-weight: 800;
    padding: 4px 4px 2px 4px;
}
QLabel#sideHint {
    color: #9298ad;
    font-size: 10px;
    padding: 2px 4px;
}
QLabel#panelHint {
    color: #a7adbd;
    font-size: 10px;
    padding: 0 4px 4px 4px;
}
QFrame#statusStrip {
    background-color: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
}
QLabel#statusMetric {
    color: #cbd1df;
    font-size: 10px;
    font-weight: 700;
}
QListWidget#historyList {
    background-color: rgba(255,255,255,0.025);
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 12px;
    padding: 4px;
    outline: none;
}
QListWidget#historyList::item {
    padding: 8px 7px;
    border-radius: 9px;
    margin: 2px;
}
QListWidget#historyList::item:selected { background-color: rgba(255,255,255,0.16); }
QListWidget#historyList::item:hover { background-color: rgba(255,255,255,0.10); }
QLabel#title {
    color: #eef1f8;
    font-weight: 700;
    font-size: 15px;
    padding: 6px 8px;
    letter-spacing: 0.4px;
}
QLabel#statusBar {
    color: #9298ad;
    font-size: 11px;
    padding: 0 12px 6px 12px;
    font-weight: 500;
    letter-spacing: 0.2px;
}

QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
    background: transparent; border: none;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.13); border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.24); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 2px 4px; }
QScrollBar::handle:horizontal { background: rgba(255,255,255,0.13); border-radius: 3px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,0.24); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTextEdit, QPlainTextEdit {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 14px;
    padding: 11px 14px;
    font-family: -apple-system, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    selection-background-color: rgba(255,255,255,0.32);
}
QLineEdit {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    padding: 9px 13px;
    font-size: 13px;
    selection-background-color: rgba(255,255,255,0.32);
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid rgba(255,255,255,0.56); }

QPushButton {
    background-color: #1e2233;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    padding: 7px 15px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover { background-color: rgba(255,255,255,0.12); border-color: rgba(255,255,255,0.42); }
QPushButton:pressed { background-color: #14172180; }

QPushButton#send {
    background-color: rgba(255,255,255,0.92);
    color: #08080a; font-weight: 700; font-size: 14px; border: none; border-radius: 11px;
}
QPushButton#send:hover {
    background-color: rgba(255,255,255,0.98);
}
QPushButton#approve { background-color: #2ea043; color: #ffffff; font-weight: 700; border: none; }
QPushButton#approve:hover { background-color: #3fb950; }
QPushButton#deny    { background-color: #e5484d; color: #ffffff; font-weight: 700; border: none; }
QPushButton#deny:hover { background-color: #f85149; }

QPushButton#titleBtn {
    background-color: rgba(255,255,255,0.05);
    color: #c9cee0;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#titleBtn:hover { background-color: rgba(255,255,255,0.14); color: #ffffff; border-color: rgba(255,255,255,0.44); }
QPushButton#closeBtn {
    background-color: rgba(255,255,255,0.05);
    color: #c9cee0;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 9px;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#closeBtn:hover { background-color: #e5484d; color: #ffffff; border-color: #e5484d; }

QFrame#pillRoot {
    background-color: #0c0e16;
    border: 1px solid rgba(255,255,255,0.54);
    border-radius: 20px;
}
QFrame#pillRoot:hover { border-color: rgba(255,255,255,0.82); }

QPushButton#chip {
    background-color: rgba(255,255,255,0.04);
    color: #b9c2e0;
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 15px;
    padding: 5px 14px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#chip:hover {
    background-color: rgba(255,255,255,0.14);
    color: #ffffff;
    border-color: rgba(255,255,255,0.42);
}
QPushButton#commandAction {
    background-color: rgba(255,255,255,0.045);
    color: #d6dae5;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 11px;
    padding: 7px 10px;
    font-size: 11px;
    font-weight: 700;
    text-align: left;
}
QPushButton#commandAction:hover {
    background-color: rgba(255,255,255,0.12);
    color: #ffffff;
    border-color: rgba(255,255,255,0.36);
}
QPushButton#voiceToggle {
    background-color: rgba(238,241,248,0.92);
    color: #08080a;
    border: none;
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#voiceToggle:hover {
    background-color: rgba(255,255,255,0.98);
}
QPushButton#voiceToggleOn {
    background-color: #2fa678;
    color: #ffffff;
    border: 1px solid rgba(153,255,209,0.55);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 800;
}
QPushButton#voiceToggleOn:hover {
    background-color: #38bd8a;
}

QFrame#typingIndicator {
    background-color: #161926;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 9px 15px;
    margin: 4px 2px;
}
QLabel#typingDots { color: rgba(255,255,255,0.86); font-size: 14px; font-weight: bold; letter-spacing: 3px; }

QFrame#bubble {
    background-color: #161926;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleUser {
    background-color: rgba(255,255,255,0.9);
    border: 1px solid rgba(255,255,255,0.72);
    border-radius: 16px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleUser QLabel { color: #08080a; }
QFrame#bubbleTool {
    background-color: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 10px;
    padding: 7px 10px;
    margin: 2px;
}
QFrame#bubbleError {
    background-color: #2e1719;
    border: 1px solid #e5484d;
    border-radius: 14px;
    padding: 12px 16px;
    margin: 5px 2px;
}
QFrame#bubbleConfirm {
    background-color: #2c2614;
    border: 1px solid #d29922;
    border-radius: 14px;
    padding: 12px 16px;
    margin: 5px 2px;
}

QLabel { color: #eef1f8; font-size: 13px; }
QLabel#meta { color: #9298ad; font-size: 10px; font-weight: 600; letter-spacing: 0.3px; }

QTabBar::tab {
    background-color: transparent;
    color: #9298ad;
    padding: 8px 12px;
    min-width: 92px;
    border: none;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid rgba(255,255,255,0.82); }
QTabBar::tab:hover { color: #eef1f8; }
QTabWidget::pane { border: none; }

QCheckBox { color: #eef1f8; font-size: 12px; spacing: 9px; }
QCheckBox::indicator {
    width: 17px; height: 17px;
    border: 1px solid rgba(255,255,255,0.18);
    background: #161926;
    border-radius: 5px;
}
QCheckBox::indicator:checked { background: rgba(255,255,255,0.86); border-color: rgba(255,255,255,0.86); }

QComboBox {
    background-color: #161926;
    color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    padding: 7px 12px;
    font-size: 12px;
}
QComboBox:focus, QComboBox:hover { border-color: rgba(255,255,255,0.5); }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #1e2233; color: #eef1f8;
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 10px; selection-background-color: rgba(255,255,255,0.28);
}
"""


class FlowLayout(QLayout):
    """A layout that wraps its widgets onto as many rows as needed (like CSS flex-wrap).
    Used for the quick-action chips so they never overflow / get clipped off-screen."""

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._hspace = hspacing
        self._vspace = vspacing
        self._items: list = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_h = 0
        right = rect.right() - m.right()
        for item in self._items:
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            if x + w > right and line_h > 0:
                x = rect.x() + m.left()
                y = y + line_h + self._vspace
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += w + self._hspace
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


class EventBridge(QObject):
    """Marshals agent events from worker threads to the UI thread."""
    event = pyqtSignal(object)
    summon = pyqtSignal()
    transcript = pyqtSignal(str, str)
    remote_chat = pyqtSignal(str)
    chat_title = pyqtSignal(str, str)
    agent_ready = pyqtSignal()  # emitted from a worker thread once google.genai is imported
    update_available = pyqtSignal(object)  # emitted from the background update-check thread
    notice = pyqtSignal(str)  # post a one-line system bubble from a background thread


class MiniPill(QWidget):
    """Compact draggable widget shown when Ember is minimized. Click to restore."""
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(170, 40)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QFrame()
        root.setObjectName("pillRoot")
        outer.addWidget(root)

        row = QHBoxLayout(root)
        row.setContentsMargins(12, 4, 6, 4)
        row.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet("color: #7aa2f7; font-size: 16px;")
        row.addWidget(dot)
        label = QLabel("Ember")
        label.setStyleSheet("color: #c0caf5; font-weight: bold; font-size: 12px;")
        row.addWidget(label)
        row.addStretch()

        up = QPushButton("↑")
        up.setObjectName("titleBtn")
        up.setFixedSize(28, 28)
        up.setToolTip("Restore")
        up.clicked.connect(self._restore)
        row.addWidget(up)

        x = QPushButton("✕")
        x.setObjectName("closeBtn")
        x.setFixedSize(28, 28)
        x.setToolTip("Quit Ember")
        x.clicked.connect(self._quit)
        row.addWidget(x)

        self.setStyleSheet(STYLE)
        self._drag_pos = None
        self._press_pos = None

    def _restore(self):
        self.parent_window.show()
        self.parent_window.raise_()
        self.parent_window.activateWindow()
        self.hide()

    def _quit(self):
        self.parent_window.close()
        QApplication.instance().quit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._press_pos is not None:
                moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
                if moved < 5:
                    self._restore()
            self._drag_pos = None
            self._press_pos = None
            e.accept()


class ManualModeDialog(QDialog):
    """Human-in-the-loop fallback: build a prompt for an external LLM,
    paste the LLM's code back, and execute it."""

    def __init__(self, recent_chat: list[str], last_screen_summary: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual mode — bridge an external AI")
        self.setMinimumSize(700, 640)
        self._recent_chat = recent_chat
        self._last_screen_summary = last_screen_summary

        layout = QVBoxLayout(self)

        info = QLabel(
            "Use this when Ember's API is exhausted or you want a stronger model.\n"
            "1) Edit your request below.   2) Copy the prompt → paste into Claude.ai / ChatGPT.\n"
            "3) Paste the response code back below → Run."
        )
        info.setStyleSheet("color: #565f89; font-size: 11px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addWidget(QLabel("Your request:"))
        self.request_input = QPlainTextEdit()
        self.request_input.setPlaceholderText("e.g. organize my Downloads by type, but skip files modified today")
        self.request_input.setMaximumHeight(70)
        layout.addWidget(self.request_input)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Python", "PowerShell"])
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch()
        self.build_btn = QPushButton("Build prompt")
        self.build_btn.setObjectName("send")
        self.build_btn.clicked.connect(self._build_prompt)
        lang_row.addWidget(self.build_btn)
        layout.addLayout(lang_row)

        layout.addWidget(QLabel("Prompt for external AI (Copy → paste into Claude.ai / ChatGPT):"))
        self.prompt_view = QPlainTextEdit()
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setMaximumHeight(180)
        layout.addWidget(self.prompt_view)

        copy_row = QHBoxLayout()
        copy_btn = QPushButton("Copy prompt to clipboard")
        copy_btn.clicked.connect(self._copy_prompt)
        copy_row.addWidget(copy_btn)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        layout.addWidget(QLabel("Paste the AI's response code here:"))
        self.code_input = QPlainTextEdit()
        self.code_input.setPlaceholderText("Paste Python / PowerShell code returned by the external AI…")
        layout.addWidget(self.code_input, 1)

        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Run pasted code")
        self.run_btn.setObjectName("send")
        self.run_btn.clicked.connect(self._run_code)
        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        run_row.addStretch()
        run_row.addWidget(cancel_btn)
        run_row.addWidget(self.run_btn)
        layout.addLayout(run_row)

        layout.addWidget(QLabel("Output:"))
        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setMaximumHeight(150)
        layout.addWidget(self.output_view)

        self.setStyleSheet(STYLE)

    def _build_prompt(self):
        req = self.request_input.toPlainText().strip()
        if not req:
            QMessageBox.warning(self, "Empty", "Type the request first.")
            return
        prompt = manual_mod.build_prompt(
            user_request=req,
            recent_chat=self._recent_chat,
            screen_summary=self._last_screen_summary,
            language=self.lang_combo.currentText(),
        )
        self.prompt_view.setPlainText(prompt)
        QApplication.clipboard().setText(prompt)
        self.output_view.setPlainText("(prompt copied to clipboard - paste it into Claude.ai / ChatGPT)")

    def _copy_prompt(self):
        text = self.prompt_view.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "No prompt yet", "Click 'Build prompt' first.")
            return
        QApplication.clipboard().setText(text)
        self.output_view.appendPlainText("[prompt copied to clipboard]")

    def _run_code(self):
        code = self.code_input.toPlainText().strip()
        if not code:
            QMessageBox.warning(self, "Empty", "Paste code first.")
            return
        confirm = QMessageBox.question(
            self, "Execute code?",
            "Ember will run the pasted code with full system access.\n"
            "Only run code you trust. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.output_view.setPlainText("Running...\n")
        self.run_btn.setEnabled(False)
        QApplication.processEvents()
        lang = self.lang_combo.currentText()
        result = (manual_mod.run_powershell(code) if lang == "PowerShell"
                  else manual_mod.run_python(code))
        self.run_btn.setEnabled(True)
        lines = []
        if not result.get("ok"):
            lines.append(f"[FAILED]  {result.get('error') or 'returncode ' + str(result.get('returncode'))}")
        else:
            lines.append("[OK]  command finished")
        if result.get("stdout"):
            lines.append("\n--- stdout ---\n" + result["stdout"])
        if result.get("stderr"):
            lines.append("\n--- stderr ---\n" + result["stderr"])
        self.output_view.setPlainText("\n".join(lines))


class SettingsDialog(QDialog):
    """Tabbed settings: Models · Performance · Automations · Memory · Security · About."""

    def __init__(self, settings: dict, parent=None, automation_engine=None):
        super().__init__(parent)
        self.setWindowTitle("Ember Settings")
        self.setMinimumSize(820, 560)
        self.settings = dict(settings)
        self.automation_engine = automation_engine

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)
        tab_bar = self.tabs.tabBar()
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        tab_bar.setExpanding(False)
        outer.addWidget(self.tabs)

        self._build_models_tab()
        self._build_appearance_tab()
        self._build_voice_tab()
        self._build_performance_tab()
        self._build_automations_tab()
        self._build_memory_tab()
        self._build_security_tab()
        self._build_about_tab()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("send")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

        self.setStyleSheet(STYLE)

    def _build_models_tab(self):
        page = QWidget()
        layout = QFormLayout(page)

        self.gemini_key_input = QLineEdit(self.settings.get("gemini_api_key", ""))
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setPlaceholderText("Get free at aistudio.google.com/apikey")
        layout.addRow("Gemini API key:", self.gemini_key_input)

        self.gemini_key_secondary_input = QLineEdit(self.settings.get("gemini_api_key_secondary", ""))
        self.gemini_key_secondary_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_secondary_input.setPlaceholderText("Optional backup key for same-model failover")
        layout.addRow("Backup Gemini key:", self.gemini_key_secondary_input)

        self.dual_api_check = QCheckBox("If Gemini is rate-limited, retry the same model with the backup key")
        self.dual_api_check.setChecked(bool(self.settings.get("dual_api_failover", True)))
        layout.addRow(self.dual_api_check)

        self.ai_titles_check = QCheckBox("Generate chat titles with Gemma 3 27B")
        self.ai_titles_check.setChecked(bool(self.settings.get("ai_chat_titles", True)))
        layout.addRow(self.ai_titles_check)

        self.anthropic_key_input = QLineEdit(self.settings.get("anthropic_api_key", ""))
        self.anthropic_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key_input.setPlaceholderText("Required if Claude is the primary model")
        layout.addRow("Anthropic API key:", self.anthropic_key_input)

        self.model_combo = QComboBox()
        self._model_options = model_catalog.all_choices()
        current = self.settings.get("model_id") or self.settings.get("gemini_model") or "gemini-3.1-flash-lite"
        current_idx = 0
        for i, (_provider, mid, name, hint) in enumerate(self._model_options):
            self.model_combo.addItem(f"{name}  -  {hint}", userData=mid)
            if mid == current:
                current_idx = i
        self.model_combo.setCurrentIndex(current_idx)
        layout.addRow("Primary model:", self.model_combo)

        rate_btn = QPushButton("Show free-tier rate limits")
        rate_btn.clicked.connect(self._show_rates)
        layout.addRow("", rate_btn)

        info = QLabel(
            "Gemini 3.1 Flash Lite has the highest free-tier RPD (500/day).\n"
            "Gemma 4 models go to 1500 RPD but don't support tool-use - text only.\n"
            "Gemma 3 27B is used for short chat titles. Pick a Claude model to switch to Anthropic as the primary brain."
        )
        info.setStyleSheet("color: #565f89; font-size: 11px;")
        info.setWordWrap(True)
        layout.addRow(info)

        self.tabs.addTab(page, "Models")

    def _build_appearance_tab(self):
        page = QWidget()
        layout = QFormLayout(page)

        self.animations_check = QCheckBox("Enable bubble fade-in + typing animations")
        self.animations_check.setChecked(bool(self.settings.get("animations_enabled", True)))
        layout.addRow(self.animations_check)

        self.autocorrect_check = QCheckBox("Autocorrect ordinary chat prompts before sending")
        self.autocorrect_check.setChecked(bool(self.settings.get("autocorrect_chat", True)))
        layout.addRow(self.autocorrect_check)

        self.glow_check = QCheckBox("Blue glow around the window")
        self.glow_check.setChecked(bool(self.settings.get("glow_enabled", True)))
        layout.addRow(self.glow_check)

        self.font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_size_slider.setRange(10, 18)
        self.font_size_slider.setValue(int(self.settings.get("font_size", 12)))
        self.font_size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.font_size_slider.setTickInterval(1)
        self.font_size_value = QLabel(f"{self.font_size_slider.value()} px")
        self.font_size_slider.valueChanged.connect(
            lambda v: self.font_size_value.setText(f"{v} px")
        )
        row = QHBoxLayout()
        row.addWidget(self.font_size_slider, 1)
        row.addWidget(self.font_size_value)
        wrap = QWidget()
        wrap.setLayout(row)
        layout.addRow("Chat text size:", wrap)

        self.liquid_glass_check = QCheckBox("Liquid Glass - neutral translucent UI, no colored glass tint")
        self.liquid_glass_check.setChecked(bool(self.settings.get("liquid_glass", False)))
        layout.addRow(self.liquid_glass_check)

        self.glass_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.glass_opacity_slider.setRange(20, 95)
        self.glass_opacity_slider.setValue(int(self.settings.get("glass_opacity", 75)))

        def _blur_word(v):
            return "Light" if v < 40 else ("Medium" if v < 70 else "Strong")
        self.glass_opacity_value = QLabel(_blur_word(self.glass_opacity_slider.value()))
        self.glass_opacity_slider.valueChanged.connect(
            lambda v: self.glass_opacity_value.setText(_blur_word(v))
        )
        row_g = QHBoxLayout()
        row_g.addWidget(QLabel("See-through:"))
        row_g.addWidget(self.glass_opacity_slider, 1)
        row_g.addWidget(self.glass_opacity_value)
        wrap_g = QWidget()
        wrap_g.setLayout(row_g)
        layout.addRow("  Glass opacity:", wrap_g)

        self.accent_combo = QComboBox()
        accents = [
            ("Blue (default)",   "#7aa2f7"),
            ("Purple",           "#bb9af7"),
            ("Cyan",             "#7dcfff"),
            ("Mint",             "#9ece6a"),
            ("Pink",             "#f7768e"),
            ("Amber",            "#e0af68"),
        ]
        cur = self.settings.get("accent_color", "#7aa2f7")
        idx = 0
        for i, (name, color) in enumerate(accents):
            self.accent_combo.addItem(name, userData=color)
            if color == cur:
                idx = i
        self.accent_combo.setCurrentIndex(idx)
        layout.addRow("Accent color:", self.accent_combo)

        note = QLabel(
            "Appearance changes apply when you save. Restart Ember via Ember.bat to fully refresh."
        )
        note.setStyleSheet("color: #565f89; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)

        self.tabs.addTab(page, "Appearance")

    def _build_voice_tab(self):
        page = QWidget()
        layout = QFormLayout(page)

        self.voice_check = QCheckBox("Speak assistant replies aloud")
        self.voice_check.setChecked(bool(self.settings.get("voice_output", False)))
        layout.addRow(self.voice_check)

        self.voice_chat_reply_check = QCheckBox("Voice chat always speaks replies")
        self.voice_chat_reply_check.setChecked(bool(self.settings.get("voice_chat_spoken_replies", True)))
        layout.addRow(self.voice_chat_reply_check)

        self.voice_auto_send_check = QCheckBox("Send each voice-chat transcript automatically")
        self.voice_auto_send_check.setChecked(bool(self.settings.get("voice_chat_auto_send", True)))
        layout.addRow(self.voice_auto_send_check)

        self.voice_continue_check = QCheckBox("Keep listening after silence or unclear audio")
        self.voice_continue_check.setChecked(bool(self.settings.get("voice_chat_continue_after_silence", True)))
        layout.addRow(self.voice_continue_check)

        self.voice_phrase_input = QLineEdit(str(self.settings.get("voice_chat_phrase_timeout", 8)))
        self.voice_phrase_input.setPlaceholderText("seconds, 4-20")
        layout.addRow("Voice turn length:", self.voice_phrase_input)

        note = QLabel(
            "Voice chat uses the same computer-control brain as typed chat. Microphone permission is required."
        )
        note.setStyleSheet("color: #565f89; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)

        self.tabs.addTab(page, "Voice")

    def _build_performance_tab(self):
        page = QWidget()
        layout = QFormLayout(page)

        self.auto_shot_check = QCheckBox("Auto-attach screenshot when message mentions the screen")
        self.auto_shot_check.setChecked(bool(self.settings.get("auto_screenshot", True)))
        layout.addRow(self.auto_shot_check)

        self.remote_autostart_check = QCheckBox(
            "Start Ember Link (phone control) automatically when Ember opens")
        self.remote_autostart_check.setChecked(bool(self.settings.get("remote_autostart", True)))
        layout.addRow(self.remote_autostart_check)

        self.auto_update_check = QCheckBox(
            "Automatically check for Ember updates on launch")
        self.auto_update_check.setChecked(bool(self.settings.get("auto_update", True)))
        layout.addRow(self.auto_update_check)

        self.hotkey_input = QLineEdit(self.settings.get("hotkey", "ctrl+shift+space"))
        self.hotkey_input.setPlaceholderText("e.g. ctrl+alt+a, ctrl+shift+space")
        layout.addRow("Global summon hotkey:", self.hotkey_input)

        self.timeout_input = QLineEdit(str(self.settings.get("request_timeout_seconds", 15)))
        self.timeout_input.setPlaceholderText("seconds, 10-60 (lower = faster failover)")
        layout.addRow("API request timeout (s):", self.timeout_input)

        # Show whether the parent window successfully registered the hotkey
        parent_w = self.parent()
        status_text = getattr(parent_w, "_hotkey_status", "unknown")
        self.hotkey_status_label = QLabel(f"Hotkey status: <b>{status_text}</b>")
        self.hotkey_status_label.setTextFormat(Qt.TextFormat.RichText)
        self.hotkey_status_label.setStyleSheet("font-size: 11px;")
        layout.addRow(self.hotkey_status_label)

        note = QLabel(
            "Hotkey changes apply immediately when you Save (no restart needed).\n"
            "If status shows 'failed', another app may have claimed the same combo "
            "(common: Ctrl+Shift+D = Chrome Bookmarks). Try Ctrl+Alt+A or Ctrl+Shift+Space."
        )
        note.setStyleSheet("color: #565f89; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)
        self.tabs.addTab(page, "Performance")

    def _build_automations_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)

        head = QLabel(
            "Background rules. When the trigger fires, Ember runs the action automatically - "
            "no API calls needed. Edit automations.json next to the exe for advanced tweaks."
        )
        head.setStyleSheet("color: #565f89; font-size: 11px;")
        head.setWordWrap(True)
        v.addWidget(head)

        self.auto_master_check = QCheckBox("Automation engine enabled")
        self.auto_master_check.setChecked(bool(self.settings.get("automation_enabled", True)))
        v.addWidget(self.auto_master_check)

        self.auto_confirm_check = QCheckBox(
            "Auto-confirm popups (presses Enter on new dialogs containing "
            "'confirm', 'are you sure', 'save changes', 'delete', 'warning', etc.)"
        )
        self.auto_confirm_check.setChecked(bool(self.settings.get("auto_confirm_popups", False)))
        v.addWidget(self.auto_confirm_check)

        self.auto_list = QListWidget()
        self._reload_automation_list()
        v.addWidget(self.auto_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("Add rule")
        add_btn.clicked.connect(self._add_automation)
        toggle_btn = QPushButton("Toggle on/off")
        toggle_btn.clicked.connect(self._toggle_automation)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_automation)
        for b in (add_btn, toggle_btn, del_btn):
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)

        self.tabs.addTab(page, "Automations")

    def _reload_automation_list(self):
        if not hasattr(self, "auto_list"):
            return
        self.auto_list.clear()
        rules = self.automation_engine.rules if self.automation_engine else automation_mod.load_rules()
        for r in rules:
            mark = "[ON ]" if r.get("enabled") else "[off]"
            trig = r.get("trigger") or {}
            act = r.get("action") or {}
            title = trig.get("title_contains", "")
            atype = act.get("type", "?")
            akey = (act.get("args") or {}).get("keys", "")
            label = f'{mark}  {r.get("name", "(unnamed)")}  - when "{title}" appears -> {atype}({akey})'
            self.auto_list.addItem(QListWidgetItem(label))

    def _selected_rule_index(self) -> int:
        return self.auto_list.currentRow()

    def _toggle_automation(self):
        i = self._selected_rule_index()
        if i < 0:
            return
        if self.automation_engine:
            self.automation_engine.toggle_rule(i)
        else:
            rules = automation_mod.load_rules()
            if 0 <= i < len(rules):
                rules[i]["enabled"] = not rules[i].get("enabled", False)
                automation_mod.save_rules(rules)
        self._reload_automation_list()

    def _delete_automation(self):
        i = self._selected_rule_index()
        if i < 0:
            return
        if self.automation_engine:
            self.automation_engine.remove_rule(i)
        else:
            rules = automation_mod.load_rules()
            if 0 <= i < len(rules):
                rules.pop(i)
                automation_mod.save_rules(rules)
        self._reload_automation_list()

    def _add_automation(self):
        name, ok = QInputDialog.getText(self, "New rule", "Rule name:")
        if not ok or not name.strip():
            return
        title, ok = QInputDialog.getText(self, "Trigger",
            "Window title contains (substring match, case-insensitive):")
        if not ok or not title.strip():
            return
        keys, ok = QInputDialog.getText(self, "Action",
            "Keys to press when triggered (e.g. enter, alt+y, esc):", text="enter")
        if not ok or not keys.strip():
            return
        rule = {
            "name": name.strip(),
            "trigger": {"type": "new_window", "title_contains": title.strip()},
            "action": {"type": "press_key", "args": {"keys": keys.strip()}},
            "enabled": True,
        }
        if self.automation_engine:
            self.automation_engine.add_rule(rule)
        else:
            rules = automation_mod.load_rules()
            rules.append(rule)
            automation_mod.save_rules(rules)
        self._reload_automation_list()

    def _build_memory_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        head = QLabel("Facts Ember has remembered about your system / preferences.")
        head.setStyleSheet("color: #565f89; font-size: 11px;")
        v.addWidget(head)
        import memory
        facts = memory._load().get("facts", {})
        view = QPlainTextEdit()
        view.setReadOnly(True)
        if facts:
            lines = []
            for k, v_ in facts.items():
                val = v_.get("value", "") if isinstance(v_, dict) else str(v_)
                lines.append(f"{k} = {val}")
            view.setPlainText("\n".join(lines))
        else:
            view.setPlainText("(no facts saved yet — Ember adds them as it works)")
        v.addWidget(view, 1)
        clear_btn = QPushButton("Forget all facts")
        clear_btn.clicked.connect(self._forget_all)
        v.addWidget(clear_btn)
        self.tabs.addTab(page, "Memory")

    def _forget_all(self):
        import memory
        n = memory.forget_all().get("forgot_count", 0)  # locked + atomic
        QMessageBox.information(self, "Cleared", f"Forgot {n} facts.")
        self._build_memory_tab()  # rebuild tab; cheap

    def _build_about_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        _hk = (self.settings.get("hotkey") or "ctrl+shift+space").title()
        _diag = "Windows diagnostics" if sys.platform.startswith("win") else "system diagnostics"
        try:
            import version as _v
            _ver = _v.__version__
        except Exception:
            _ver = "?"
        text = QLabel(
            f"<b>Ember</b> v{_ver} — AI agent for your computer.<br><br>"
            "Capabilities: hands-free voice chat, vision + mouse/keyboard control, DOM-driven browser, file organization, "
            f"{_diag}, background automations, voice in/out, persistent memory, "
            "phone remote control, and Claude fallback for hard reasoning.<br><br>"
            f"Hotkey: <b>{_hk}</b> summons from anywhere.<br>"
            "Drop files into the chat to discuss them.<br>"
            "Voice Chat runs continuous listen → act → speak turns."
        )
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        v.addWidget(text)
        v.addStretch()
        self.tabs.addTab(page, "About")

    def _build_security_tab(self):
        """Security & Pro: malware protection, web protection, agent mode, VPN, audit."""
        page = QWidget()
        v = QVBoxLayout(page)
        try:
            self._populate_security_tab(v)
        except Exception as e:
            v.addWidget(QLabel(f"Security panel unavailable: {e}"))
        self.tabs.addTab(page, "Security")

    def _populate_security_tab(self, v):
        import antivirus, web_policy, safety, plan, audit, vpn

        p = plan.get_plan()
        plan_lbl = QLabel(
            "<b>Plan:</b> " + ("Pro ✓ — all features unlocked (free for everyone)"
                               if p.get("is_pro") else f"{p.get('plan')}"))
        plan_lbl.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(plan_lbl)

        def _section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#565f89; font-size:11px; margin-top:8px;")
            v.addWidget(lbl)

        # --- Malware protection ---
        _section("Malware protection")
        cfg = antivirus.get_config()
        self._sec_scan_dl = QCheckBox("Scan files on download")
        self._sec_scan_dl.setChecked(bool(cfg.get("scan_downloads", True)))
        self._sec_scan_dl.stateChanged.connect(
            lambda s: antivirus.set_config(scan_downloads=bool(s)))
        v.addWidget(self._sec_scan_dl)
        self._sec_scan_open = QCheckBox("Scan files before opening them")
        self._sec_scan_open.setChecked(bool(cfg.get("scan_before_open", True)))
        self._sec_scan_open.stateChanged.connect(
            lambda s: antivirus.set_config(scan_before_open=bool(s)))
        v.addWidget(self._sec_scan_open)

        st = antivirus.security_status()
        eng = QLabel("Engines: " + ", ".join(st.get("engines_available", []))
                     + f"   ·   Sandbox: {st.get('sandbox_available')}")
        eng.setStyleSheet("color:#565f89; font-size:11px;")
        eng.setWordWrap(True)
        v.addWidget(eng)

        row = QHBoxLayout()
        self._sec_quar_lbl = QLabel(f"Quarantine: {st.get('quarantine_count', 0)} item(s)")
        row.addWidget(self._sec_quar_lbl)
        view_btn = QPushButton("View quarantine")
        view_btn.clicked.connect(self._show_quarantine)
        row.addWidget(view_btn)
        scan_btn = QPushButton("Scan a folder…")
        scan_btn.clicked.connect(self._scan_folder)
        row.addWidget(scan_btn)
        row.addStretch()
        v.addLayout(row)

        # --- Web protection ---
        _section("Web protection")
        wp = web_policy.get_config()
        self._sec_web = QCheckBox("Block malicious / phishing websites")
        self._sec_web.setChecked(bool(wp.get("enabled", True)))
        self._sec_web.stateChanged.connect(lambda s: web_policy.set_config(enabled=bool(s)))
        v.addWidget(self._sec_web)
        self._sec_web_rep = QCheckBox("Use live URL reputation (URLhaus / VirusTotal / Safe Browsing)")
        self._sec_web_rep.setChecked(bool(wp.get("online_reputation", True)))
        self._sec_web_rep.stateChanged.connect(
            lambda s: web_policy.set_config(online_reputation=bool(s)))
        v.addWidget(self._sec_web_rep)

        # --- Agent capability mode ---
        _section("Agent capability mode")
        mrow = QHBoxLayout()
        self._sec_mode = QComboBox()
        self._sec_mode.addItems(["full", "restricted", "read_only"])
        try:
            self._sec_mode.setCurrentText(safety.current_mode())
        except Exception:
            pass
        self._sec_mode.currentTextChanged.connect(lambda m: safety.set_mode(m))
        mrow.addWidget(self._sec_mode)
        mrow.addStretch()
        v.addLayout(mrow)
        mhint = QLabel("full = all tools · restricted = no high-risk actions · "
                       "read_only = safe read-only tools only")
        mhint.setStyleSheet("color:#565f89; font-size:11px;")
        mhint.setWordWrap(True)
        v.addWidget(mhint)

        # --- VPN ---
        _section("VPN (bring-your-own WireGuard)")
        try:
            vs = vpn.status()
            vl = vpn.list_locations()
            vtxt = ("Connected ✓" if vs.get("connected") else "Not connected")
            vtxt += f"   ·   {len(vl.get('locations', []))} location(s)"
            if not vl.get("wireguard_installed"):
                vtxt += "   ·   WireGuard not installed (brew install wireguard-tools)"
            vlbl = QLabel(vtxt)
            vlbl.setStyleSheet("color:#565f89; font-size:11px;")
            vlbl.setWordWrap(True)
            v.addWidget(vlbl)
        except Exception:
            pass

        # --- Audit log ---
        _section("Tamper-evident audit log")
        arow = QHBoxLayout()
        averify = QPushButton("Verify audit log")
        averify.clicked.connect(self._verify_audit)
        arow.addWidget(averify)
        arow.addStretch()
        v.addLayout(arow)
        v.addStretch()

    def _show_quarantine(self):
        import antivirus
        items = antivirus.list_quarantine().get("items", [])
        if not items:
            QMessageBox.information(self, "Quarantine", "Quarantine is empty.")
            return
        blocks = []
        for it in items[:30]:
            reasons = ", ".join(it.get("reasons", []) or []) or "—"
            blocks.append(f"{it.get('original_path')}\n  {reasons}\n  auto-deletes: {it.get('deletes_on')}")
        QMessageBox.information(self, "Quarantine", "\n\n".join(blocks))

    def _scan_folder(self):
        import antivirus
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if not folder:
            return
        r = antivirus.scan_directory(folder, deep=False)
        if not r.get("ok"):
            QMessageBox.warning(self, "Scan", r.get("error", "scan failed"))
            return
        flagged = r.get("flagged", [])
        detail = "\n".join(f"{f['verdict']}: {f['path']}" for f in flagged[:20]) or "Nothing suspicious found."
        QMessageBox.information(
            self, "Scan complete",
            f"Scanned {r.get('scanned', 0)} files — flagged {r.get('flagged_count', 0)}.\n\n{detail}")
        try:
            self._sec_quar_lbl.setText(
                f"Quarantine: {antivirus.security_status().get('quarantine_count', 0)} item(s)")
        except Exception:
            pass

    def _verify_audit(self):
        import audit
        r = audit.verify()
        if r.get("valid"):
            QMessageBox.information(self, "Audit log",
                                    f"✓ Intact — {r.get('entries', 0)} entries, no tampering detected.")
        else:
            QMessageBox.warning(self, "Audit log",
                                f"⚠ Tampering detected at entry {r.get('broken_at')}: {r.get('reason')}")

    def _show_rates(self):
        QMessageBox.information(self, "Free-tier rate limits", model_catalog.rate_limit_summary())

    def get_settings(self) -> dict:
        self.settings["gemini_api_key"] = self.gemini_key_input.text().strip()
        self.settings["gemini_api_key_secondary"] = self.gemini_key_secondary_input.text().strip()
        self.settings["dual_api_failover"] = self.dual_api_check.isChecked()
        self.settings["ai_chat_titles"] = self.ai_titles_check.isChecked()
        self.settings["anthropic_api_key"] = self.anthropic_key_input.text().strip()
        sel_id = self.model_combo.currentData()
        self.settings["model_id"] = sel_id or "gemini-3.1-flash-lite"
        provider = model_catalog.provider_for(sel_id)
        self.settings["provider"] = provider
        if provider == "gemini":
            self.settings["gemini_model"] = sel_id
        else:
            self.settings["anthropic_model"] = sel_id
        self.settings["auto_screenshot"] = self.auto_shot_check.isChecked()
        self.settings["remote_autostart"] = self.remote_autostart_check.isChecked()
        self.settings["auto_update"] = self.auto_update_check.isChecked()
        self.settings["voice_output"] = self.voice_check.isChecked()
        self.settings["voice_chat_spoken_replies"] = self.voice_chat_reply_check.isChecked()
        self.settings["voice_chat_auto_send"] = self.voice_auto_send_check.isChecked()
        self.settings["voice_chat_continue_after_silence"] = self.voice_continue_check.isChecked()
        try:
            self.settings["voice_chat_phrase_timeout"] = max(4, min(20, int(self.voice_phrase_input.text().strip() or 8)))
        except ValueError:
            self.settings["voice_chat_phrase_timeout"] = 8
        self.settings["hotkey"] = self.hotkey_input.text().strip() or "ctrl+shift+space"
        try:
            self.settings["request_timeout_seconds"] = max(10, min(60, int(self.timeout_input.text().strip() or 15)))
        except ValueError:
            self.settings["request_timeout_seconds"] = 15
        self.settings["automation_enabled"] = self.auto_master_check.isChecked()
        self.settings["auto_confirm_popups"] = self.auto_confirm_check.isChecked()
        # Appearance tab
        if hasattr(self, "animations_check"):
            self.settings["animations_enabled"] = self.animations_check.isChecked()
            self.settings["autocorrect_chat"] = self.autocorrect_check.isChecked()
            self.settings["glow_enabled"] = self.glow_check.isChecked()
            self.settings["font_size"] = int(self.font_size_slider.value())
            self.settings["accent_color"] = self.accent_combo.currentData() or "#7aa2f7"
            self.settings["liquid_glass"] = self.liquid_glass_check.isChecked()
            self.settings["glass_opacity"] = int(self.glass_opacity_slider.value())
        return self.settings


class ClaudeHandoffDialog(QDialog):
    def __init__(self, pending: PendingClaudeResponse, parent=None):
        super().__init__(parent)
        self.pending = pending
        self.setWindowTitle("Gemini is consulting Claude")
        self.setMinimumSize(640, 540)
        layout = QVBoxLayout(self)

        info = QLabel("Gemini is stuck and prepared this prompt for Claude. The prompt is already on your clipboard.\n"
                      "Paste it into Claude.ai (or any Claude chat), then paste Claude's reply below.")
        info.setWordWrap(True)
        layout.addWidget(info)

        prompt_label = QLabel("Prompt (already copied to clipboard):")
        layout.addWidget(prompt_label)
        prompt_view = QPlainTextEdit(pending.handoff_prompt)
        prompt_view.setReadOnly(True)
        prompt_view.setMaximumHeight(220)
        layout.addWidget(prompt_view)

        copy_btn = QPushButton("Copy prompt again")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(pending.handoff_prompt))
        layout.addWidget(copy_btn)

        layout.addWidget(QLabel("Paste Claude's reply here:"))
        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("Paste Claude's response, then click Send back to Gemini")
        layout.addWidget(self.reply_input)

        btn_row = QHBoxLayout()
        send_btn = QPushButton("Send back to Gemini")
        send_btn.setObjectName("send")
        send_btn.clicked.connect(self._send)
        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(self._skip)
        btn_row.addStretch()
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(send_btn)
        layout.addLayout(btn_row)

        self.setStyleSheet(STYLE)

    def _send(self):
        text = self.reply_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty", "Paste Claude's reply first.")
            return
        self.pending.response.put(text)
        self.accept()

    def _skip(self):
        self.pending.response.put("")
        self.reject()


class EmberWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.chat_history = load_chat_history()
        self.active_chat_id = self.chat_history.get("active_id")
        self._history_loading = False
        self.agent: Agent | None = None
        self._drag_pos: QPoint | None = None
        self._bridge = EventBridge()
        self._bridge.event.connect(self._on_event_main_thread)
        self._bridge.summon.connect(self._summon)
        self._bridge.transcript.connect(self._on_transcript)
        self._bridge.remote_chat.connect(self._on_remote_chat)
        self._bridge.chat_title.connect(self._on_chat_title)
        self._bridge.agent_ready.connect(self._build_agent)
        self._bridge.update_available.connect(self._on_update_available)
        self._bridge.notice.connect(lambda m: self._add_bubble("system", m))
        self._pending_update: dict | None = None
        # macOS never auto-prompts for Accessibility — explicitly request it shortly after launch.
        if not _SAFE_MODE:
            QTimer.singleShot(900, self._check_accessibility)
        self._listening = False
        self._voice_chat_enabled = False
        self._voice_waiting_for_reply = False
        self._voice_turns = 0
        self._title_jobs: set[str] = set()
        self._build_ui()
        self._restore_position()
        if not _SAFE_MODE:
            self._install_hotkey()
        self._overlay_timer = QTimer(self)
        self._overlay_timer.timeout.connect(self._keep_overlay_on_top)
        self._overlay_timer.start(2500)
        self.setAcceptDrops(True)
        self._automation = automation_mod.AutomationEngine(on_fire=self._on_automation_fire)
        self._automation.enabled = bool(self.settings.get("automation_enabled", True))
        self._automation.auto_confirm_popups = bool(self.settings.get("auto_confirm_popups", False))
        self._automation.start()
        if self.settings.get("remote_autostart", True) and not _SAFE_MODE:
            # Bring Ember Link (phone control) up as soon as the app opens — deferred a beat
            # so the window paints first. Starts silently; no modal on launch.
            QTimer.singleShot(1200, self._autostart_remote_control)
        if self.settings.get("auto_update", True):
            # Auto-update on every launch. Frozen .app: check + auto-install a published
            # release. Git/source checkout: fast-forward to the latest commit. Both are
            # non-blocking and failure-silent.
            QTimer.singleShot(5000, self._check_for_update_async)
            QTimer.singleShot(1500, self._git_self_update)
        if self.settings.get("gemini_api_key"):
            # Defer agent init (and the heavy google.genai import) so the window paints first.
            self._set_status("Starting…")
            QTimer.singleShot(0, self._init_agent)
        else:
            QTimer.singleShot(200, self._first_run_settings)

    def _on_automation_fire(self, rule_name: str, trigger: dict, action: dict):
        # Marshal to UI thread via a queued call.
        QTimer.singleShot(0, lambda: self._add_bubble(
            "system",
            f"⚡ automation '{rule_name}' fired: {action.get('type')}({(action.get('args') or {}).get('keys', '')})",
        ))

    def _uninstall_hotkey(self):
        """Remove any previously-registered hotkey so we can change combo at runtime."""
        try:
            import keyboard
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        listener = getattr(self, "_pynput_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
            self._pynput_listener = None
        self._hotkey_combo = None
        self._hotkey_status = "off"

    def _install_hotkey(self):
        """Register the global summon hotkey. Tries pynput first (more reliable on Windows
        without admin), then falls back to `keyboard`. Stores status for the UI to display."""
        self._uninstall_hotkey()
        combo = (self.settings.get("hotkey") or "ctrl+shift+space").lower().strip()
        errors = []

        # On macOS, a global key listener is a Quartz CGEventTap. Creating one while the
        # process is NOT trusted for Accessibility prints "This process is not trusted!"
        # and can hard-crash the app (segfault) under Python 3.12. So only start it once
        # access is actually granted; _check_accessibility re-calls this after a grant.
        if sys.platform == "darwin":
            try:
                import mac_permissions
                if not mac_permissions.request_accessibility(prompt=False):
                    self._hotkey_combo = None
                    self._hotkey_status = "off (grant Accessibility to enable the global hotkey)"
                    print(f"[hotkey] {self._hotkey_status}")
                    return
            except Exception:
                pass

        # Attempt 1: pynput (works reliably without admin on most Windows installs).
        try:
            from pynput import keyboard as pk
            _is_mac = sys.platform == "darwin"
            _norm = []
            for k in combo.split("+"):
                if _is_mac and k in ("win", "super", "meta"):
                    k = "cmd"
                # pynput needs every named (multi-char) key bracketed: <ctrl>, <shift>, <space>, <enter>…
                _norm.append(f"<{k}>" if len(k) > 1 else k)
            pyn_combo = "+".join(_norm)
            self._pynput_listener = pk.GlobalHotKeys({pyn_combo: lambda: self._bridge.summon.emit()})
            self._pynput_listener.start()
            self._hotkey_combo = combo
            self._hotkey_status = f"on ({combo}, pynput)"
            return
        except Exception as e:
            errors.append(f"pynput: {e}")
            self._pynput_listener = None

        # Attempt 2: keyboard library
        try:
            import keyboard
            keyboard.add_hotkey(combo, lambda: self._bridge.summon.emit(), suppress=False)
            self._hotkey_combo = combo
            self._hotkey_status = f"on ({combo}, keyboard)"
            return
        except Exception as e:
            errors.append(f"keyboard: {e}")

        self._hotkey_combo = None
        self._hotkey_status = "failed: " + " | ".join(errors)
        print(f"[hotkey unavailable] {self._hotkey_status}")
        # Surface to the user so they don't wonder
        try:
            if sys.platform == "darwin":
                self._set_status("Hotkey failed — grant Ember Input Monitoring + Accessibility "
                                 "in System Settings ▸ Privacy & Security")
            else:
                self._set_status("Hotkey registration failed — try running Ember as Administrator")
        except Exception:
            pass

    def _summon(self):
        if hasattr(self, "_pill") and self._pill is not None and self._pill.isVisible():
            self._pill.hide()
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.input_box.setFocus()

    def _keep_overlay_on_top(self):
        # The main window is a stay-on-top overlay we periodically re-raise so it stays above
        # other apps. But don't fight our OWN dialogs/popups: raising over a modal Settings
        # (or a confirm box, menu, or combo dropdown) sends it behind, so it appears to flicker
        # back and forth. Skip the raise whenever one of those is active.
        app = QApplication.instance()
        if app is not None and (app.activeModalWidget() is not None
                                or app.activePopupWidget() is not None):
            return
        if self.isVisible() and not self.isMinimized():
            try:
                self.raise_()
            except Exception:
                pass

    def _open_upload_dialog(self):
        """File picker - the chosen paths get attached to the user's next message
        the same way drag-and-drop does."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Upload files for Ember",
            str(Path.home()),
            "All files (*);;Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;"
            "Documents (*.pdf *.docx *.txt *.md *.csv *.json);;Spreadsheets (*.xlsx *.csv)",
        )
        if not paths:
            return
        listing = "\n".join(f"- {p}" for p in paths[:20])
        existing = self.input_box.toPlainText().strip()
        prefix = (existing + "\n\n") if existing else ""
        self.input_box.setPlainText(
            f"{prefix}I'm uploading these files for you to read / discuss:\n{listing}\n\n"
        )
        cursor = self.input_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_box.setTextCursor(cursor)
        self.input_box.setFocus()
        self._set_status(f"{len(paths)} file(s) attached")

    def _toggle_mic(self):
        if self._voice_chat_enabled:
            self._stop_voice_chat("Voice chat off")
            return
        if self._listening:
            return
        self._start_voice_listen(mode="dictation")

    def _toggle_voice_chat(self):
        if self._voice_chat_enabled:
            self._stop_voice_chat("Voice chat off")
            return
        if not self.agent:
            QMessageBox.warning(self, "No API key", "Open settings (gear) and add your Gemini API key first.")
            self._open_settings()
            return
        try:
            import voice
        except ImportError:
            QMessageBox.warning(self, "Voice not available",
                "Voice deps missing. Run: pip install SpeechRecognition pyttsx3 pyaudio")
            return
        self._voice_chat_enabled = True
        self._voice_waiting_for_reply = False
        self._voice_turns = 0
        self._update_voice_chat_ui("Listening")
        self._add_bubble("system", "Voice chat is on. Say **stop voice chat** to turn it off.")
        self._start_voice_listen(mode="voice_chat")

    def _stop_voice_chat(self, status: str = "Voice chat off"):
        self._voice_chat_enabled = False
        self._voice_waiting_for_reply = False
        if self._listening and getattr(self, "_listening_mode", "") == "voice_chat":
            self._ignore_transcript_once = True
        try:
            import voice
            voice.stop_speaking()
        except Exception:
            pass
        self._update_voice_chat_ui("Voice idle")
        self._set_status(status)

    def _update_voice_chat_ui(self, hint: str | None = None):
        btn = getattr(self, "voice_chat_btn", None)
        if btn is not None:
            if self._voice_chat_enabled and self._listening:
                btn.setText("Listening")
            elif self._voice_chat_enabled:
                btn.setText("Voice On")
            else:
                btn.setText("Voice Chat")
            btn.setObjectName("voiceToggleOn" if self._voice_chat_enabled else "voiceToggle")
            btn.setToolTip("Turn off hands-free voice chat" if self._voice_chat_enabled else "Toggle hands-free voice conversation")
            try:
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            except Exception:
                pass
        label = getattr(self, "voice_status_label", None)
        if label is not None:
            if hint:
                label.setText(hint)
            elif self._voice_chat_enabled and self._voice_waiting_for_reply:
                label.setText(f"Waiting for reply · {self._voice_turns} turn(s)")
            elif self._voice_chat_enabled:
                label.setText(f"Voice chat live · {self._voice_turns} turn(s)")
            else:
                label.setText("Voice idle")

    def _start_voice_listen(self, mode: str = "voice_chat"):
        if self._listening:
            return
        try:
            import voice
        except ImportError:
            QMessageBox.warning(self, "Voice not available",
                "Voice deps missing. Run: pip install SpeechRecognition pyttsx3 pyaudio")
            return
        if mode == "voice_chat" and not self._voice_chat_enabled:
            return
        self._listening = True
        self._listening_mode = mode
        self.mic_btn.setText("●")
        self.mic_btn.setStyleSheet("color: #f7768e;")
        self.mic_btn.setToolTip("Listening... speak now")
        if mode == "voice_chat":
            self._set_status("Voice chat listening...")
            self._update_voice_chat_ui("Listening now")
        else:
            self._set_status("Listening...")
        def _cb(text, err):
            self._bridge.transcript.emit(text or "", err or "")
        phrase_timeout = 6.0
        if mode == "voice_chat":
            try:
                phrase_timeout = float(self.settings.get("voice_chat_phrase_timeout", 8))
            except Exception:
                phrase_timeout = 8.0
        voice.listen_once(_cb, phrase_timeout=phrase_timeout, listen_timeout=max(8.0, phrase_timeout + 2.0))

    def _on_transcript(self, text: str, err: str):
        mode = getattr(self, "_listening_mode", "dictation")
        self._listening_mode = None
        self._listening = False
        self.mic_btn.setText("◉")
        self.mic_btn.setStyleSheet("")
        self.mic_btn.setToolTip("Click to dictate - auto-stops on silence")
        if getattr(self, "_ignore_transcript_once", False):
            self._ignore_transcript_once = False
            self._update_voice_chat_ui("Voice idle")
            return
        if mode == "voice_chat":
            self._handle_voice_chat_transcript(text, err)
            return
        if err:
            self._set_status(f"Mic: {err}")
            return
        if not text:
            self._set_status("No speech captured")
            return
        existing = self.input_box.toPlainText()
        joined = (existing + " " + text).strip() if existing else text
        self.input_box.setPlainText(joined)
        cursor = self.input_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_box.setTextCursor(cursor)
        self._set_status("Transcribed")

    def _handle_voice_chat_transcript(self, text: str, err: str):
        if not self._voice_chat_enabled:
            self._update_voice_chat_ui("Voice idle")
            return
        if err:
            self._set_status(f"Voice chat: {err}")
            self._update_voice_chat_ui(err[:48])
            if self.settings.get("voice_chat_continue_after_silence", True):
                QTimer.singleShot(850, lambda: self._start_voice_listen(mode="voice_chat"))
            else:
                self._stop_voice_chat("Voice chat paused")
            return
        text = (text or "").strip()
        if not text:
            self._update_voice_chat_ui("No speech captured")
            QTimer.singleShot(850, lambda: self._start_voice_listen(mode="voice_chat"))
            return
        if text.lower() in {"stop voice chat", "stop listening", "voice off", "cancel voice", "cancel voice chat"}:
            self._stop_voice_chat("Voice chat off")
            return
        if not self.agent:
            self._stop_voice_chat("Need API key for voice chat")
            self._open_settings()
            return
        if not self.settings.get("voice_chat_auto_send", True):
            existing = self.input_box.toPlainText()
            joined = (existing + " " + text).strip() if existing else text
            self.input_box.setPlainText(joined)
            cursor = self.input_box.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input_box.setTextCursor(cursor)
            self._voice_waiting_for_reply = False
            self._update_voice_chat_ui("Transcript ready")
            return
        self._voice_turns += 1
        self._voice_waiting_for_reply = True
        self._update_voice_chat_ui("Sending voice turn")
        if not self._submit_user_text(text, meta="voice chat", status="Voice command..."):
            self._voice_waiting_for_reply = False
            self._update_voice_chat_ui("Voice idle")

    def _to_pill(self):
        if not hasattr(self, "_pill") or self._pill is None:
            self._pill = MiniPill(self)
        screen = QApplication.primaryScreen().availableGeometry()
        self._pill.move(screen.right() - self._pill.width() - 12,
                        screen.bottom() - self._pill.height() - 12)
        self._normal_geometry = self.geometry()
        self._pill.show()
        self._pill.raise_()
        self.hide()

    def _apply_glow(self):
        """Apply or remove the soft accent glow around the window — live, from settings."""
        root = getattr(self, "_root", None)
        if root is None:
            return
        if self.settings.get("glow_enabled", True):
            if self.settings.get("liquid_glass", False):
                col = QColor(255, 255, 255)
                alpha = 82
            else:
                accent = self.settings.get("accent_color", "#6c9eff")
                col = QColor(accent)
                alpha = 150
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(34 if self.settings.get("liquid_glass", False) else 30)
            glow.setColor(QColor(col.red(), col.green(), col.blue(), alpha))
            glow.setOffset(0, 0)
            root.setGraphicsEffect(glow)
        else:
            root.setGraphicsEffect(None)

    def _apply_glass_effect(self):
        """Liquid Glass toggle. The clean approach:
        - When ON: enable DWM acrylic on the window. The root QFrame's QSS uses rgba bg
          so the window edges are partially transparent and the desktop blur shows through
          *around* the chat content. We DO NOT mess with child widget attributes - that
          was breaking the bubble rendering.
        - When OFF: solid theme."""
        enabled = bool(self.settings.get("liquid_glass", False))
        # glass_opacity controls how clear vs. frosted the glass is (not darkness).
        blur_level = int(self.settings.get("glass_opacity", 75))
        blurred = False  # True once a real desktop blur is mounted behind the content

        if sys.platform.startswith("win"):
            # Windows: DWM acrylic / mica backdrop (Win11 22H2+).
            try:
                import ctypes
                from ctypes import c_int, byref, sizeof
                hwnd = int(self.winId())
                backdrop = 3 if enabled else 1  # 3=Acrylic, 1=Auto
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, byref(c_int(backdrop)), sizeof(c_int))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(c_int(1)), sizeof(c_int))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, byref(c_int(2)), sizeof(c_int))
                blurred = enabled
            except Exception:
                pass
        else:
            # macOS: real NSVisualEffectView blur mounted behind the content. Escape hatch:
            # set "glass_native_blur": false in settings.json if a future macOS regresses it
            # (the frosted stylesheet below still looks like glass without it).
            if enabled and self.settings.get("glass_native_blur", True):
                try:
                    import mac_glass
                    blurred = mac_glass.set_blur(self, enabled, blur_level, radius=26)
                except Exception:
                    blurred = False
            else:
                try:
                    import mac_glass
                    mac_glass.set_blur(self, False)  # ensure any prior effect is torn down
                except Exception:
                    pass

        # Swap the QSS theme. The frosted veil thins automatically when a real blur is active
        # (blurred=True) so the desktop blur shows; otherwise the gradient + specular rim carry
        # the glass look on their own.
        # The translucent glass stylesheet is only safe when a REAL blur sits behind the
        # window (Windows acrylic, or the opt-in macOS native blur via EMBER_NATIVE_BLUR).
        # Without it, a frameless translucent window just shows the desktop through
        # everything — the "see-through / overlapping / unreadable" bug. So only use the
        # glass look when actually blurred; otherwise fall back to the solid opaque theme.
        if enabled and blurred:
            self.setStyleSheet(_glass_style(200, self.settings.get("accent_color", "#58a6ff"),
                                            see_through=blur_level, blurred=blurred))
        else:
            self.setStyleSheet(STYLE)
        self.update()

    def _toggle_max(self):
        screen = QApplication.primaryScreen().availableGeometry()
        if not getattr(self, "_is_maxed", False):
            self._pre_max_geometry = self.geometry()
            self.setGeometry(screen)
            self._is_maxed = True
            self.max_btn.setText("❐")
            self.max_btn.setToolTip("Restore size")
        else:
            if getattr(self, "_pre_max_geometry", None) is not None:
                self.setGeometry(self._pre_max_geometry)
            else:
                self.resize(440, 700)
            self._is_maxed = False
            self.max_btn.setText("□")
            self.max_btn.setToolTip("Toggle fullscreen")

    def _build_ui(self):
        # NOTE: removed Qt.WindowType.Tool - it was hiding Ember from the taskbar, which
        # blocked taskbar pinning. With just FramelessWindowHint + StaysOnTop, the window
        # shows up in the taskbar like a normal app and can be pinned.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1040, 760)
        # Apply liquid-glass acrylic backdrop if enabled
        if not _SAFE_MODE:
            QTimer.singleShot(100, self._apply_glass_effect)
        # Set window icon so the taskbar shows the globe instead of the generic python icon
        try:
            icon_path = _base_dir() / "icon.ico"
            if not icon_path.exists() and getattr(sys, "frozen", False):
                icon_path = Path(getattr(sys, "_MEIPASS", _base_dir())) / "icon.ico"
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)  # margin so glow has room

        root = QFrame()
        root.setObjectName("root")
        outer.addWidget(root)
        self._root = root
        self._apply_glow()

        root_row = QHBoxLayout(root)
        root_row.setContentsMargins(8, 6, 8, 8)
        root_row.setSpacing(8)

        sidebar = QFrame()
        sidebar.setObjectName("historyPanel")
        sidebar.setFixedWidth(196)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.setSpacing(7)

        side_title = QLabel("Chats")
        side_title.setObjectName("sideTitle")
        side_layout.addWidget(side_title)

        self.new_chat_btn = QPushButton("+ New chat")
        self.new_chat_btn.setObjectName("chip")
        self.new_chat_btn.clicked.connect(self._new_chat)
        side_layout.addWidget(self.new_chat_btn)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.currentItemChanged.connect(self._on_history_selected)
        side_layout.addWidget(self.history_list, 1)

        self.history_hint = QLabel("Recent context is sent automatically.")
        self.history_hint.setObjectName("sideHint")
        self.history_hint.setWordWrap(True)
        side_layout.addWidget(self.history_hint)
        root_row.addWidget(sidebar)

        main_panel = QWidget()
        main_panel.setObjectName("mainPanel")
        layout = QVBoxLayout(main_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        root_row.addWidget(main_panel, 1)

        command_panel = QFrame()
        command_panel.setObjectName("commandPanel")
        command_panel.setFixedWidth(226)
        command_layout = QVBoxLayout(command_panel)
        command_layout.setContentsMargins(10, 10, 10, 10)
        command_layout.setSpacing(7)

        command_title = QLabel("Command Center")
        command_title.setObjectName("sectionTitle")
        command_layout.addWidget(command_title)

        self.voice_chat_btn = QPushButton("Voice Chat")
        self.voice_chat_btn.setObjectName("voiceToggle")
        self.voice_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_chat_btn.setToolTip("Toggle hands-free voice conversation")
        self.voice_chat_btn.clicked.connect(self._toggle_voice_chat)
        command_layout.addWidget(self.voice_chat_btn)

        self.voice_status_label = QLabel("Voice idle")
        self.voice_status_label.setObjectName("panelHint")
        command_layout.addWidget(self.voice_status_label)

        status_strip = QFrame()
        status_strip.setObjectName("statusStrip")
        status_layout = QVBoxLayout(status_strip)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(3)
        self.capability_metric = QLabel("Computer + web + files + apps")
        self.capability_metric.setObjectName("statusMetric")
        self.model_metric = QLabel("Model warming up")
        self.model_metric.setObjectName("statusMetric")
        self.tool_metric = QLabel("Approvals stay visible")
        self.tool_metric.setObjectName("statusMetric")
        status_layout.addWidget(self.capability_metric)
        status_layout.addWidget(self.model_metric)
        status_layout.addWidget(self.tool_metric)
        command_layout.addWidget(status_strip)

        action_title = QLabel("Actions")
        action_title.setObjectName("sectionTitle")
        command_layout.addWidget(action_title)

        for label, cmd in COMMAND_CENTER_ACTIONS:
            b = QPushButton(label)
            b.setObjectName("commandAction")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cmd: self._run_slash(c))
            command_layout.addWidget(b)

        command_layout.addStretch(1)
        root_row.addWidget(command_panel)

        # Title bar
        title_row = QHBoxLayout()
        title = QLabel("● Ember")
        title.setObjectName("title")
        # Let clicks on the title text fall through to the window so the WHOLE bar drags,
        # not just the empty gaps between the label and the buttons.
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_row.addWidget(title)
        title_row.addStretch()

        BTN = 30
        self.reset_btn = QPushButton("↺")
        self.reset_btn.setObjectName("titleBtn")
        self.reset_btn.setFixedSize(BTN, BTN)
        self.reset_btn.setToolTip("Reset conversation")
        self.reset_btn.clicked.connect(self._reset_chat)
        title_row.addWidget(self.reset_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("titleBtn")
        self.settings_btn.setFixedSize(BTN, BTN)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        title_row.addWidget(self.settings_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("titleBtn")
        self.max_btn.setFixedSize(BTN, BTN)
        self.max_btn.setToolTip("Toggle fullscreen")
        self.max_btn.clicked.connect(self._toggle_max)
        title_row.addWidget(self.max_btn)

        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("titleBtn")
        self.min_btn.setFixedSize(BTN, BTN)
        self.min_btn.setToolTip("Minimize to corner pill")
        self.min_btn.clicked.connect(self._to_pill)
        title_row.addWidget(self.min_btn)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setFixedSize(BTN, BTN)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.close)
        title_row.addWidget(self.close_btn)

        layout.addLayout(title_row)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBar")
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.status_label)

        # Quick action chips — wrap onto multiple rows instead of scrolling off-screen.
        chip_holder = QWidget()
        chip_flow = FlowLayout(chip_holder, margin=2, hspacing=6, vspacing=6)
        # Keep the header clean: only the essentials. Everything else is available by typing
        # (e.g. "organize my downloads") or via /help, which lists all commands.
        _chips = [
            ("Autopilot", "/autopilot"),
            ("Voice", "__voice_chat__"),
            ("Screen", "/shot"),
            ("Browser", "/web"),
            ("Files", "/downloads"),
            ("Demo", "Create a cluttered demo folder on my Desktop, then show me how you would organize it with a dry run."),
            ("Help", "/help"),
        ]
        for label, cmd in _chips:
            b = QPushButton(label)
            b.setObjectName("chip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cmd: self._run_slash(c))
            chip_flow.addWidget(b)
        layout.addWidget(chip_holder)

        # Chat area
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Block the chat scroll area from EVER being wider than its viewport - that's
        # what was causing the rogue horizontal scrollbar.
        self.chat_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chat_container = QWidget()
        self.chat_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.chat_layout.setSpacing(6)
        # Stretch at end keeps bubbles top-aligned when chat isn't full yet.
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_container)
        layout.addWidget(self.chat_scroll, 1)

        # Input row
        input_row = QHBoxLayout()
        self.input_box = QTextEdit()
        self.input_box.setMaximumHeight(70)
        self.input_box.setPlaceholderText("What should I do?")
        self.input_box.installEventFilter(self)
        input_row.addWidget(self.input_box)

        # Upload button - up arrow (mono)
        self.upload_btn = QPushButton("↑")
        self.upload_btn.setObjectName("titleBtn")
        self.upload_btn.setFixedSize(36, 36)
        self.upload_btn.setToolTip("Upload files for Ember to read (images, PDFs, text, etc.)")
        self.upload_btn.clicked.connect(self._open_upload_dialog)
        input_row.addWidget(self.upload_btn)

        # Mic button (mono - circle for idle, red when listening)
        self.mic_btn = QPushButton("◉")
        self.mic_btn.setObjectName("titleBtn")
        self.mic_btn.setFixedSize(36, 36)
        self.mic_btn.setToolTip("Click to dictate (auto-stops on silence)")
        self.mic_btn.clicked.connect(self._toggle_mic)
        input_row.addWidget(self.mic_btn)

        send_col = QVBoxLayout()
        self.send_btn = QPushButton("▶")
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.clicked.connect(self._on_send)
        send_col.addWidget(self.send_btn)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setFixedSize(36, 26)
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.clicked.connect(self._on_stop)
        send_col.addWidget(self.stop_btn)
        input_row.addLayout(send_col)
        layout.addLayout(input_row)

        self.setStyleSheet(STYLE)
        # Mark root widget so the stylesheet applies
        root.setProperty("class", "root")

        self._refresh_history_sidebar()
        self._load_active_chat_into_view()
        self._update_voice_chat_ui()

    def _active_chat(self) -> dict:
        sessions = self.chat_history.setdefault("sessions", [])
        for chat in sessions:
            if chat.get("id") == self.active_chat_id:
                return chat
        if not sessions:
            sessions.append(_make_chat("Ember workspace"))
        self.active_chat_id = sessions[0].get("id")
        self.chat_history["active_id"] = self.active_chat_id
        return sessions[0]

    def _refresh_history_sidebar(self):
        if not hasattr(self, "history_list"):
            return
        self._history_loading = True
        self.history_list.clear()
        sessions = sorted(self.chat_history.get("sessions") or [], key=lambda c: c.get("updated", 0), reverse=True)
        self.chat_history["sessions"] = sessions
        active_row = 0
        for i, chat in enumerate(sessions):
            count = len(chat.get("messages") or [])
            title = chat.get("title") or "New chat"
            item = QListWidgetItem(f"{title}\n{count} message{'s' if count != 1 else ''}")
            item.setData(Qt.ItemDataRole.UserRole, chat.get("id"))
            self.history_list.addItem(item)
            if chat.get("id") == self.active_chat_id:
                active_row = i
        if self.history_list.count():
            self.history_list.setCurrentRow(active_row)
        self._history_loading = False

    def _clear_chat_view(self):
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _load_active_chat_into_view(self):
        self._clear_chat_view()
        chat = self._active_chat()
        messages = chat.get("messages") or []
        if not messages:
            hk = (self.settings.get("hotkey") or "ctrl+shift+space").upper().replace("+", "+")
            self._add_bubble("system",
                "Hi — I'm Ember, your computer's AI agent.\n"
                "I can organize files, browse the web, use desktop apps, debug crashes, automate tasks, "
                "control the mouse and keyboard, read the screen, create local files, and recover context from chat history.\n"
                "Use Voice Chat for hands-free work, the Command Center for common tasks, or just tell me the outcome you want.\n"
                f"**{hk}** summons me from anywhere · drop files into chat to discuss them.")
            return
        for msg in messages[-120:]:
            self._add_bubble(msg.get("role", "assistant"), msg.get("text", ""), meta=msg.get("meta"))

    def _append_history(self, role: str, text: str, meta: str | None = None):
        if not text:
            return
        chat = self._active_chat()
        now = int(time.time())
        chat.setdefault("messages", []).append({"role": role, "text": text, "meta": meta, "ts": now})
        chat["messages"] = chat["messages"][-240:]
        chat["updated"] = now
        if not chat.get("title") or chat.get("title") in {"New chat", "Ember workspace"}:
            if role == "user":
                chat["title"] = self._local_chat_title(text)
                self._queue_ai_chat_title(chat.get("id"), text)
        self.chat_history["active_id"] = self.active_chat_id
        save_chat_history(self.chat_history)
        self._refresh_history_sidebar()

    def _local_chat_title(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = re.sub(r"^[/#]\w+\s*", "", cleaned).strip()
        if not cleaned:
            return "New chat"
        words = cleaned.split()
        title = " ".join(words[:7])
        return title[:48].strip(" -:,.") or "New chat"

    def _queue_ai_chat_title(self, chat_id: str | None, first_text: str):
        if not chat_id or chat_id in self._title_jobs:
            return
        if not bool(self.settings.get("ai_chat_titles", True)):
            return
        key = (self.settings.get("gemini_api_key") or self.settings.get("gemini_api_key_secondary") or "").strip()
        if not key:
            return
        self._title_jobs.add(chat_id)

        def _run():
            title = ""
            try:
                from google import genai
                prompt = (
                    "Create a concise chat title for this user request. "
                    "Use 2 to 6 words. No quotes, no punctuation at the end.\n\n"
                    f"Request: {first_text[:1200]}"
                )
                client = genai.Client(api_key=key)
                resp = client.models.generate_content(model="gemma-3-27b-it", contents=prompt)
                title = (getattr(resp, "text", "") or "").strip()
                title = re.sub(r"^[\"'`]+|[\"'`.]+$", "", title)
                title = re.sub(r"\s+", " ", title).strip()
                title = " ".join(title.split()[:7])[:56].strip(" -:,.")
            except Exception:
                title = ""
            if title:
                self._bridge.chat_title.emit(chat_id, title)
            self._title_jobs.discard(chat_id)

        threading.Thread(target=_run, daemon=True).start()

    def _on_chat_title(self, chat_id: str, title: str):
        title = (title or "").strip()
        if not chat_id or not title:
            return
        for chat in self.chat_history.get("sessions") or []:
            if chat.get("id") == chat_id:
                chat["title"] = title
                chat["updated"] = int(time.time())
                save_chat_history(self.chat_history)
                self._refresh_history_sidebar()
                return

    def _new_chat(self):
        chat = _make_chat("New chat")
        self.chat_history.setdefault("sessions", []).insert(0, chat)
        self.active_chat_id = chat["id"]
        self.chat_history["active_id"] = self.active_chat_id
        save_chat_history(self.chat_history)
        if self.agent:
            self.agent.reset()
        self._refresh_history_sidebar()
        self._load_active_chat_into_view()
        self.input_box.setFocus()

    def _on_history_selected(self, current, _previous):
        if self._history_loading or current is None:
            return
        chat_id = current.data(Qt.ItemDataRole.UserRole)
        if not chat_id or chat_id == self.active_chat_id:
            return
        self.active_chat_id = chat_id
        self.chat_history["active_id"] = chat_id
        save_chat_history(self.chat_history)
        if self.agent:
            self.agent.reset()
        self._load_active_chat_into_view()

    def _agent_contextual_text(self, text: str) -> str:
        chat = self._active_chat()
        messages = [m for m in (chat.get("messages") or []) if m.get("role") in {"user", "assistant"}]
        if not messages:
            return text
        recent = messages[-10:]
        lines = []
        for m in recent:
            role = "User" if m.get("role") == "user" else "Ember"
            body = re.sub(r"\s+", " ", m.get("text", "")).strip()
            if body:
                lines.append(f"{role}: {body[:600]}")
        if not lines:
            return text
        return (
            "[Ember UI conversation context. Use this as active chat history whenever relevant; "
            "follow-ups like 'that', 'it', 'continue', and 'do the same' refer to this context.]\n"
            + "\n".join(lines)
            + "\n[/Ember UI conversation context]\n\nCurrent user message:\n"
            + text
        )

    def _refresh_welcome_line(self):
        """No-op placeholder kept for the hotkey-change callback - the welcome message
        is only shown once on startup. Future versions could update it in place."""
        pass

    def _restore_position(self):
        try:
            self.move(int(self.settings.get("window_x", 100)), int(self.settings.get("window_y", 100)))
        except Exception:
            pass

    def closeEvent(self, e):
        self.settings["window_x"] = self.x()
        self.settings["window_y"] = self.y()
        save_settings(self.settings)
        super().closeEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        # The real chat-viewport width is only known once shown; re-clamp bubbles added
        # during init (welcome / history) so they don't render at a stale width and end
        # up visually off to the side.
        QTimer.singleShot(0, self._clamp_bubble_widths)
        if not bool(self.settings.get("animations_enabled", True)):
            return
        try:
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(180)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            if not hasattr(self, "_anims"):
                self._anims = []
            self._anims.append(anim)
            self._anims = self._anims[-50:]
        except Exception:
            pass

    def mousePressEvent(self, e):
        # Whole title bar + status line is a drag handle (the control buttons keep their own
        # clicks since they aren't transparent-for-mouse). ~64px covers title row + status.
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 64:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if not paths:
            return
        listing = "\n".join(f"- {p}" for p in paths[:20])
        existing = self.input_box.toPlainText().strip()
        prefix = (existing + "\n\n") if existing else ""
        self.input_box.setPlainText(
            f"{prefix}Here are some files/folders I'm asking about:\n{listing}\n\n"
        )
        cursor = self.input_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_box.setTextCursor(cursor)
        self.input_box.setFocus()
        e.acceptProposedAction()

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        if obj is self.input_box and ev.type() == QEvent.Type.KeyPress:
            ke: QKeyEvent = ev
            if ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (ke.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, ev)

    # --- chat bubbles ---
    def _add_bubble(self, kind: str, text: str, meta: str | None = None) -> QFrame:
        frame = QFrame()
        if kind == "user":
            frame.setObjectName("bubbleUser")
        elif kind == "tool":
            frame.setObjectName("bubbleTool")
        elif kind == "error":
            frame.setObjectName("bubbleError")
        elif kind == "confirm":
            frame.setObjectName("bubbleConfirm")
        else:
            frame.setObjectName("bubble")
        # Bubble fills horizontal width and expands vertically for long responses.
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Hard cap so it never exceeds the visible chat area, even mid-resize.
        try:
            view_w = self.chat_scroll.viewport().width() - 24  # leave margin
            if view_w > 100:
                frame.setMaximumWidth(view_w)
        except Exception:
            pass

        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        if meta:
            m = QLabel(meta)
            m.setObjectName("meta")
            m.setWordWrap(True)
            v.addWidget(m)
        body = QLabel()
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(_md_to_html(text))
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setOpenExternalLinks(True)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setMinimumWidth(50)
        v.addWidget(body)
        # Insert before the trailing stretch so bubbles stay top-aligned.
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame)
        self._fade_in(frame, 180)
        QTimer.singleShot(35, self._scroll_to_bottom_smooth)
        # Re-clamp once layout settles so a bubble added before geometry is known
        # (e.g. during init) doesn't sit at the wrong width.
        QTimer.singleShot(0, self._clamp_bubble_widths)
        return frame

    def _clamp_bubble_widths(self):
        """Cap every bubble to the current chat viewport width. Called on resize and once
        the window is shown, so bubbles added before the 3-column layout settled (the
        welcome / restored-history messages) don't keep a stale, too-wide width."""
        try:
            view_w = self.chat_scroll.viewport().width() - 24
            if view_w > 100 and hasattr(self, "chat_layout"):
                for i in range(self.chat_layout.count()):
                    item = self.chat_layout.itemAt(i)
                    w = item.widget() if item else None
                    if w is not None:
                        w.setMaximumWidth(view_w)
        except Exception:
            pass

    def resizeEvent(self, e):
        """When the window resizes, update every bubble's max width so they stay contained."""
        super().resizeEvent(e)
        self._clamp_bubble_widths()

    def _fade_in(self, widget: QWidget, duration: int = 220):
        """Bubbles appear instantly.

        This used to fade each bubble in with a QGraphicsOpacityEffect. But a widget that
        has a QGraphicsEffect attached is rendered through an offscreen pixmap at its
        UNCONSTRAINED natural size — which made chat bubbles ignore the column width
        (overflowing under the Command Center) and leave ghosted/empty-box artifacts.
        Correct rendering beats the fade, so this is now a no-op. The window-open fade
        (showEvent, window-level opacity) is unaffected."""
        return

    def _scroll_to_bottom_smooth(self, duration: int = 190):
        try:
            bar = self.chat_scroll.verticalScrollBar()
            end = bar.maximum()
            if not bool(self.settings.get("animations_enabled", True)):
                bar.setValue(end)
                return
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setDuration(duration)
            anim.setStartValue(bar.value())
            anim.setEndValue(end)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            if not hasattr(self, "_anims"):
                self._anims = []
            self._anims.append(anim)
            self._anims = self._anims[-50:]
        except Exception:
            pass

    def _show_typing_indicator(self):
        """Pulsing dots that appear while the agent is thinking. Removed when first text arrives."""
        if getattr(self, "_typing_frame", None) is not None:
            return
        frame = QFrame()
        frame.setObjectName("typingIndicator")
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(8)
        dot_label = QLabel("●")
        dot_label.setObjectName("typingDots")
        h.addWidget(dot_label)
        label = QLabel("Ember is thinking…")
        label.setStyleSheet("color: #565f89; font-size: 11px;")
        h.addWidget(label)
        h.addStretch()
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame)
        self._typing_frame = frame
        self._typing_label = dot_label
        self._typing_phase = 0

        # Animate the dot label between 3 states: ● ●● ●●●
        def tick():
            if getattr(self, "_typing_label", None) is None:
                return
            self._typing_phase = (self._typing_phase + 1) % 3
            self._typing_label.setText("●" + " ●" * self._typing_phase)
        if not hasattr(self, "_typing_timer"):
            self._typing_timer = QTimer(self)
            self._typing_timer.timeout.connect(tick)
        self._typing_timer.start(350)
        self._fade_in(frame, 160)
        QTimer.singleShot(45, self._scroll_to_bottom_smooth)

    def _hide_typing_indicator(self):
        if getattr(self, "_typing_timer", None) is not None:
            self._typing_timer.stop()
        f = getattr(self, "_typing_frame", None)
        if f is not None:
            f.setParent(None)
            f.deleteLater()
        self._typing_frame = None
        self._typing_label = None

    def _set_status(self, text: str):
        self.status_label.setText(text)
        metric = getattr(self, "model_metric", None)
        if metric is not None:
            model = self.settings.get("model_id") or self.settings.get("gemini_model") or "No model"
            metric.setText(f"{model} · {text}")

    def _submit_user_text(self, text: str, meta: str | None = None, status: str = "Thinking...") -> bool:
        if not self.agent:
            QMessageBox.warning(self, "No API key", "Open settings (gear) and add your Gemini API key first.")
            self._open_settings()
            return False
        text = (text or "").strip()
        if not text:
            return False
        if text.startswith("/"):
            if self._handle_slash(text):
                return True
        corrected = False
        if self.settings.get("autocorrect_chat", True):
            text, corrected = autocorrect_chat_text(text)
        if corrected:
            meta = f"{meta} · autocorrected" if meta else "autocorrected"
        agent_text = self._agent_contextual_text(text)
        self._add_bubble("user", text, meta=meta)
        self._append_history("user", text, meta=meta)
        self._set_status(status)
        self.send_btn.setEnabled(False)
        self._show_typing_indicator()
        self.agent.send_user_message(agent_text)
        return True

    def _on_send(self):
        text = self.input_box.toPlainText().strip()
        if not text:
            return
        if self._submit_user_text(text):
            self.input_box.clear()

    def _handle_slash(self, text: str) -> bool:
        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower()
        target = SLASH_COMMANDS.get(cmd)
        if target is None:
            return False
        if target == "__help__":
            self._add_bubble("system", HELP_TEXT)
            return True
        if target == "__clear__":
            self._reset_chat()
            return True
        if target == "__forget_all__":
            import memory as _mem
            count = _mem.forget_all().get("forgot_count", 0)  # locked + atomic
            self._add_bubble("system", f"Forgot {count} saved facts.")
            return True
        if target == "__voice_chat__":
            self._toggle_voice_chat()
            return True
        if target == "__remote__":
            self._start_remote_control()
            return True
        if target == "__update__":
            self._start_update()
            return True
        if target == "__manual__":
            self._open_manual_mode()
            return True
        expanded = target + (" " + rest if rest else "")
        agent_text = self._agent_contextual_text(expanded)
        self._add_bubble("user", text + f"\n(expanded: {target[:80]}{'…' if len(target) > 80 else ''})")
        self._append_history("user", text)
        self._set_status("Thinking…")
        self.send_btn.setEnabled(False)
        self._show_typing_indicator()
        self.agent.send_user_message(agent_text)
        return True

    def _run_slash(self, cmd: str):
        if cmd == "__voice_chat__":
            self._toggle_voice_chat()
            return
        if cmd == "__manual__":
            self._open_manual_mode()
            return
        if cmd == "__remote__":
            self._start_remote_control()
            return
        if cmd == "__update__":
            self._start_update()
            return
        self.input_box.setPlainText(cmd)
        self._on_send()

    def _start_remote_control(self):
        """Start Ember Link (phone control) and show the URL + PIN."""
        try:
            import remote_server
            remote_server.set_chat_handler(lambda text: self._bridge.remote_chat.emit(text))
            r = remote_server.start()
        except Exception as e:
            QMessageBox.warning(self, "Remote control", f"Could not start: {e}")
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Remote control", r.get("error", "Failed to start."))
            return
        url, pin = r.get("url", ""), r.get("pin", "")
        box = QMessageBox(self)
        box.setWindowTitle("Ember Link")
        box.setText("Control this Mac from your phone:")
        box.setInformativeText(
            f"1.  Connect your phone to the SAME Wi-Fi as this Mac.\n"
            f"2.  Open this address in the phone browser:\n\n        {url}\n\n"
            f"3.  Enter PIN:  {pin}\n\n"
            "You get a faster mirrored screen, reliable click-and-drag, a full-screen mirror, "
            "trackpad, keyboard, and a Chat tab that can tell Ember what to do remotely."
        )
        box.exec()
        self._add_bubble("system", f"Ember Link is live at **{url}** (PIN **{pin}**). Same Wi-Fi required.")

    def _autostart_remote_control(self):
        """Bring Ember Link up automatically at launch — no modal, just a status note.
        Best-effort: a bind failure (e.g. port already in use) is reported quietly and
        never blocks the app from opening."""
        try:
            import remote_server
            remote_server.set_chat_handler(lambda text: self._bridge.remote_chat.emit(text))
            r = remote_server.start()
        except Exception as e:
            print(f"[Ember Link autostart failed: {e}]")
            return
        if not r.get("ok"):
            print(f"[Ember Link autostart failed: {r.get('error')}]")
            return
        url, pin = r.get("url", ""), r.get("pin", "")
        # Persist the URL + PIN so it's discoverable outside the app too.
        try:
            d = remote_server._data_dir()
            (d / "remote_url.txt").write_text(f"{url}   PIN {pin}\n")
        except Exception:
            pass
        if r.get("already_running"):
            return
        self._add_bubble(
            "system",
            f"📱 Ember Link is live at **{url}** (PIN **{pin}**). "
            "Open it on a phone on the same Wi-Fi to control this Mac.",
        )

    # ------------------------------------------------------------------ updates
    def _check_for_update_async(self):
        """Background check for a newer release. No-op in dev / when unconfigured.
        Emits update_available (with the manifest) on the UI thread if one is found."""
        try:
            import updater
            if not updater.can_self_update():
                return
        except Exception:
            return

        def _work():
            try:
                import updater
                manifest = updater.check_for_update()
            except Exception:
                manifest = None
            if manifest:
                self._bridge.update_available.emit(manifest)

        threading.Thread(target=_work, daemon=True).start()

    def _git_self_update(self):
        """If Ember runs from a git checkout (source/dev), fast-forward to the latest commit
        on launch so it stays current without a manual 'git pull'. Background + silent; only
        fast-forwards (never clobbers local edits)."""
        if getattr(sys, "frozen", False):
            return
        import shutil
        base = _base_dir()
        if not (base / ".git").exists() or not shutil.which("git"):
            return

        def _work():
            try:
                import subprocess
                r = subprocess.run(["git", "-C", str(base), "pull", "--ff-only"],
                                   capture_output=True, text=True, timeout=90)
                out = ((r.stdout or "") + (r.stderr or "")).strip()
                if r.returncode == 0 and "up to date" not in out.lower():
                    self._bridge.notice.emit(
                        "⬆️ Updated Ember to the latest version — **restart Ember** to apply.")
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def _on_update_available(self, manifest: dict):
        """A newer release is available. With auto-update on (default) install it now;
        otherwise just announce it (manual install via /update)."""
        import version
        self._pending_update = manifest
        ver = manifest.get("version", "?")
        notes = (manifest.get("notes") or "").strip()
        if bool(self.settings.get("auto_update", True)):
            self._add_bubble("system", f"🔄 Installing **Ember {ver}** automatically…")
            self._set_status(f"Updating to {ver}…")
            QTimer.singleShot(300, lambda: self._start_update(auto=True))
            return
        msg = (f"🔄 **Ember {ver}** is available (you have {version.__version__}). "
               "Type **/update** to install it now.")
        if notes:
            msg += "\n\n" + notes[:500]
        self._add_bubble("system", msg)
        self._set_status(f"Update {ver} available — type /update")

    def _start_update(self, auto: bool = False):
        """Download + install the pending update, then relaunch. Triggered by /update, or
        automatically at launch when auto=True (skips the confirmation prompt)."""
        try:
            import updater
        except Exception as e:
            self._add_bubble("error", f"Updater unavailable: {e}")
            return
        if not updater.can_self_update():
            self._add_bubble("system",
                "Auto-update only works on the installed **Ember.app**. In dev mode, "
                "rebuild with BUILD_DESKTOP_APP.command, or download the latest build "
                "from the Ember website.")
            return
        manifest = self._pending_update
        if not manifest:
            self._set_status("Checking for updates…")
            self._add_bubble("system", "Checking for the latest version… "
                             "if an update exists you'll see it shortly, then /update installs it.")
            self._check_for_update_async()
            return
        ver = manifest.get("version", "?")
        if not auto and QMessageBox.question(
                self, "Update Ember",
                f"Install Ember {ver} now?\n\nEmber will download the update and restart.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
            return
        self._set_status(f"Downloading Ember {ver}…")
        self._add_bubble("system", f"Downloading Ember {ver}…")

        def _post(fn):
            QTimer.singleShot(0, fn)

        def _work():
            try:
                import updater
                staged = updater.download_and_stage(manifest)
                updater.apply_update_and_relaunch(staged)
                _post(lambda: self._add_bubble("system", "Update ready — restarting Ember…"))
                _post(lambda: QApplication.instance().quit())
            except Exception as e:
                _post(lambda: self._add_bubble("error", f"Update failed: {type(e).__name__}: {e}"))
                _post(lambda: self._set_status("Update failed"))

        threading.Thread(target=_work, daemon=True).start()

    def _on_remote_chat(self, text: str):
        """Receive a command from Ember Link and run it through the desktop agent."""
        text = (text or "").strip()
        if not text:
            return
        try:
            import remote_server
        except Exception:
            remote_server = None
        if text.lower() in {"stop", "stop what you are doing.", "cancel"}:
            if self.agent:
                self.agent.stop()
            self._set_status("Stopped from Ember Link")
            self.send_btn.setEnabled(True)
            self._hide_typing_indicator()
            self._add_bubble("system", "Ember Link asked Ember to stop.")
            if remote_server:
                remote_server.push_chat("system", "Stopped the current desktop turn.")
            return
        if not self.agent:
            msg = "Ember is not ready yet. Open Settings in the desktop app and add an API key first."
            self._add_bubble("error", msg)
            if remote_server:
                remote_server.push_chat("system", msg)
            return
        if text.startswith("/") and self._handle_slash(text):
            if remote_server:
                remote_server.push_chat("system", f"Ran command from Ember Link: {text}")
            return
        self._submit_user_text(text, meta="Ember Link", status="Remote command...")

    def _open_manual_mode(self):
        # Pull the last ~12 chat bubbles for context
        recent = []
        for i in range(self.chat_layout.count()):
            w = self.chat_layout.itemAt(i).widget()
            if w is None:
                continue
            for child in w.findChildren(QLabel):
                txt = child.text()
                if txt and len(txt) > 4:
                    recent.append(txt)
        dlg = ManualModeDialog(recent_chat=recent[-12:], parent=self)
        dlg.exec()

    def _on_stop(self):
        if self._voice_chat_enabled:
            self._stop_voice_chat("Voice chat stopped")
        if self.agent:
            self.agent.stop()
            self._set_status("Stopping...")

    def _reset_chat(self):
        if self.agent:
            self.agent.reset()
        chat = self._active_chat()
        chat["messages"] = []
        chat["updated"] = int(time.time())
        save_chat_history(self.chat_history)
        self._refresh_history_sidebar()
        self._load_active_chat_into_view()
        self._add_bubble("system", "Conversation reset.")

    def _open_settings(self):
        old_hotkey = (self.settings.get("hotkey") or "ctrl+shift+space").lower()
        # Snapshot the keys the agent / glass effect actually depend on, so we only rebuild
        # them when relevant — a font or appearance tweak shouldn't drop conversation state.
        agent_keys = ("model_id", "provider", "gemini_api_key", "gemini_api_key_secondary",
                      "anthropic_api_key", "anthropic_model", "gemini_model",
                      "auto_screenshot", "request_timeout_seconds", "dual_api_failover")
        glass_keys = ("liquid_glass", "glass_opacity", "accent_color")
        old_agent = {k: self.settings.get(k) for k in agent_keys}
        old_glass = {k: self.settings.get(k) for k in glass_keys}
        dlg = SettingsDialog(self.settings, self, automation_engine=self._automation)
        if dlg.exec():
            self.settings = dlg.get_settings()
            save_settings(self.settings)
            if self.agent is None or any(self.settings.get(k) != old_agent[k] for k in agent_keys):
                self._init_agent()
            if hasattr(self, "_automation"):
                self._automation.enabled = bool(self.settings.get("automation_enabled", True))
                self._automation.auto_confirm_popups = bool(self.settings.get("auto_confirm_popups", False))
            # Re-apply appearance live (no restart needed)
            self._apply_glow()
            if any(self.settings.get(k) != old_glass[k] for k in glass_keys):
                self._apply_glass_effect()
            new_hotkey = (self.settings.get("hotkey") or "ctrl+shift+space").lower()
            if new_hotkey != old_hotkey:
                self._install_hotkey()
                self._add_bubble("system", f"Hotkey changed to **{new_hotkey}**. Status: {self._hotkey_status}")
            # Refresh the welcome line so the message matches the active combo
            QTimer.singleShot(50, self._refresh_welcome_line)

    def _first_run_settings(self):
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to Ember")
        box.setText("Let's get you set up — 2 quick steps:")
        box.setInformativeText(
            "1.  Get a FREE Gemini API key (no credit card) — tap 'Get free key'.\n"
            "2.  Paste it into Settings (opens next), pick a model, click Save.\n\n"
            "Ember will then ask for Screen Recording + Accessibility permissions so it can "
            "see the screen and control the mouse and keyboard. Grant both, then quit and "
            "reopen Ember once."
        )
        get_btn = box.addButton("Get free key", QMessageBox.ButtonRole.ActionRole)
        box.addButton("I have a key →", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is get_btn:
            try:
                import webbrowser
                webbrowser.open("https://aistudio.google.com/apikey")
            except Exception:
                pass
        self._open_settings()

    def _check_accessibility(self):
        """Trigger the macOS Accessibility prompt if Ember isn't trusted yet (needed to move the
        mouse / type). macOS doesn't ask for this automatically the way it does Screen Recording."""
        if sys.platform != "darwin":
            return
        try:
            import mac_permissions
            if mac_permissions.request_accessibility(prompt=True):
                return  # already trusted
            self._add_bubble(
                "system",
                "⚠️ Ember needs **Accessibility** access to control the mouse and keyboard.\n"
                "I've opened the request — enable **Ember** under System Settings → Privacy & "
                "Security → Accessibility, then quit and reopen Ember.",
            )
            mac_permissions.open_accessibility_settings()
        except Exception:
            pass

    def _init_agent(self):
        # Warm the heavy google.genai import on a BACKGROUND thread so the UI never freezes,
        # then finish building the agent on the main thread via the agent_ready signal.
        self._set_status("Starting…")

        def _warm():
            try:
                import agent  # noqa: F401  (~1.7s import, off the main thread)
            except Exception:
                pass
            try:
                self._bridge.agent_ready.emit()
            except Exception:
                pass
        threading.Thread(target=_warm, daemon=True).start()

    def _build_agent(self):
        from agent import Agent  # already warm from the background thread -> fast here
        model_id = self.settings.get("model_id") or self.settings.get("gemini_model") or "gemini-3.1-flash-lite"
        provider = self.settings.get("provider") or model_catalog.provider_for(model_id)
        try:
            if provider == "claude":
                key = self.settings.get("anthropic_api_key") or ""
                if not key:
                    self._set_status("Need Anthropic API key for Claude — open settings (gear)")
                    return
                from claude_agent import ClaudeAgent
                self.agent = ClaudeAgent(
                    api_key=key,
                    model_name=model_id,
                    auto_screenshot=bool(self.settings.get("auto_screenshot", True)),
                )
            else:
                if not self.settings.get("gemini_api_key"):
                    self._set_status("Need Gemini API key — open settings (gear)")
                    return
                self.agent = Agent(
                    api_key=self.settings["gemini_api_key"],
                    secondary_api_key=self.settings.get("gemini_api_key_secondary") or None,
                    dual_api_failover=bool(self.settings.get("dual_api_failover", True)),
                    model_name=model_id,
                    anthropic_key=self.settings.get("anthropic_api_key") or None,
                    anthropic_model=self.settings.get("anthropic_model", "claude-opus-4-8"),
                    auto_screenshot=bool(self.settings.get("auto_screenshot", True)),
                    request_timeout_seconds=int(self.settings.get("request_timeout_seconds", 30)),
                )
            self.agent.subscribe(lambda ev: self._bridge.event.emit(ev))
            self._set_status(f"Ready ({model_id})")
        except Exception as e:
            QMessageBox.critical(self, "Agent init failed", f"{type(e).__name__}: {e}\n\nCheck your API key and model name.")
            self._set_status("Agent init failed")

    def _should_speak_reply(self) -> bool:
        return bool(self.settings.get("voice_output", False)) or (
            self._voice_chat_enabled and bool(self.settings.get("voice_chat_spoken_replies", True))
        )

    def _speak_reply(self, text: str):
        if not self._should_speak_reply():
            return
        try:
            import voice
            spoken = (text or "").replace("*", "").replace("`", "")
            spoken = re.sub(r"\[[^\]]+\]", "", spoken).strip()
            if spoken:
                voice.speak(spoken[:700])
        except Exception:
            pass

    # --- agent events on UI thread ---
    def _on_event_main_thread(self, ev: AgentEvent):
        try:
            try:
                import remote_server as _remote
            except Exception:
                _remote = None
            if ev.kind == "stream_chunk":
                # Real text is arriving - kill the thinking indicator
                self._hide_typing_indicator()
                if _remote:
                    _remote.update_stream(ev.payload or "")
                # First chunk creates the bubble; subsequent chunks append.
                if not getattr(self, "_streaming_bubble_label", None):
                    frame = self._add_bubble("assistant", "")
                    # Find the body QLabel inside the frame
                    for child in frame.findChildren(QLabel):
                        if child.objectName() != "meta":
                            self._streaming_bubble_label = child
                            self._streaming_buffer = ""
                            break
                if getattr(self, "_streaming_bubble_label", None):
                    self._streaming_buffer += ev.payload or ""
                    self._streaming_bubble_label.setText(_md_to_html(self._streaming_buffer))
                    QTimer.singleShot(0, lambda: self.chat_scroll.verticalScrollBar().setValue(
                        self.chat_scroll.verticalScrollBar().maximum()
                    ))
                return
            if ev.kind == "stream_end":
                # Lock in the final text and clear streaming state.
                if _remote:
                    _remote.update_stream("", done=True)
                final_stream = getattr(self, "_streaming_buffer", "") or ""
                if final_stream.strip():
                    self._append_history("assistant", final_stream.strip())
                if getattr(self, "_streaming_bubble_label", None):
                    self._speak_reply(self._streaming_buffer or "")
                self._streaming_bubble_label = None
                self._streaming_buffer = ""
                return
            if ev.kind == "message":
                # First real content - kill the thinking indicator
                self._hide_typing_indicator()
                # Non-streamed text (errors, status messages, fallback notices).
                # If a stream is in progress, finalize it cleanly: commit its text to history
                # (don't drop it) and close the phone's streaming bubble so the next
                # stream_chunk starts a fresh one instead of appending to a stale bubble.
                if getattr(self, "_streaming_bubble_label", None):
                    partial = getattr(self, "_streaming_buffer", "") or ""
                    if partial.strip():
                        self._append_history("assistant", partial.strip())
                    if _remote:
                        _remote.update_stream("", done=True)
                    self._streaming_bubble_label = None
                    self._streaming_buffer = ""
                text = ev.payload or "(empty)"
                self._add_bubble("assistant", text)
                self._append_history("assistant", text)
                if _remote:
                    _remote.push_chat("assistant", text)
                self._speak_reply(text)
            elif ev.kind == "tool_call":
                name = ev.payload["name"]
                args_short = self._shorten_args(ev.payload["args"])
                self._add_bubble("tool", f"→ {name}({args_short})", meta="tool call")
                if _remote:
                    _remote.push_chat("tool", f"Running {name}({args_short})")
                self._set_status(f"Running {name}…")
            elif ev.kind == "tool_result":
                name = ev.payload["name"]
                result = ev.payload["result"]
                ok = result.get("ok", True)
                summary = self._summarize_result(name, result)
                kind = "tool" if ok else "error"
                self._add_bubble(kind, summary, meta=f"← {name}")
                if _remote:
                    _remote.push_chat("tool" if ok else "system", f"{name}: {summary}")
            elif ev.kind == "confirm":
                pending: PendingConfirmation = ev.payload
                if _remote:
                    _remote.push_chat("system", f"Approval needed on desktop: {pending.tool_name}")
                self._show_confirm_inline(pending)
            elif ev.kind == "awaiting_claude":
                pending: PendingClaudeResponse = ev.payload
                if _remote:
                    _remote.push_chat("system", "Ember is waiting for a Claude handoff on the desktop.")
                self._add_bubble("system",
                                 "Gemini is consulting Claude. The handoff prompt is on your clipboard. "
                                 "Paste it into Claude.ai and paste the reply in the dialog.")
                dlg = ClaudeHandoffDialog(pending, self)
                dlg.exec()
            elif ev.kind == "human_pause":
                if _remote:
                    _remote.push_chat("system", "Ember paused for a manual step on the desktop.")
                self._show_human_pause_inline(ev.payload)
            elif ev.kind == "claude_handoff":
                self._add_bubble("system", "Claude replied (via API):\n" + (ev.payload.get("auto_reply") or "")[:1500])
            elif ev.kind == "error":
                self._add_bubble("error", str(ev.payload))
                self._append_history("error", str(ev.payload))
                if _remote:
                    _remote.push_chat("system", "Error: " + str(ev.payload))
            elif ev.kind == "done":
                self.send_btn.setEnabled(True)
                self._hide_typing_indicator()
                self._set_status(f"Ready ({self.settings.get('model_id') or self.settings.get('gemini_model')})")
                if self._voice_chat_enabled:
                    self._voice_waiting_for_reply = False
                    self._update_voice_chat_ui("Listening again")
                    QTimer.singleShot(650, lambda: self._start_voice_listen(mode="voice_chat"))
                if _remote:
                    _remote.push_chat("system", "Done.")
        except Exception:
            traceback.print_exc()

    def _shorten_args(self, args: dict) -> str:
        items = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 60:
                s = s[:60] + "…"
            items.append(f"{k}={s}")
        return ", ".join(items)

    def _summarize_result(self, name: str, result: dict) -> str:
        if not result.get("ok", True):
            return f"❌ {result.get('error', 'failed')}"
        if name == "take_screenshot":
            return f"📸 captured {result.get('width')}x{result.get('height')}"
        if name == "get_event_logs":
            return f"📜 {result.get('event_count', 0)} events"
        if name == "get_installed_drivers":
            return f"🔧 {result.get('driver_count', 0)} drivers"
        if name == "get_running_processes":
            return f"⚙ {result.get('process_count', 0)} processes"
        if name in {"run_powershell", "run_cmd"}:
            out = (result.get("stdout") or "").strip()
            err = (result.get("stderr") or "").strip()
            if err and not out:
                return f"stderr: {err[:300]}"
            return f"out: {out[:400] if out else '(no output)'}"
        if name == "read_file":
            return f"📄 read {len(result.get('content', ''))} chars"
        if name == "write_file":
            return f"💾 wrote {result.get('bytes_written', 0)} bytes"
        if name == "list_directory":
            return f"📁 {len(result.get('entries', []))} entries"
        if name == "get_system_info":
            info = result.get("info") or {}
            return f"🖥 {info.get('OS', '')} | CPU {info.get('CPU', '')[:40]} | RAM {info.get('FreeRAM_GB','?')}/{info.get('RAM_GB','?')} GB free"
        return "✓ done"

    def _show_human_pause_inline(self, pending: PendingHumanPause):
        self._set_status("Waiting for you…")
        frame = QFrame()
        frame.setObjectName("bubbleConfirm")
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        meta = QLabel("Hand-off to you")
        meta.setObjectName("meta")
        v.addWidget(meta)
        body = QLabel(pending.reason)
        body.setWordWrap(True)
        body.setStyleSheet("font-weight: bold;")
        v.addWidget(body)
        if pending.what_you_need:
            need = QLabel(pending.what_you_need)
            need.setWordWrap(True)
            v.addWidget(need)
        note_input = QLineEdit()
        note_input.setPlaceholderText("Optional note for the agent (e.g. 'logged in')")
        v.addWidget(note_input)

        btn_row = QHBoxLayout()
        resume = QPushButton("I'm done — resume")
        resume.setObjectName("approve")
        cancel = QPushButton("Cancel turn")
        cancel.setObjectName("deny")

        def _resume():
            pending.response.put(note_input.text().strip() or "done")
            resume.setEnabled(False); cancel.setEnabled(False)
            resume.setText("Resumed ✓")
        def _cancel():
            pending.response.put("CANCELLED")
            resume.setEnabled(False); cancel.setEnabled(False)
            cancel.setText("Cancelled ✕")
            if self.agent:
                self.agent.stop()

        resume.clicked.connect(_resume)
        cancel.clicked.connect(_cancel)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(resume)
        v.addLayout(btn_row)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame)
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        ))

    def _show_confirm_inline(self, pending: PendingConfirmation):
        frame = QFrame()
        frame.setObjectName("bubbleConfirm")
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        meta = QLabel(f"⚠ Approve risky action — {pending.reason}")
        meta.setObjectName("meta")
        v.addWidget(meta)
        args_text = self._shorten_args(pending.args)
        body = QLabel(f"{pending.tool_name}({args_text})")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(body)
        if "command" in pending.args:
            cmd_box = QPlainTextEdit(pending.args["command"])
            cmd_box.setReadOnly(True)
            cmd_box.setMaximumHeight(120)
            v.addWidget(cmd_box)

        btn_row = QHBoxLayout()
        approve = QPushButton("Approve")
        approve.setObjectName("approve")
        deny = QPushButton("Deny")
        deny.setObjectName("deny")
        def _approve():
            pending.response.put(True)
            approve.setEnabled(False); deny.setEnabled(False)
            approve.setText("Approved ✓")
        def _deny():
            pending.response.put(False)
            approve.setEnabled(False); deny.setEnabled(False)
            deny.setText("Denied ✕")
        approve.clicked.connect(_approve)
        deny.clicked.connect(_deny)
        btn_row.addStretch()
        btn_row.addWidget(deny)
        btn_row.addWidget(approve)
        v.addLayout(btn_row)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame)
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        ))


def main(instance_listener=None):
    if _SAFE_MODE:
        print("[Ember] EMBER_SAFE_MODE on — native blur, global hotkey, accessibility "
              "prompt, and phone-remote autostart are disabled.")
    app = QApplication(sys.argv)
    app.setApplicationName("Ember")
    app.setQuitOnLastWindowClosed(True)

    # Tray
    tray = QSystemTrayIcon()
    icon_path = _base_dir() / "icon.ico"
    if not icon_path.exists() and getattr(sys, "frozen", False):
        # PyInstaller _MEIPASS bundle
        icon_path = Path(getattr(sys, "_MEIPASS", _base_dir())) / "icon.ico"
    if icon_path.exists():
        tray.setIcon(QIcon(str(icon_path)))
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        pix = QPixmap(16, 16)
        pix.fill(QColor("#7aa2f7"))
        tray.setIcon(QIcon(pix))
    tray.setToolTip("Ember")
    tray_menu = QMenu()
    show_action = QAction("Show")
    quit_action = QAction("Quit")
    tray_menu.addAction(show_action)
    tray_menu.addAction(quit_action)
    tray.setContextMenu(tray_menu)
    tray.show()

    window = EmberWindow()
    window.show()

    # If another instance of Ember is started, it sends SUMMON through the lock socket.
    if instance_listener is not None:
        try:
            from single_instance import listen_for_summon
            listen_for_summon(instance_listener, lambda: window._bridge.summon.emit())
        except Exception as e:
            print(f"[summon listener failed: {e}]")

    show_action.triggered.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
    quit_action.triggered.connect(app.quit)
    tray.activated.connect(lambda r: (window.showNormal(), window.raise_(), window.activateWindow())
                           if r == QSystemTrayIcon.ActivationReason.Trigger else None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
