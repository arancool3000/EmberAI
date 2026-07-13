"""Floating chat window UI for Ember."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
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
    Qt, QPoint, QRect, QSize, pyqtSignal, QObject, QTimer, QThread,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation,
)
from PyQt6.QtGui import QFont, QIcon, QTextCursor, QAction, QPainter, QColor, QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextEdit, QLineEdit, QFrame, QScrollArea, QDialog, QFormLayout,
    QMessageBox, QPlainTextEdit, QSizePolicy, QSystemTrayIcon, QMenu,
    QComboBox, QCheckBox, QTabWidget, QListWidget, QListWidgetItem,
    QInputDialog, QFileDialog, QGraphicsOpacityEffect, QGraphicsDropShadowEffect,
    QSlider, QLayout, QGroupBox, QProgressBar, QStackedWidget, QGridLayout,
)

import models as model_catalog
import automation as automation_mod
import manual_mode as manual_mod
import icons

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
    "/terminal": "__terminal__",
    "/term": "__terminal__",
    "/repl": "__terminal__",
    "/agents": "__agents__",
    "/tasks": "__agents__",
    "/remote": "__remote__",
    "/link": "__remote__",
    "/browser": "__browser_app__",
    "/manual": "__manual__",
    "/antivirus": "__antivirus__",
    "/adblock": "__adblock__",
    "/setup": "__setup_tour__",
    "/features": "__features__",
    "/help": "__help__",
    "/clear": "__clear__",
    "/reset": "__clear__",
    "/forget": "__forget_all__",
    "/update": "__update__",
    "/usage": "__usage__",
    "/plugins": "__plugins__",
    "/passwords": "__passwords__",
    "/vpn": "__vpn__",
    "/workflow": "__workflow__",
    "/record": "__screen_record__",
    "/snippets": "__snippets__",
    "/macros": "__macros__",
    "/localai": "__local_ai__",
    "/ollama": "__local_ai__",
    "/storage": "__storage__",
    "/network": "__network_inspector__",
    "/clipboard": "__clipboard__",
}

HELP_TEXT = """Tip: click the ✨ Features button (top of the window) for a searchable list of
EVERYTHING Ember can do, with a one-click "Open" for each. Or type /features.

How Ember's buttons work
The Command Center has two kinds of buttons:
  • Apps & tools — OPEN a feature (Phone Link, Ember Browser, Antivirus, Sandbox,
    Usage, Plugins, Manual bridge).
  • Quick tasks — TYPE a request into the chat and send it. They're just examples;
    you can also type any request yourself.

Slash commands  (type these, or use the Quick-task buttons)

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

Features (open a tool)
  /remote     start Ember Link for phone control
  /browser    open the Ember Browser app (tab groups + password manager)
  /passwords  manage saved website logins
  /vpn        connect / disconnect your WireGuard VPN
  /workflow   record & replay mouse/keyboard workflows
  /record     record your screen to a video
  /snippets   manage reusable text snippets
  /macros     save & run named task macros
  /localai    use a local Ollama model (offline, no key, no limits)
  /storage    open the visual large-file and duplicate-file inspector
  /network    inspect Wi-Fi, connections, listening ports, and nearby devices
  /clipboard  open a private, temporary clipboard history
  /usage      show API usage vs the free-tier limits
  /plugins    manage drop-in plugin tools
  /manual     bridge an external AI

Session
  /voice      toggle hands-free voice chat
  /windows    list open windows
  /clear      clear chat
  /forget     wipe saved facts
  /update     install the latest Ember version
  /help       this list

More you can just ASK for (no command needed)
  • "save a snippet" / "expand ;sig" — reusable text snippets
  • "record a workflow" / "replay <name>" — record & replay mouse+keyboard
  • "record my screen for 20s" — screen recorder
  • "has my email been breached?" — email breach check
  • "scan this download" — and turn on real-time download protection in Settings
  • Settings ▸ Models: store API keys in the encrypted vault
  • Plugins: drop a .py in the plugins/ folder to add your own tools (/plugins)

Global hotkey: configurable in Settings -> Performance (default Ctrl+Shift+Space).
Drop a file/folder onto the chat to discuss it.

Tip: just say "organize my Downloads", "find duplicates in Pictures",
"why is my PC slow", "open my GoPro folder" -- no command needed.
"""


# Command Center, grouped so a new user can tell the two kinds of buttons apart:
#  - "Apps & tools" OPEN a feature/window (Phone Link, Browser, Antivirus, …).
#  - "Quick tasks" TYPE a request into the chat and send it (they are example prompts).
# Each entry is (label, command, tooltip). A command starting with "__" opens a feature;
# anything else is a prompt/slash that gets sent to Ember as a request.
COMMAND_CENTER_GROUPS = [
    ("Apps & tools", [
        ("📱 Phone Link",     "__remote__",      "Control this computer from your phone (Ember Link)"),
        ("🌐 Ember Browser",  "__browser_app__", "Open the secure AI browser — tab groups + password manager"),
        ("🛡 Antivirus",      "__antivirus__",   "Open the Antivirus app — scan, quarantine, real-time protection"),
        ("🚫 Ad blocker",     "__adblock__",     "Block ads & trackers system-wide (every app, not just the browser)"),
        ("💾 Storage",        "__storage__",     "Find large files and duplicates with a safe visual scanner"),
        ("⌁ Network",         "__network_inspector__", "Inspect Wi-Fi, devices, connections, and listening ports"),
        ("▣ Clipboard",       "__clipboard__",   "Use a private clipboard history while this window is open"),
        ("📦 Sandbox",        "__sandbox__",     "Run a file safely in an isolated sandbox"),
        ("🔐 Passwords",      "__passwords__",   "Saved website logins (encrypted)"),
        ("🌍 VPN",            "__vpn__",         "Connect / disconnect your WireGuard VPN"),
        ("🎬 Workflows",      "__workflow__",    "Record & replay mouse/keyboard workflows"),
        ("🔴 Screen recorder","__screen_record__","Record your screen to a video file"),
        ("✂️ Snippets",       "__snippets__",    "Save & expand reusable text snippets"),
        ("📋 Macros",         "__macros__",      "Save & run named task macros"),
        ("🖥 Local AI",       "__local_ai__",    "Use a local Ollama model — offline, no key, no limits"),
        ("⌨️ Terminal",       "__terminal__",    "Built-in terminal + Python runner (run shell & code in-app)"),
        ("🧠 Agent tasks",    "__agents__",      "Run several Ember tasks at once and track them in a dashboard"),
        ("📊 Usage",          "__usage__",       "API calls & tokens vs the free-tier limits"),
        ("🧩 Plugins",        "__plugins__",     "Manage drop-in plugin tools (the plugins/ folder)"),
        ("⬇️ Update",         "__update__",      "Check for and install the latest version of Ember"),
        ("🔗 Manual bridge",  "__manual__",      "Bridge an external AI for hard reasoning"),
    ]),
    ("Quick tasks", [
        ("Autopilot a task",  "/autopilot", None),
        ("Operate this app",  "/apps",      None),
        ("Research a topic",  "/research",  None),
        ("Create a file",     "/create",    None),
        ("Take a screenshot", "/shot",      None),
        ("Automate a website","/web",       None),
        ("Organize a folder", "/organize",  None),
        ("Clean Downloads",   "/downloads", None),
        ("Find duplicates",   "/dedupe",    None),
        ("Performance check", "/perf",      None),
        ("Diagnose this PC",  "/diagnose",  None),
        ("Build a rule",      "/automate",  None),
        ("Schedule a task",   "/schedule",  None),
    ]),
]


# ---------------------------------------------------------------------------
# Feature catalog — the single, browsable list of EVERYTHING Ember can do, shown in
# the "✨ Features" directory (FeaturesDialog). Each entry is (emoji, name, description,
# action). action is one of:
#   ("open", "<token>")  -> runs a feature opener / slash via _run_slash (e.g. "__remote__", "/diagnose")
#   ("type", "<text>")   -> drops an example request into the chat box (the user hits send)
#   ("settings", "<tab>")-> opens Settings (tab name is just a hint shown to the user)
#   ("info", "")         -> no button, purely informational
# Keeping it data-driven means the directory, search, and launch buttons all stay in sync.
FEATURE_CATALOG = [
    ("Getting started", [
        ("🧭", "Setup tour", "New here? A 1-minute guided setup that picks your AI in plain language and installs the free offline AI.", ("open", "__setup_tour__")),
    ]),
    ("Automate your computer", [
        ("🤖", "Autopilot", "Hand Ember a whole task and it drives the mouse, keyboard and apps to finish it.", ("open", "/autopilot")),
        ("🪟", "Operate an app", "Tell Ember to do something in the app that's open right now.", ("open", "/apps")),
        ("⏰", "Scheduled tasks", "Run a task on a timer (e.g. every morning).", ("open", "/schedule")),
        ("⚙️", "Automation rules", "Background rules: when a window/app appears, auto-do something.", ("open", "/automate")),
        ("🎬", "Record & replay workflows", "Record your mouse/keyboard once, replay it any time.", ("open", "__workflow__")),
        ("🧩", "Build your own tool", "Ask Ember to save a repeatable multi-step procedure as a reusable tool.", ("type", "Build a custom tool that organizes my Downloads and then lists what changed, and save it.")),
    ]),
    ("Web & research", [
        ("🌐", "Ember Browser", "A real built-in browser Ember can read and drive (tab groups + passwords).", ("open", "__browser_app__")),
        ("🧩", "AI extension maker", "In Ember Browser, describe what you want and AI writes a userscript that runs on matching pages (🧩 button).", ("open", "__browser_app__")),
        ("🕸️", "Automate a website", "Open a page and fill forms / click / scrape via the DevTools browser.", ("open", "/web")),
        ("🔎", "Research a topic", "Browse multiple sources, compare, and report back.", ("open", "/research")),
        ("📸", "Screenshot the screen", "Capture the screen so Ember can see and act on it.", ("open", "/shot")),
    ]),
    ("Files & documents", [
        ("🗂️", "Organize a folder", "Sort a folder by type/date — always previews with a dry-run first.", ("open", "/organize")),
        ("🧹", "Clean Downloads", "Tidy your Downloads folder.", ("open", "/downloads")),
        ("📑", "Find duplicates", "Find duplicate files so you can reclaim space.", ("open", "/dedupe")),
        ("📦", "Find big files", "Find the biggest space-hogs.", ("open", "/biggest")),
        ("📄", "Read / discuss a file", "Drop or paste a file (or photo) into the chat — Ember reads it.", ("type", "Read this file and summarize it: ")),
        ("✍️", "Create a file", "Have Ember create a document, script, or asset locally.", ("open", "/create")),
    ]),
    ("Voice & hands-free", [
        ("🎙️", "Voice chat", "Talk to Ember hands-free — it listens, acts, and speaks back.", ("open", "__voice_chat__")),
        ("🗣️", "Natural voice (Live API)", "Real-time full-duplex voice that hears your tone/accent & replies naturally (Gemini Live). Enable in Settings ▸ Voice.", ("settings", "Voice")),
        ("👋", "“Hey Ember” wake word", "Always-on wake word so you can summon it by voice. Toggle in Settings ▸ Voice.", ("settings", "Voice")),
        ("🔊", "Spoken replies", "Have Ember read its answers aloud. Settings ▸ Voice.", ("settings", "Voice")),
    ]),
    ("Security & privacy", [
        ("🛡️", "Antivirus", "Scan, manage quarantine, and toggle real-time protection.", ("open", "__antivirus__")),
        ("🚫", "Ad blocker", "Block ads & trackers for EVERY app (system-wide hosts sinkhole).", ("open", "__adblock__")),
        ("📦", "Sandbox a file", "Run a risky file in an isolated sandbox.", ("open", "__sandbox__")),
        ("🔒", "Real-time protection", "Always-on download, fileless & Security-Center scanning. Settings ▸ Security.", ("settings", "Security")),
        ("🌍", "VPN", "Connect/disconnect your WireGuard VPN.", ("open", "__vpn__")),
        ("🔐", "Password manager", "Saved website logins, encrypted on your machine.", ("open", "__passwords__")),
        ("🗝️", "Encrypted key vault", "Store your API keys encrypted instead of in plaintext. Settings ▸ Models.", ("settings", "Models")),
    ]),
    ("AI brain & models", [
        ("✨", "Gemini (free)", "Runs day-to-day on Google's free tier. Settings ▸ Models.", ("settings", "Models")),
        ("🧠", "Claude", "Switch to Claude for hard reasoning. Add a key in Settings ▸ Models.", ("settings", "Models")),
        ("🖥️", "Local AI (Ollama)", "Run fully offline with no key or limits.", ("open", "__local_ai__")),
        ("🔗", "Manual bridge", "Bridge an external AI for a tough problem.", ("open", "__manual__")),
    ]),
    ("Phone & remote", [
        ("📱", "Phone Link (Ember Link)", "Control this computer from your phone over Wi-Fi, PIN-protected.", ("open", "__remote__")),
    ]),
    ("Productivity", [
        ("💾", "Storage inspector", "Find large files and duplicate copies, then move unwanted items to Trash.", ("open", "__storage__")),
        ("▣", "Clipboard history", "Temporarily capture, preview, and reuse copied text. Nothing is uploaded.", ("open", "__clipboard__")),
        ("✂️", "Snippets", "Save reusable text and expand it anywhere.", ("open", "__snippets__")),
        ("📋", "Macros", "Save and run named task macros.", ("open", "__macros__")),
        ("🔴", "Screen recorder", "Record your screen to a video file.", ("open", "__screen_record__")),
        ("📊", "Usage dashboard", "See API calls & tokens vs the free-tier limits.", ("open", "__usage__")),
    ]),
    ("Diagnose your PC", [
        ("⌁", "Network inspector", "Review Wi-Fi details, nearby devices, active connections, and listening ports.", ("open", "__network_inspector__")),
        ("🩺", "Full diagnosis", "Scan crashes, errors and health in one go.", ("open", "/diagnose")),
        ("📈", "Performance snapshot", "Live CPU / memory / disk.", ("open", "/perf")),
        ("💻", "System info", "OS / CPU / GPU / RAM.", ("open", "/info")),
    ]),
    ("Extend & upkeep", [
        ("🧩", "Plugins", "Drop a .py file in the plugins/ folder to add your own tools.", ("open", "__plugins__")),
        ("⬇️", "Update Ember", "Check for and install the latest version.", ("open", "__update__")),
        ("🎛️", "Settings", "Models, Appearance, Voice, Performance, Automations, Memory, Security.", ("settings", "")),
    ]),
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


_VAULT_KEYS = ("gemini_api_key", "gemini_api_key_secondary", "gemini_api_key_3",
               "gemini_api_key_4", "anthropic_api_key", "openai_api_key")


def _hydrate_keys_from_vault(settings: dict) -> dict:
    """When the encrypted key vault is enabled, settings.json holds blanked keys; pull the
    real values back into the in-memory settings so the running agent can use them."""
    if settings.get("use_key_vault"):
        try:
            import key_vault
            for k in _VAULT_KEYS:
                if not settings.get(k):
                    v = key_vault.get_key(k)
                    if v:
                        settings[k] = v
        except Exception:
            pass
    return settings


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return _hydrate_keys_from_vault(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return {
        "gemini_api_key": "",
        "gemini_api_key_secondary": "",
        "gemini_api_key_3": "",
        "gemini_api_key_4": "",
        "gemini_model": "gemini-3.1-flash-lite",
        "model_id": "gemini-3.1-flash-lite",
        "provider": "gemini",
        "anthropic_api_key": "",
        "anthropic_model": "claude-opus-4-8",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "openai_base_url": "",        # blank = OpenAI; set for an OpenAI-compatible provider (Grok/DeepSeek/…)
        "openai_provider_label": "OpenAI",
        "ollama_model": "",
        "auto_screenshot": True,
        "autocorrect_chat": True,
        "voice_output": False,
        "voice_chat_spoken_replies": True,
        "voice_chat_auto_send": True,
        "voice_chat_continue_after_silence": True,
        "voice_chat_phrase_timeout": "auto",  # "auto" = end the turn when you pause; or a max seconds
        "tts_engine": "system",       # read-aloud engine: system | edge | gemini | soundtools
        "edge_tts_voice": "en-US-AriaNeural",  # Microsoft Edge neural voice (free, no key)
        "gemini_tts_voice": "Kore",
        "soundtools_api_key": "",
        "live_voice_enabled": False,  # use the Gemini Live API for real-time natural voice chat
        "live_voice_voice": "Zephyr", # Live API prebuilt voice name
        "push_to_talk": False,        # hold a key to talk (zero-latency, no wake word)
        "push_to_talk_key": "f9",     # the key you hold to talk
        "stt_engine": "auto",         # speech-to-text: auto | whisper (local) | gemini | google
        "whisper_model": "base",      # local Whisper model size when stt_engine uses Whisper
        "wake_word": True,            # always-on "hey ember" wake listening
        "wake_visual": "glow",        # "Hey Ember" shows: glow around Ember (default) | floating orb
        "glow_animation": True,       # Siri-style flowing glow while listening/thinking/speaking
        "bubble_animation": True,     # grow-in animation for new chat bubbles
        "keep_running_in_background": True,  # closing the window hides to tray so wake word keeps working
        "launch_at_login": False,     # install a login item so Ember is always running for the wake word
        "ai_chat_titles": True,
        "chat_title_model": "gemma-3-4b-it",  # which model names chats: "ollama" or a small Gemma
        "dual_api_failover": True,
        "automation_enabled": True,
        "auto_confirm_popups": False,
        "remote_autostart": True,
        "mcp_bridge_enabled": False,          # expose Ember's tools to external MCP clients (off by default)
        "mcp_bridge_port": 8770,
        "mcp_bridge_allow_high_risk": False,  # allow high-risk tools over MCP with no human confirm
        "auto_update": True,
        "lean_tools": False,
        "offline_mode": False,        # no internet: local brain + local tools, network tools fail fast
        "auto_lockdown_on_critical": False,  # panic: auto stop-AI + cut-network + lock on a critical threat
        "hotkey": "ctrl+shift+space",
        "hotkey_daemon": False,       # always-on login helper so the hotkey works even when quit
        "mouse_humanize": True,       # curved/eased human-like pointer movement
        "mouse_speed": 1.0,           # pointer movement speed multiplier (0.25x–3.0x)
        "show_ember_pointer": True,   # branded click-through pointer while Ember acts
        "request_timeout_seconds": 15,
        "animations_enabled": True,
        "motion_level": "dynamic",  # dynamic | smooth | reduced | off
        "show_thinking": True,      # show Ember's reasoning ("Thinking…") as a collapsible section
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
        to_write = settings
        if settings.get("use_key_vault"):
            # Move API keys into the encrypted vault and write a redacted copy to disk, so
            # settings.json never holds plaintext keys. The in-memory dict keeps the real
            # values (the running agent still works). If the vault write fails for any key,
            # fall back to writing that key as-is rather than silently losing it.
            try:
                import key_vault
                to_write = dict(settings)
                for k in _VAULT_KEYS:
                    val = to_write.get(k)
                    if val and key_vault.set_key(k, val):
                        to_write[k] = ""
            except Exception:
                to_write = settings
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(to_write, indent=2), encoding="utf-8")
    except OSError as e:
        # Never let a failed write crash the app or silently lose the API key.
        print(f"[settings save failed: {e}] path={SETTINGS_PATH}")


def _new_chat_id() -> str:
    return f"chat_{int(time.time() * 1000)}"


def _fix_assistant_name(text: str) -> str:
    """Correct 'amber'-style mishearings of 'Ember' (delegates to voice; importable + tested)."""
    try:
        import voice
        return voice.fix_assistant_name(text)
    except Exception:
        return text


def _is_stop_phrase(text: str) -> bool:
    try:
        import voice
        return voice.is_stop_phrase(text)
    except Exception:
        return False


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


from styles import _glass_style, STYLE, WORKSPACE_POLISH  # stylesheets live in styles.py



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


# Sentinel that tells _dialog_header (and any brand spot) to render Ember's diamond logo mark
# instead of a text glyph.
DIAMOND_MARK = "diamond"


class DiamondMark(QWidget):
    """Ember's brand logo: a resizable faceted "ember gem" diamond.

    Renders the branded vector gem (icons.diamond_svg) scaled to the widget's CURRENT size, so
    it stays crisp at any size and is trivially resizable — pass a size, call set_mark_size(),
    or let a layout resize it. Replaces the old letter-"E" / sparkle badges. Falls back to a
    hand-painted gradient diamond if QtSvg is unavailable, so the mark never disappears."""

    def __init__(self, size: int = 34, parent=None):
        super().__init__(parent)
        self._size = max(8, int(size))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_mark_size(self, px: int) -> None:
        """Resize the mark. It re-renders crisply because the art is vector, not a fixed bitmap."""
        self._size = max(8, int(px))
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def minimumSizeHint(self) -> QSize:
        s = min(12, self._size)
        return QSize(s, s)

    def paintEvent(self, event) -> None:
        side = min(self.width(), self.height())
        if side <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = (self.width() - side) / 2.0
        y = (self.height() - side) / 2.0
        if not self._render_svg(painter, x, y, float(side)):
            self._render_fallback(painter, x, y, float(side))
        painter.end()

    def _render_svg(self, painter, x, y, side) -> bool:
        try:
            from PyQt6.QtCore import QByteArray, QRectF
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(QByteArray(icons.diamond_svg().encode("utf-8")))
            if not renderer.isValid():
                return False
            renderer.render(painter, QRectF(x, y, side, side))
            return True
        except Exception:
            return False

    def _render_fallback(self, painter, x, y, side) -> None:
        # QtSvg missing → draw a simple ember-gradient rhombus so the logo still appears.
        try:
            from PyQt6.QtCore import QPointF
            from PyQt6.QtGui import QBrush, QLinearGradient, QPolygonF
            cx, cy, h = x + side / 2.0, y + side / 2.0, side / 2.0
            grad = QLinearGradient(cx - h, cy - h, cx + h, cy + h)
            grad.setColorAt(0.0, QColor("#e6963c"))
            grad.setColorAt(0.5, QColor("#dc5a28"))
            grad.setColorAt(1.0, QColor("#af2629"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPolygon(QPolygonF([
                QPointF(cx, cy - h), QPointF(cx + h, cy),
                QPointF(cx, cy + h), QPointF(cx - h, cy)]))
        except Exception:
            pass


def _dialog_header(title: str, description: str, eyebrow: str = "EMBER",
                   mark: str = DIAMOND_MARK) -> QFrame:
    """Reusable product header for every secondary Ember surface. `mark` is a short text glyph
    for the surface, or DIAMOND_MARK to show Ember's diamond brand logo."""
    frame = QFrame()
    frame.setObjectName("dialogHeader")
    row = QHBoxLayout(frame)
    row.setContentsMargins(13, 11, 15, 11)
    row.setSpacing(11)
    if mark == DIAMOND_MARK:
        icon = DiamondMark(38)
    else:
        icon = QLabel(mark)
        icon.setObjectName("dialogMark")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(38, 38)
    row.addWidget(icon)
    copy = QVBoxLayout()
    copy.setContentsMargins(0, 0, 0, 0)
    copy.setSpacing(0)
    overline = QLabel(eyebrow.upper())
    overline.setObjectName("dialogEyebrow")
    heading = QLabel(title)
    heading.setObjectName("dialogTitle")
    heading.setWordWrap(True)
    sub = QLabel(description)
    sub.setObjectName("dialogDescription")
    sub.setWordWrap(True)
    copy.addWidget(overline)
    copy.addWidget(heading)
    copy.addWidget(sub)
    row.addLayout(copy, 1)
    frame.heading_label = heading
    frame.description_label = sub
    return frame


def _polish_dialog(dialog: QDialog) -> None:
    """Apply the shared surface system without relying on a parent window's stylesheet."""
    dialog.setStyleSheet(STYLE + WORKSPACE_POLISH)


class EventBridge(QObject):
    """Marshals agent events from worker threads to the UI thread."""
    event = pyqtSignal(object)
    summon = pyqtSignal()
    transcript = pyqtSignal(str, str)
    remote_chat = pyqtSignal(str)
    chat_title = pyqtSignal(str, str)
    agent_ready = pyqtSignal()  # emitted from a worker thread once google.genai is imported
    update_available = pyqtSignal(object)  # emitted from the background update-check thread
    update_progress = pyqtSignal(int)  # download percentage from the updater worker
    notice = pyqtSignal(str)  # post a one-line system bubble from a background thread
    source_updated = pyqtSignal(str)  # a git source checkout fast-forwarded to a new version
    wake_detected = pyqtSignal()  # the "hey ember" wake word was heard (from the wake thread)
    download_alert = pyqtSignal(object)  # a downloaded file was a threat / cautionary executable
    live_voice_event = pyqtSignal(str, str)  # (kind, text) from the Live API voice thread
    timer_fired = pyqtSignal(str)  # a countdown timer elapsed (message) — from the timer thread
    ptt_press = pyqtSignal()       # push-to-talk key down (from the key-listener thread)
    ptt_release = pyqtSignal()     # push-to-talk key up
    ptt_text = pyqtSignal(str)     # final push-to-talk transcript (from the transcribe thread)
    ptt_state = pyqtSignal(str)    # push-to-talk state change: recording/transcribing/idle
    ptt_error = pyqtSignal(str)    # push-to-talk failure message


# macOS virtual key codes -> pynput key-name tokens (so replay's _resolve_key matches). Only
# the special (non-character) keys need mapping; printable keys use their character directly.
_MAC_KEYCODE_NAMES = {
    36: "enter", 76: "enter", 48: "tab", 49: "space", 51: "backspace", 53: "esc",
    117: "delete", 123: "left", 124: "right", 125: "down", 126: "up",
    115: "home", 119: "end", 116: "page_up", 121: "page_down",
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7",
    100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12",
}


def _mac_key_token(ev) -> str:
    """Turn an NSEvent keyDown into a token compatible with workflow_recorder/pynput replay."""
    try:
        kc = int(ev.keyCode())
    except Exception:
        kc = -1
    if kc in _MAC_KEYCODE_NAMES:
        return _MAC_KEYCODE_NAMES[kc]
    try:
        ch = ev.charactersIgnoringModifiers() or ""
    except Exception:
        ch = ""
    if len(ch) == 1 and ch.isprintable():
        return ch
    return ""


class _MacInputRecorder(QObject):
    """Captures global mouse/keyboard input via NSEvent monitors on the MAIN run loop and feeds
    it to workflow_recorder. This REPLACES pynput on macOS for workflow recording: pynput's
    background Quartz event tap builds NSEvents off the main thread, and macOS then asserts and
    hard-crashes the process (SIGTRAP) — the same bug fixed for the global hotkey. NSEvent
    monitors run on the run loop and are safe. Mouse coordinates are converted from Cocoa
    (bottom-left origin) to top-left so they line up with pyautogui, which replay now uses
    instead of pynput's Controller for the same reason (avoids pynput's macOS event machinery
    entirely, not just its Listener)."""

    _start_req = pyqtSignal(object)   # emitted from a worker thread -> installs on the main thread
    _stop_req = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monitors: list = []
        self._handlers: dict | None = None
        self._ok = False
        self._screen_h = 0.0
        self._dispatch_cb = None
        # BlockingQueuedConnection: when emitted from a background thread the slot runs on the
        # GUI thread and emit() blocks until it returns, so monitors are always installed on the
        # run loop. We never emit these from the GUI thread (we'd call _do_* directly), so no
        # self-deadlock.
        self._start_req.connect(self._do_start, Qt.ConnectionType.BlockingQueuedConnection)
        self._stop_req.connect(self._do_stop, Qt.ConnectionType.BlockingQueuedConnection)

    # ---- public API (callable from any thread) -------------------------------
    def start(self, handlers: dict) -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        self._handlers = handlers
        self._ok = False
        if QThread.currentThread() == app.thread():
            self._do_start()
        else:
            self._start_req.emit(handlers)
        return self._ok

    def stop(self):
        app = QApplication.instance()
        if app is None:
            return
        if QThread.currentThread() == app.thread():
            self._do_stop()
        else:
            self._stop_req.emit()

    # ---- main-thread implementation ------------------------------------------
    def _do_start(self, handlers=None):
        if handlers is not None:
            self._handlers = handlers
        try:
            from AppKit import NSEvent, NSScreen
        except Exception as e:
            print(f"[workflow] AppKit unavailable: {e}")
            self._ok = False
            return
        # Global key/mouse monitoring needs Accessibility (same as the hotkey). Prompt if missing
        # — monitors still install either way (they just won't see global events until granted).
        try:
            import mac_permissions
            if not mac_permissions.request_accessibility(prompt=False):
                mac_permissions.request_accessibility(prompt=True)
        except Exception:
            pass
        try:
            self._screen_h = float(NSScreen.screens()[0].frame().size.height)
        except Exception:
            self._screen_h = 0.0

        h = self._handlers or {}
        on_move, on_click = h.get("move"), h.get("click")
        on_scroll, on_key = h.get("scroll"), h.get("key")

        def _pt():
            try:
                loc = NSEvent.mouseLocation()
                x = int(loc.x)
                y = int(self._screen_h - loc.y) if self._screen_h else int(loc.y)
                return x, y
            except Exception:
                return 0, 0

        def _dispatch(ev):
            try:
                et = int(ev.type())
            except Exception:
                return
            if et in (5, 6, 7, 27):              # moved + L/R/other dragged
                if on_move:
                    x, y = _pt(); on_move(x, y)
            elif et == 1:                         # left mouse down
                if on_click:
                    x, y = _pt(); on_click(x, y, "left", True)
            elif et == 3:                         # right mouse down
                if on_click:
                    x, y = _pt(); on_click(x, y, "right", True)
            elif et == 25:                        # other (middle) mouse down
                if on_click:
                    x, y = _pt(); on_click(x, y, "middle", True)
            elif et == 22:                        # scroll wheel
                if on_scroll:
                    x, y = _pt()
                    try:
                        dx, dy = int(ev.scrollingDeltaX()), int(ev.scrollingDeltaY())
                    except Exception:
                        dx, dy = 0, 0
                    on_scroll(x, y, dx, dy)
            elif et == 10:                        # key down
                if on_key:
                    tok = _mac_key_token(ev)
                    if tok:
                        on_key(tok)

        self._dispatch_cb = _dispatch
        # Combined mask: mouse downs (L/R/other) + moved/drags + scroll + key-down.
        MASK = ((1 << 1) | (1 << 3) | (1 << 25)
                | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 27)
                | (1 << 22) | (1 << 10))
        try:
            g = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(MASK, lambda ev: _dispatch(ev))

            def _local(ev):
                try:
                    _dispatch(ev)
                except Exception:
                    pass
                return ev   # MUST return the event so Ember's own UI still receives it
            l = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(MASK, _local)
            self._monitors = [m for m in (g, l) if m is not None]
            self._ok = bool(self._monitors)
        except Exception as e:
            print(f"[workflow] NSEvent monitor failed: {e}")
            self._ok = False

    def _do_stop(self):
        try:
            from AppKit import NSEvent
            for m in self._monitors:
                try:
                    NSEvent.removeMonitor_(m)
                except Exception:
                    pass
        except Exception:
            pass
        self._monitors = []
        self._dispatch_cb = None


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
        try:
            self.parent_window._really_quit = True
        except Exception:
            pass
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
        self.setWindowTitle("External AI Bridge")
        self.setMinimumSize(700, 640)
        self._recent_chat = recent_chat
        self._last_screen_summary = last_screen_summary

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(9)
        layout.addWidget(_dialog_header(
            "External AI Bridge",
            "Hand a difficult task to another model, then review and run the code it returns.",
            eyebrow="HUMAN-IN-THE-LOOP WORKFLOW", mark="↗"))

        info = QLabel(
            "1  Describe the outcome   ·   2  Copy the generated prompt into another AI   ·   "
            "3  Review its code here before running it"
        )
        info.setObjectName("dialogStatus")
        info.setWordWrap(True)
        layout.addWidget(info)

        request_label = QLabel("OUTCOME")
        request_label.setObjectName("fieldLabel")
        layout.addWidget(request_label)
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

        prompt_label = QLabel("GENERATED PROMPT")
        prompt_label.setObjectName("fieldLabel")
        layout.addWidget(prompt_label)
        self.prompt_view = QPlainTextEdit()
        self.prompt_view.setObjectName("codeSurface")
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setMaximumHeight(180)
        layout.addWidget(self.prompt_view)

        copy_row = QHBoxLayout()
        copy_btn = QPushButton("Copy prompt to clipboard")
        copy_btn.clicked.connect(self._copy_prompt)
        copy_row.addWidget(copy_btn)
        copy_row.addStretch()
        layout.addLayout(copy_row)

        code_label = QLabel("REVIEW RETURNED CODE")
        code_label.setObjectName("fieldLabel")
        layout.addWidget(code_label)
        self.code_input = QPlainTextEdit()
        self.code_input.setObjectName("codeSurface")
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

        output_label = QLabel("RUN OUTPUT")
        output_label.setObjectName("fieldLabel")
        layout.addWidget(output_label)
        self.output_view = QPlainTextEdit()
        self.output_view.setObjectName("codeSurface")
        self.output_view.setReadOnly(True)
        self.output_view.setMaximumHeight(150)
        layout.addWidget(self.output_view)

        _polish_dialog(self)

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


# Theme presets: each sets accent color + glass opacity + glow/animations/liquid-glass at once.
_THEME_PRESETS = {
    "Midnight Blue": {"accent_color": "#7aa2f7", "glass_opacity": 75, "glow_enabled": True,
                      "animations_enabled": True, "liquid_glass": False},
    "Neon Purple":   {"accent_color": "#bb9af7", "glass_opacity": 60, "glow_enabled": True,
                      "animations_enabled": True, "liquid_glass": False},
    "Minimal Light": {"accent_color": "#7dcfff", "glass_opacity": 40, "glow_enabled": False,
                      "animations_enabled": False, "liquid_glass": True},
    "Forest":        {"accent_color": "#9ece6a", "glass_opacity": 70, "glow_enabled": True,
                      "animations_enabled": True, "liquid_glass": False},
    "Amber Warm":    {"accent_color": "#e0af68", "glass_opacity": 80, "glow_enabled": True,
                      "animations_enabled": True, "liquid_glass": False},
    "High Contrast": {"accent_color": "#f7768e", "glass_opacity": 95, "glow_enabled": False,
                      "animations_enabled": False, "liquid_glass": False},
}


def show_usage_dashboard(parent):
    """Show the API usage dashboard (calls/tokens vs the free-tier limits). Shared by the
    Settings dialog and the Command Center so the same view is reachable from both."""
    try:
        import usage
        s = usage.summary()
    except Exception as e:
        QMessageBox.warning(parent, "Usage dashboard", f"Could not read usage: {e}")
        return

    def _bar(pct):
        pct = max(0, min(100, int(pct)))
        return "█" * round(pct / 10) + "░" * (10 - round(pct / 10))

    lines = [
        "<b>API usage — Gemini free tier</b>", "",
        f"This minute:  {s['calls_last_minute']}/{s['limit_per_minute']} calls   "
        f"{_bar(s['minute_pct'])} {int(s['minute_pct'])}%",
        f"Today:        {s['calls_today']}/{s['limit_per_day']} calls   "
        f"{_bar(s['day_pct'])} {int(s['day_pct'])}%",
        f"Tokens today: {s['tokens_today']:,}",
        f"Remaining:    {s['minute_remaining']} this minute · {s['day_remaining']} today",
    ]
    by_model = s.get("by_model") or {}
    if by_model:
        lines += ["", "<b>By model (today)</b>"]
        lines += [f"  {m}: {c}" for m, c in sorted(by_model.items(), key=lambda kv: -kv[1])]
    last7 = s.get("last_7_days") or []
    if last7:
        lines += ["", "<b>Last 7 days</b>"]
        lines += [f"  {d['date']}: {d['calls']} calls · {d['tokens']:,} tokens" for d in last7]
    box = QMessageBox(parent)
    box.setWindowTitle("Usage dashboard")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText("<pre style='font-family:monospace'>" + "\n".join(lines) + "</pre>")
    reset_btn = box.addButton("Reset counters", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Close)
    box.exec()
    if box.clickedButton() is reset_btn:
        try:
            import usage
            usage.usage_reset()
        except Exception:
            pass


def _qt_key_name(key, text: str) -> str:
    """Map a Qt key code to the pynput-compatible name Ember's hotkey/PTT layers understand
    ('f9', 'space', 'esc', 'a', …). Returns '' for keys we can't name."""
    # Function keys F1..F35 are consecutive.
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
        return f"f{key - Qt.Key.Key_F1 + 1}"
    named = {
        Qt.Key.Key_Space: "space", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "enter",
        Qt.Key.Key_Enter: "enter", Qt.Key.Key_Escape: "esc", Qt.Key.Key_Backspace: "backspace",
        Qt.Key.Key_Delete: "delete", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
        Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Home: "home",
        Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "page_up", Qt.Key.Key_PageDown: "page_down",
        Qt.Key.Key_Control: "ctrl", Qt.Key.Key_Shift: "shift", Qt.Key.Key_Alt: "alt",
        Qt.Key.Key_Meta: "cmd",
    }
    if key in named:
        return named[key]
    if text and len(text.strip()) == 1 and text.isprintable():
        return text.strip().lower()
    try:
        s = QKeySequence(key).toString().lower()
        return s if s else ""
    except Exception:
        return ""


def _qkeyevent_to_combo(e, chord: bool) -> str:
    """Turn a Qt key press into Ember's combo string. chord=True -> 'ctrl+shift+space';
    chord=False -> a single key like 'f9' (a lone modifier is accepted as the key)."""
    key = e.key()
    is_mod = key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta)
    if chord and is_mod:
        return ""  # wait for the real key while modifiers are held
    name = _qt_key_name(key, e.text())
    if not name:
        return ""
    if not chord:
        return name
    mods = []
    m = e.modifiers()
    if m & Qt.KeyboardModifier.ControlModifier:
        mods.append("ctrl")
    if m & Qt.KeyboardModifier.AltModifier:
        mods.append("alt")
    if m & Qt.KeyboardModifier.ShiftModifier:
        mods.append("shift")
    if m & Qt.KeyboardModifier.MetaModifier:
        mods.append("cmd")
    return "+".join(mods + [name])


class _KeyCaptureEdit(QLineEdit):
    """A shortcut field you set by PRESSING the key(s), like every other app — instead of typing
    the key's name. chord=True captures a combination (Ctrl+Shift+Space); chord=False a single
    key (F9). Read-only; the captured value is available via value()."""

    def __init__(self, value: str = "", chord: bool = True, parent=None):
        super().__init__(parent)
        self._chord = chord
        self._value = (value or "").strip()
        self.setReadOnly(True)
        self.setPlaceholderText("Click, then press a key…")
        self.setToolTip("Click here, then press the key" + (" combination" if chord else "") +
                        " you want to use.")
        self._render()

    def value(self) -> str:
        return self._value

    def _render(self):
        self.setText(self._value.replace("+", " + ").title() if self._value else "")

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.setText("Press a key…" if self._chord else "Press a key…")

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._render()

    def keyPressEvent(self, e):
        combo = _qkeyevent_to_combo(e, self._chord)
        if combo:
            self._value = combo
            self._render()
            self.clearFocus()
        # swallow the event either way so it doesn't type into the field
        e.accept()

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.setFocus()


class SettingsDialog(QDialog):
    _mcp_setup_done = pyqtSignal(str, object)

    """Tabbed settings: Models · Performance · Automations · Memory · Security · About."""

    def __init__(self, settings: dict, parent=None, automation_engine=None, only_tab=None):
        super().__init__(parent)
        self.setWindowTitle("Ember Settings")
        self.setMinimumSize(820, 560)
        self.settings = dict(settings)
        self.automation_engine = automation_engine
        # When only_tab is set, this dialog is presented as a single feature's OWN window
        # (its tab shown, the tab bar hidden, retitled). All tabs are still BUILT so the shared
        # get_settings() save path is unchanged — we just don't show the others.
        self._only_tab = only_tab
        self._mcp_setup_done.connect(self._on_mcp_setup_done)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(10)
        self._settings_header = _dialog_header(
            "Settings", "Configure Ember's models, behavior, privacy, and integrations.",
            eyebrow="EMBER", mark="⚙")
        outer.addWidget(self._settings_header)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(False)
        tab_bar = self.tabs.tabBar()
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        tab_bar.setExpanding(True)
        outer.addWidget(self.tabs)

        # Build each tab defensively: if one builder raises, the dialog still opens with the rest
        # (and we record which failed) instead of an exception bubbling up to the slot — which on
        # PyQt6 would ABORT the whole app ("Ember quit unexpectedly").
        self._tab_errors = []
        for _label, _builder in (
            ("Models", self._build_models_tab),
            ("Appearance", self._build_appearance_tab),
            ("Voice", self._build_voice_tab),
            ("Performance", self._build_performance_tab),
            ("Automations", self._build_automations_tab),
            ("Memory", self._build_memory_tab),
            ("Security", self._build_security_tab),
            ("About", self._build_about_tab),
        ):
            try:
                _builder()
            except Exception as _e:
                import traceback
                traceback.print_exc()
                self._tab_errors.append(f"{_label}: {type(_e).__name__}: {_e}")
        if self._tab_errors:
            err_page = QWidget()
            ev = QVBoxLayout(err_page)
            lbl = QLabel("Some settings sections couldn't load:\n\n• " + "\n• ".join(self._tab_errors)
                         + "\n\nThe rest of Settings still works. This was logged to ember-crash.log.")
            lbl.setWordWrap(True)
            ev.addWidget(lbl)
            ev.addStretch()
            self.tabs.addTab(err_page, "⚠ Issues")

        if self._only_tab:
            self._scope_to_tab(self._only_tab)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save changes")
        save_btn.setObjectName("primaryBtn")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

        _polish_dialog(self)

    # --- layout helpers (keep every tab tidy + prevent clipped/elided text) ---
    def _new_form(self):
        """Return a fresh QFormLayout whose fields EXPAND to fill the width.

        macOS's QFormLayout default (FieldsStayAtSizeHint) keeps inputs at their
        size hint, which elides long placeholders like 'Optional 2nd backup key'.
        Growing the fields fixes that and uses the dialog's full width."""
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        return form

    def _group(self, title: str) -> tuple:
        """A titled QGroupBox containing an expanding QFormLayout. Returns (box, form)."""
        box = QGroupBox(title)
        form = self._new_form()
        box.setLayout(form)
        return box, form

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #8f99ad; font-size: 11px;")
        lbl.setWordWrap(True)
        return lbl

    def _set_status(self, text: str) -> None:
        """Forward a status message to the main window (best-effort). The settings dialog has no
        status bar of its own, but several handlers (sandbox run, hotkey helper, etc.) report
        progress via _set_status — without this, those calls raised AttributeError mid-toggle."""
        try:
            win = self.parent()
            if win is not None and hasattr(win, "_set_status"):
                win._set_status(text)
        except Exception:
            pass

    def _voice_val(self, combo, default: str) -> str:
        """Read a voice dropdown's value, ignoring the 'Other…' placeholder and blanks."""
        v = (combo.currentText() or "").strip()
        if not v or v == getattr(self, "_CUSTOM_VOICE", None):
            return default
        return v

    def _scope_to_tab(self, only_tab: str):
        """Present one built tab as a standalone feature window: switch to it and hide the tab
        bar so only that section shows. We DON'T remove the other tabs — every widget stays
        parented exactly as built, so the shared get_settings() save path is untouched (zero
        reparenting risk); the user just sees one focused feature window."""
        keep = None
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i).strip().lower() == only_tab.strip().lower():
                keep = i
                break
        if keep is None:
            return  # unknown section -> fall back to the full tabbed dialog
        title = self.tabs.tabText(keep)
        self.tabs.setCurrentIndex(keep)
        try:
            self.tabs.tabBar().hide()
        except Exception:
            pass
        self.setWindowTitle(f"Ember — {title}")
        try:
            self._settings_header.heading_label.setText(title)
            self._settings_header.description_label.setText(
                f"Focused {title.lower()} controls. Changes apply when you save.")
        except Exception:
            pass
        self.setMinimumSize(660, 520)

    def _add_tab(self, page, title: str, scroll: bool = True):
        """Add a tab, optionally wrapped in a scroll area so tall content never clips
        off the bottom of the dialog."""
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(page)
            self.tabs.addTab(area, title)
        else:
            self.tabs.addTab(page, title)

    def _build_models_tab(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 8, 4, 8)
        outer.setSpacing(12)

        # --- API keys ---------------------------------------------------------
        keys_box, layout = self._group("API keys")
        outer.addWidget(keys_box)

        self.gemini_key_input = QLineEdit(self.settings.get("gemini_api_key", ""))
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setPlaceholderText("Get free at aistudio.google.com/apikey")
        layout.addRow("Gemini API key:", self.gemini_key_input)

        self.gemini_key_secondary_input = QLineEdit(self.settings.get("gemini_api_key_secondary", ""))
        self.gemini_key_secondary_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_secondary_input.setPlaceholderText("Optional backup key for same-model failover")
        layout.addRow("Backup Gemini key 1:", self.gemini_key_secondary_input)

        self.gemini_key_3_input = QLineEdit(self.settings.get("gemini_api_key_3", ""))
        self.gemini_key_3_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_3_input.setPlaceholderText("Optional 2nd backup key")
        layout.addRow("Backup Gemini key 2:", self.gemini_key_3_input)

        self.gemini_key_4_input = QLineEdit(self.settings.get("gemini_api_key_4", ""))
        self.gemini_key_4_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_4_input.setPlaceholderText("Optional 3rd backup key")
        layout.addRow("Backup Gemini key 3:", self.gemini_key_4_input)

        self.anthropic_key_input = QLineEdit(self.settings.get("anthropic_api_key", ""))
        self.anthropic_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key_input.setPlaceholderText("Required if Claude is the primary model")
        layout.addRow("Anthropic key:", self.anthropic_key_input)

        self.openai_key_input = QLineEdit(self.settings.get("openai_api_key", ""))
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("For ChatGPT (or a compatible provider's key)")
        layout.addRow("OpenAI key:", self.openai_key_input)

        # "Other API-key providers": pick an OpenAI-compatible endpoint (Grok/DeepSeek/Groq/…)
        # or "OpenAI (ChatGPT)" for the default. Sets base_url so the OpenAI provider talks to it.
        self.openai_provider_combo = QComboBox()
        self._openai_bases = model_catalog.OPENAI_COMPAT_BASES
        _cur_base = (self.settings.get("openai_base_url") or "").strip()
        _cur_base_idx = 0
        for i, (_k, _label, _base, _env) in enumerate(self._openai_bases):
            self.openai_provider_combo.addItem(_label, userData=(_label, _base))
            if _base == _cur_base:
                _cur_base_idx = i
        self.openai_provider_combo.setCurrentIndex(_cur_base_idx)
        layout.addRow("OpenAI-compatible endpoint:", self.openai_provider_combo)
        layout.addRow(self._hint(
            "Use ChatGPT with an OpenAI key, or pick another provider (xAI Grok, DeepSeek, Groq, "
            "Mistral, OpenRouter, local LM Studio/vLLM…) and paste that provider's key above. To "
            "use a custom model id on a compatible endpoint, type it as openai:<model-id> below."))

        self.dual_api_check = QCheckBox("If Gemini is rate-limited, rotate through the backup keys")
        self.dual_api_check.setChecked(bool(self.settings.get("dual_api_failover", True)))
        layout.addRow(self.dual_api_check)

        self.vault_check = QCheckBox("🔒 Store API keys in the encrypted vault (not plaintext settings.json)")
        self.vault_check.setChecked(bool(self.settings.get("use_key_vault", False)))
        layout.addRow(self.vault_check)
        try:
            import key_vault
            _vbk = key_vault.backend()
        except Exception:
            _vbk = "encrypted-file"
        layout.addRow(self._hint(
            f"Keys are encrypted using {'the OS keychain' if _vbk == 'keychain' else 'an encrypted file (Fernet)'}. "
            "When on, settings.json keeps blank keys and the real values live in the vault."))

        # --- Model selection --------------------------------------------------
        model_box, mlayout = self._group("Model")
        outer.addWidget(model_box)

        self.model_combo = QComboBox()
        self._model_options = model_catalog.all_choices()
        current = self.settings.get("model_id") or self.settings.get("gemini_model") or "gemini-3.1-flash-lite"
        # Show plain-language model names for non-experts (e.g. "Free offline AI") so the picker
        # isn't a wall of jargon; experts see the technical id + rate limits.
        _lvl = (self.settings.get("experience_level") or "expert")
        try:
            import setup_tour as _stour
        except Exception:
            _stour = None
        current_idx = 0
        for i, (_provider, mid, name, hint) in enumerate(self._model_options):
            if _stour is not None and _lvl in ("beginner", "some"):
                label = _stour.friendly_model_label(mid, name, _lvl)
            else:
                label = f"{name}  -  {hint}"
            self.model_combo.addItem(label, userData=mid)
            if mid == current:
                current_idx = i
        self.model_combo.setCurrentIndex(current_idx)
        mlayout.addRow("Primary model:", self.model_combo)

        rate_btn = QPushButton("Show free-tier rate limits")
        rate_btn.clicked.connect(self._show_rates)
        mlayout.addRow("", rate_btn)

        self.ai_titles_check = QCheckBox("Auto-name chats with a small AI")
        self.ai_titles_check.setChecked(bool(self.settings.get("ai_chat_titles", True)))
        mlayout.addRow(self.ai_titles_check)

        # Which (cheap) model writes the short chat title — local Ollama or a small free Gemma.
        self.title_model_combo = QComboBox()
        _cur_title = self.settings.get("chat_title_model", model_catalog.DEFAULT_TITLE_MODEL)
        _ti = 0
        for i, (mid, label) in enumerate(model_catalog.TITLE_MODELS):
            self.title_model_combo.addItem(label, userData=mid)
            if mid == _cur_title:
                _ti = i
        self.title_model_combo.setCurrentIndex(_ti)
        self.title_model_combo.setEnabled(self.ai_titles_check.isChecked())
        self.ai_titles_check.toggled.connect(self.title_model_combo.setEnabled)
        mlayout.addRow("Chat-title model:", self.title_model_combo)

        # Local AI (Ollama): pick "Local (Ollama)" as the model above; optionally name a model.
        self.ollama_model_input = QLineEdit(self.settings.get("ollama_model", ""))
        self.ollama_model_input.setPlaceholderText("e.g. llama3.2 (blank = first installed)")
        mlayout.addRow("Ollama model:", self.ollama_model_input)
        ollama_btn = QPushButton("Check local Ollama")
        ollama_btn.clicked.connect(self._check_ollama)
        mlayout.addRow("", ollama_btn)

        mlayout.addRow(self._hint(
            "Gemini 3.1 Flash Lite has the highest free-tier RPD (500/day) and drives Ember's "
            "tools. Gemma models are text-only (no tool-use) but have huge free limits — ideal "
            "for the tiny 'name this chat' job above. Pick 'Local (Ollama)' there to name chats "
            "entirely offline. Pick a Claude model to switch to Anthropic. Local (Ollama) as the "
            "primary model runs fully offline with no key or limits. Install from ollama.com and "
            "`ollama pull llama3.2`."))

        # --- Gmail / Email (one App Password powers both sending mail AND organising the inbox) ---
        gmail_box, glayout = self._group("Gmail / Email")
        outer.addWidget(gmail_box)
        self.gmail_addr_input = QLineEdit(
            self.settings.get("gmail_address") or self.settings.get("email_smtp_user", ""))
        self.gmail_addr_input.setPlaceholderText("you@gmail.com")
        glayout.addRow("Gmail address:", self.gmail_addr_input)
        self.gmail_pw_input = QLineEdit(
            self.settings.get("gmail_app_password") or self.settings.get("email_smtp_password", ""))
        self.gmail_pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gmail_pw_input.setPlaceholderText("16-char Google App Password (not your login password)")
        glayout.addRow("App Password:", self.gmail_pw_input)
        glayout.addRow(self._hint(
            "Lets Ember send email AND organise your inbox (search, label, archive, star, trash). "
            "Turn on 2-Step Verification, then create an App Password at "
            "myaccount.google.com/apppasswords and paste it here. Stored locally; used only to "
            "connect to Gmail (IMAP/SMTP)."))

        outer.addStretch()
        self._add_tab(page, "Models")

    def _check_ollama(self):
        try:
            import local_ai
            st = local_ai.local_ai_status()
        except Exception as e:
            QMessageBox.warning(self, "Local AI", f"Could not check Ollama: {e}")
            return
        if st.get("running"):
            models = st.get("models") or []
            msg = ("Ollama is running ✓\n\nInstalled models:\n  "
                   + ("\n  ".join(models) if models else "(none — run: ollama pull llama3.2)"))
        else:
            msg = (st.get("note")
                   or "Ollama is not running. Install it from https://ollama.com, then run "
                      "`ollama pull llama3.2` and start Ollama.")
        QMessageBox.information(self, "Local AI (Ollama)", msg)

    def _build_appearance_tab(self):
        page = QWidget()
        layout = self._new_form()
        page.setLayout(layout)

        self.animations_check = QCheckBox("Enable interface motion")
        self.animations_check.setChecked(bool(self.settings.get("animations_enabled", True)))
        layout.addRow(self.animations_check)

        self.motion_combo = QComboBox()
        for label, value in (("Dynamic — expressive motion", "dynamic"),
                             ("Smooth — calm motion", "smooth"),
                             ("Reduced — essential motion only", "reduced"),
                             ("Off — no motion", "off")):
            self.motion_combo.addItem(label, userData=value)
        current_motion = str(self.settings.get("motion_level", "dynamic"))
        for i in range(self.motion_combo.count()):
            if self.motion_combo.itemData(i) == current_motion:
                self.motion_combo.setCurrentIndex(i)
                break
        self.motion_combo.setEnabled(self.animations_check.isChecked())
        self.animations_check.toggled.connect(self.motion_combo.setEnabled)
        layout.addRow("Motion style:", self.motion_combo)

        self.autocorrect_check = QCheckBox("Autocorrect ordinary chat prompts before sending")
        self.autocorrect_check.setChecked(bool(self.settings.get("autocorrect_chat", True)))
        layout.addRow(self.autocorrect_check)

        self.show_thinking_check = QCheckBox("Show Ember's thinking (collapsible reasoning)")
        self.show_thinking_check.setChecked(bool(self.settings.get("show_thinking", True)))
        self.show_thinking_check.setToolTip(
            "When the model reasons before answering, show a collapsible “Thinking” note so you "
            "can see how it got there. Turn off to hide it.")
        layout.addRow(self.show_thinking_check)

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
        self._glass_seethru_label = QLabel("See-through:")
        row_g.addWidget(self._glass_seethru_label)
        row_g.addWidget(self.glass_opacity_slider, 1)
        row_g.addWidget(self.glass_opacity_value)
        wrap_g = QWidget()
        wrap_g.setLayout(row_g)
        layout.addRow("  Glass opacity:", wrap_g)

        # The glass slider only does anything when Liquid Glass is ON — grey it out otherwise
        # (so it's clearly inert instead of "pointlessly" moving with no effect).
        def _sync_glass_enabled(on):
            for w in (self.glass_opacity_slider, self.glass_opacity_value, self._glass_seethru_label):
                w.setEnabled(bool(on))
        self.liquid_glass_check.toggled.connect(_sync_glass_enabled)
        _sync_glass_enabled(self.liquid_glass_check.isChecked())

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

        # --- Theme presets: one click sets accent + glass + glow + animations together. ---
        self.theme_preset_combo = QComboBox()
        self.theme_preset_combo.addItem("Custom (current)", userData=None)
        for pname in _THEME_PRESETS:
            self.theme_preset_combo.addItem(pname, userData=pname)
        self.theme_preset_combo.setCurrentIndex(0)
        self.theme_preset_combo.activated.connect(
            lambda _i: self._apply_theme_preset(self.theme_preset_combo.currentData()))
        layout.addRow("Theme preset:", self.theme_preset_combo)

        note = QLabel(
            "Appearance changes apply when you save. Restart Ember via Ember.bat to fully refresh."
        )
        note.setStyleSheet("color: #8f99ad; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)

        self._add_tab(page, "Appearance")

    def _apply_theme_preset(self, key):
        """Apply a named theme preset to the Appearance widgets (does not save until the user
        clicks Save). 'Custom (current)' (key None) is a no-op."""
        preset = _THEME_PRESETS.get(key)
        if not preset:
            return
        # accent: match the preset color to a combo entry by its userData
        for i in range(self.accent_combo.count()):
            if self.accent_combo.itemData(i) == preset["accent_color"]:
                self.accent_combo.setCurrentIndex(i)
                break
        self.glass_opacity_slider.setValue(int(preset["glass_opacity"]))
        self.glow_check.setChecked(bool(preset["glow_enabled"]))
        self.animations_check.setChecked(bool(preset["animations_enabled"]))
        self.liquid_glass_check.setChecked(bool(preset["liquid_glass"]))

    def _build_voice_tab(self):
        page = QWidget()
        layout = self._new_form()
        page.setLayout(layout)

        self.wake_word_check = QCheckBox('Always listen for "Hey Ember" (hands-free wake word)')
        self.wake_word_check.setChecked(bool(self.settings.get("wake_word", True)))
        layout.addRow(self.wake_word_check)

        # Concrete "does my mic work?" diagnostic — the fastest way to see WHY voice is dead
        # (missing pyaudio / no permission / no device) instead of guessing.
        self.mic_test_btn = QPushButton("🎤  Test microphone")
        self.mic_test_btn.clicked.connect(self._test_microphone)
        self.mic_test_status = QLabel("")
        self.mic_test_status.setWordWrap(True)
        self.mic_test_status.setStyleSheet("color: #8f99ad; font-size: 11px;")
        layout.addRow(self.mic_test_btn, self.mic_test_status)

        self.wake_visual_combo = QComboBox()
        for label, val in (("Glow around Ember (recommended)", "glow"),
                           ("Floating orb", "orb")):
            self.wake_visual_combo.addItem(label, userData=val)
        cur_wv = (self.settings.get("wake_visual", "glow") or "glow").lower()
        for i in range(self.wake_visual_combo.count()):
            if self.wake_visual_combo.itemData(i) == cur_wv:
                self.wake_visual_combo.setCurrentIndex(i)
        self.wake_visual_combo.setToolTip(
            "What to show when you say 'Hey Ember': the Siri-style glow around the Ember window, "
            "or a separate floating orb. Neither brings the app to the front.")
        layout.addRow("“Hey Ember” shows:", self.wake_visual_combo)

        self.glow_anim_check = QCheckBox("Siri-style glow animation while listening / thinking / speaking")
        self.glow_anim_check.setChecked(bool(self.settings.get("glow_animation", True)))
        layout.addRow(self.glow_anim_check)

        self.voice_check = QCheckBox("Speak assistant replies aloud")
        self.voice_check.setChecked(bool(self.settings.get("voice_output", False)))
        layout.addRow(self.voice_check)

        # --- Text-to-speech voice/engine ---
        self.tts_engine_combo = QComboBox()
        for label, val in (("System voice (free, built-in)", "system"),
                           ("Edge Neural TTS — natural & free, NO key needed (recommended)", "edge"),
                           ("Gemini TTS — most natural (uses your Gemini key; rate-limited)", "gemini"),
                           ("Custom HTTP endpoint (advanced)", "soundtools")):
            self.tts_engine_combo.addItem(label, userData=val)
        cur_tts = self.settings.get("tts_engine", "system")
        for i in range(self.tts_engine_combo.count()):
            if self.tts_engine_combo.itemData(i) == cur_tts:
                self.tts_engine_combo.setCurrentIndex(i)
        layout.addRow("Read-aloud voice:", self.tts_engine_combo)

        CUSTOM_VOICE = "✏️  Other (type a name)…"
        self._CUSTOM_VOICE = CUSTOM_VOICE

        def _voice_picker(options, current, tip=""):
            """A real dropdown of known voices (matches the other Settings dropdowns), with an
            'Other…' item to type a custom voice when you need one outside the list."""
            cb = QComboBox()                       # non-editable -> a clear dropdown with an arrow
            cb.addItems(options)
            cur = (current or "").strip()
            if cur and cur not in options:
                cb.insertItem(0, cur)              # surface a previously-saved custom voice
            cb.addItem(CUSTOM_VOICE)
            cb.setCurrentText(cur if cur else (options[0] if options else ""))
            if tip:
                cb.setToolTip(tip)

            def _maybe_custom(idx, _cb=cb):
                if _cb.itemText(idx) != CUSTOM_VOICE:
                    return
                from PyQt6.QtWidgets import QInputDialog
                text, ok = QInputDialog.getText(self, "Custom voice", "Voice name:")
                name = (text or "").strip()
                if ok and name:
                    if _cb.findText(name) < 0:
                        _cb.insertItem(_cb.count() - 1, name)   # insert above the 'Other…' item
                    _cb.setCurrentText(name)
                else:
                    _cb.setCurrentIndex(0)
            cb.currentIndexChanged.connect(_maybe_custom)
            return cb

        self.edge_voice_input = _voice_picker(
            ["en-US-AriaNeural", "en-US-JennyNeural", "en-US-EmmaNeural", "en-US-AvaNeural",
             "en-US-GuyNeural", "en-US-AndrewNeural", "en-US-BrianNeural", "en-GB-SoniaNeural",
             "en-GB-LibbyNeural", "en-GB-RyanNeural", "en-AU-NatashaNeural", "en-AU-WilliamNeural",
             "en-CA-ClaraNeural", "en-IN-NeerjaNeural", "en-IE-EmilyNeural"],
            self.settings.get("edge_tts_voice", "en-US-AriaNeural"),
            tip=("Microsoft Edge neural voices — free, no API key, not rate-limited. Needs the "
                 "'edge-tts' package (pip install edge-tts). Falls back to the system voice if missing."))
        layout.addRow("Edge voice:", self.edge_voice_input)

        self.gemini_voice_input = _voice_picker(
            ["Kore", "Puck", "Charon", "Aoede", "Zephyr", "Fenrir", "Leda", "Orus", "Callirrhoe",
             "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome",
             "Algenib", "Rasalgethi", "Laomedeia", "Achernar", "Schedar", "Gacrux", "Sulafat"],
            self.settings.get("gemini_tts_voice", "Kore"),
            tip="Gemini TTS prebuilt voice (used when Read-aloud voice = Gemini TTS).")
        layout.addRow("Gemini voice:", self.gemini_voice_input)

        # soundtools.io has no public API key, so the old key field is gone. This is now an
        # optional CUSTOM endpoint URL for power users running their own/any HTTP TTS service.
        self.soundtools_url_input = QLineEdit(self.settings.get("soundtools_url", ""))
        self.soundtools_url_input.setPlaceholderText(
            "Custom TTS endpoint URL (optional, for 'Custom HTTP endpoint') — leave blank")
        layout.addRow("Custom TTS URL:", self.soundtools_url_input)

        # --- Natural voice (Gemini Live API, real-time full-duplex) ---
        self.live_voice_check = QCheckBox(
            "Natural voice for Voice Chat (Gemini Live API — real-time, understands tone/accent)")
        self.live_voice_check.setChecked(bool(self.settings.get("live_voice_enabled", False)))
        self.live_voice_check.setToolTip(
            "Streams a live conversation instead of record→transcribe→speak: it hears HOW you "
            "talk, replies in a natural neural voice, supports barge-in, and isn't capped by the "
            "per-message rate limit. Needs a Gemini key + a mic backend (sounddevice, installed "
            "by default; PyAudio also works).")
        layout.addRow(self.live_voice_check)

        self.live_voice_voice_input = _voice_picker(
            ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus"],
            self.settings.get("live_voice_voice", "Zephyr"),
            tip="Gemini Live API (native-audio) voice for real-time natural voice chat.")
        layout.addRow("Natural voice name:", self.live_voice_voice_input)

        self.voice_chat_reply_check = QCheckBox("Voice chat always speaks replies")
        self.voice_chat_reply_check.setChecked(bool(self.settings.get("voice_chat_spoken_replies", True)))
        layout.addRow(self.voice_chat_reply_check)

        self.voice_auto_send_check = QCheckBox("Send each voice-chat transcript automatically")
        self.voice_auto_send_check.setChecked(bool(self.settings.get("voice_chat_auto_send", True)))
        layout.addRow(self.voice_auto_send_check)

        self.voice_continue_check = QCheckBox("Keep listening after silence or unclear audio")
        self.voice_continue_check.setChecked(bool(self.settings.get("voice_chat_continue_after_silence", True)))
        layout.addRow(self.voice_continue_check)

        self.voice_phrase_combo = QComboBox()
        self.voice_phrase_combo.addItem("Auto — stop when I pause (recommended)", userData="auto")
        for sec in (4, 6, 8, 10, 15, 20):
            self.voice_phrase_combo.addItem(f"Up to {sec} seconds", userData=str(sec))
        _cur_vt = str(self.settings.get("voice_chat_phrase_timeout", "auto")).strip().lower()
        _vt_idx = 0
        for i in range(self.voice_phrase_combo.count()):
            if self.voice_phrase_combo.itemData(i) == _cur_vt:
                _vt_idx = i
                break
        self.voice_phrase_combo.setCurrentIndex(_vt_idx)
        self.voice_phrase_combo.setToolTip(
            "How long one spoken turn can last in Voice Chat. Auto (recommended) listens until "
            "you naturally pause, so it never cuts you off — pick a fixed cap only if you want a "
            "hard limit.")
        layout.addRow("Voice turn length:", self.voice_phrase_combo)

        note = QLabel(
            "Auto stops listening when you pause talking. Natural voice (Live API) also lets the "
            "AI decide turn-taking. Voice chat uses the same brain as typed chat; mic permission "
            "is required."
        )
        note.setStyleSheet("color: #8f99ad; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)

        # --- Push-to-talk: hold a key to talk (zero-latency, no wake word) ---
        ptt_head = QLabel("Push-to-talk")
        ptt_head.setStyleSheet("font-weight: 700; margin-top: 8px;")
        layout.addRow(ptt_head)

        self.ptt_check = QCheckBox("Hold a key to talk (instant — no “Hey Ember” needed)")
        self.ptt_check.setChecked(bool(self.settings.get("push_to_talk", False)))
        layout.addRow(self.ptt_check)

        self.ptt_key_input = _KeyCaptureEdit(self.settings.get("push_to_talk_key", "f9"), chord=False)
        layout.addRow("Push-to-talk key:", self.ptt_key_input)

        self.stt_engine_combo = QComboBox()
        for val, label in (("auto", "Auto (recommended)"),
                           ("whisper", "On-device — private & offline (Whisper)"),
                           ("gemini", "Cloud — uses your Gemini key"),
                           ("google", "Free online (Google)")):
            self.stt_engine_combo.addItem(label, userData=val)
        _cur_stt = str(self.settings.get("stt_engine", "auto")).strip().lower()
        for i in range(self.stt_engine_combo.count()):
            if self.stt_engine_combo.itemData(i) == _cur_stt:
                self.stt_engine_combo.setCurrentIndex(i)
                break
        layout.addRow("Transcription engine:", self.stt_engine_combo)

        self.whisper_model_combo = QComboBox()
        for val, label in (("tiny", "tiny — fastest, lowest accuracy"),
                           ("base", "base — fast (recommended)"),
                           ("small", "small — slower, more accurate"),
                           ("medium", "medium — slow, high accuracy")):
            self.whisper_model_combo.addItem(label, userData=val)
        _cur_wm = str(self.settings.get("whisper_model", "base")).strip().lower()
        for i in range(self.whisper_model_combo.count()):
            if self.whisper_model_combo.itemData(i) == _cur_wm:
                self.whisper_model_combo.setCurrentIndex(i)
                break
        layout.addRow("Local Whisper model:", self.whisper_model_combo)

        ptt_note = QLabel(
            "Hold the key, speak, release — Ember transcribes and sends it instantly, with no "
            "wake-word wait or false triggers. For private offline transcription install "
            "faster-whisper (pip install faster-whisper); otherwise it uses your Gemini key or "
            "free Google speech. macOS needs Accessibility + Microphone permission."
        )
        ptt_note.setStyleSheet("color: #8f99ad; font-size: 11px;")
        ptt_note.setWordWrap(True)
        layout.addRow(ptt_note)

        self._add_tab(page, "Voice")

    def _test_microphone(self):
        """One-click 'does my mic actually work?' — opens the device and captures a moment of
        audio, then reports EXACTLY what's wrong (missing pyaudio / no permission / no device) or
        confirms it works. This is the fastest path out of 'voice does nothing and I don't know why'."""
        self.mic_test_btn.setEnabled(False)
        self.mic_test_status.setStyleSheet("color: #8f99ad; font-size: 11px;")
        self.mic_test_status.setText("Testing… say a few words.")
        try:
            QApplication.processEvents()
        except Exception:
            pass
        try:
            import voice
            ok, detail = voice.mic_available()
            if not ok:
                hint = detail
                if "pyaudio" in detail.lower() or "sounddevice" in detail.lower():
                    hint = ("Microphone input could not start.\n"
                            "Repair Ember's portable backend, then reopen it:\n"
                            "  pip install --upgrade sounddevice SpeechRecognition\n"
                            "PyAudio remains an optional alternate backend.")
                self.mic_test_status.setStyleSheet("color: #f7768e; font-size: 11px;")
                self.mic_test_status.setText("❌ " + hint)
                return
            text, err = self._quick_capture()
            if err:
                self.mic_test_status.setStyleSheet("color: #e0af68; font-size: 11px;")
                self.mic_test_status.setText("⚠️ Mic opened but capture failed: " + err)
            elif text:
                self.mic_test_status.setStyleSheet("color: #9ece6a; font-size: 11px;")
                self.mic_test_status.setText(f"✅ Microphone works — I heard: “{text}”")
            else:
                self.mic_test_status.setStyleSheet("color: #9ece6a; font-size: 11px;")
                self.mic_test_status.setText("✅ Microphone works (captured audio). If ‘Hey Ember’ "
                                             "still won’t trigger, speak a bit louder/closer.")
        except Exception as e:
            self.mic_test_status.setStyleSheet("color: #f7768e; font-size: 11px;")
            self.mic_test_status.setText(f"❌ {type(e).__name__}: {e}")
        finally:
            self.mic_test_btn.setEnabled(True)

    def _quick_capture(self):
        """Listen briefly and try to transcribe. Returns (text, error). error='' means audio was
        captured even if it couldn't be transcribed (e.g. offline)."""
        try:
            import speech_recognition as sr
        except Exception as e:
            return "", f"SpeechRecognition not installed ({e})"
        try:
            r = sr.Recognizer()
            with sr.Microphone() as src:
                r.adjust_for_ambient_noise(src, duration=0.3)
                audio = r.listen(src, timeout=4, phrase_time_limit=3)
        except Exception as e:
            return "", str(e)
        try:
            return r.recognize_google(audio), ""      # best-effort; free + no key
        except Exception:
            return "", ""                              # captured, just couldn't transcribe offline

    def _setup_mcp_claude(self):
        self._start_mcp_setup("claude")

    def _setup_mcp_chatgpt(self):
        self._start_mcp_setup("chatgpt")

    def _start_mcp_setup(self, client: str):
        """Install/start MCP off the UI thread so Settings never freezes during pip work."""
        for button in (getattr(self, "mcp_chatgpt_btn", None),
                       getattr(self, "mcp_setup_btn", None)):
            if button is not None:
                button.setEnabled(False)
        self.mcp_setup_status.setStyleSheet("color: #8f99ad; font-size: 11px;")
        self.mcp_setup_status.setText(
            f"Setting up {client.title()}… installing/verifying the free MCP runtime.")
        def work():
            try:
                import mcp_setup
                res = (mcp_setup.start_chatgpt_mcp() if client == "chatgpt"
                       else mcp_setup.setup_claude_desktop())
            except Exception as exc:
                res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._mcp_setup_done.emit(client, res)
        threading.Thread(target=work, daemon=True).start()

    def _on_mcp_setup_done(self, client: str, res: dict):
        if res.get("ok"):
            try:
                self.mcp_bridge_check.setChecked(True)   # ensure the bridge turns on when saved
            except Exception:
                pass
            self.mcp_setup_status.setStyleSheet("color: #9ece6a; font-size: 11px;")
            if client == "chatgpt":
                self.mcp_setup_status.setText(
                    "ChatGPT MCP is running locally with every Ember tool. In ChatGPT developer "
                    "mode, create an app with Secure MCP Tunnel and use:\n"
                    f"{res.get('url')}\nRefresh its metadata after Ember updates.")
            else:
                self.mcp_setup_status.setText(
                    "Claude Desktop is configured. Click Save, fully quit and reopen Claude "
                    f"Desktop. Config: {res.get('config')}")
        else:
            self.mcp_setup_status.setStyleSheet("color: #f7768e; font-size: 11px;")
            self.mcp_setup_status.setText(str(res.get("error", "setup failed")))
        for button in (getattr(self, "mcp_chatgpt_btn", None),
                       getattr(self, "mcp_setup_btn", None)):
            if button is not None:
                button.setEnabled(True)

    def _build_performance_tab(self):
        page = QWidget()
        layout = self._new_form()
        page.setLayout(layout)

        self.auto_shot_check = QCheckBox("Let Ember view the screen when it decides it needs to")
        self.auto_shot_check.setChecked(bool(self.settings.get("auto_screenshot", True)))
        self.auto_shot_check.setToolTip(
            "On: Ember decides per task whether to take a screenshot (no more capturing just "
            "because a message mentions the screen).\nOff: Ember never views the screen — it "
            "uses the browser, files, and shell only.")
        layout.addRow(self.auto_shot_check)

        self.remote_autostart_check = QCheckBox(
            "Start Ember Link (phone control) automatically when Ember opens")
        self.remote_autostart_check.setChecked(bool(self.settings.get("remote_autostart", True)))
        layout.addRow(self.remote_autostart_check)

        self.mcp_bridge_check = QCheckBox(
            "MCP bridge — expose Ember's tools to your MCP client (Claude Desktop, ChatGPT, and others)")
        self.mcp_bridge_check.setChecked(bool(self.settings.get("mcp_bridge_enabled", False)))
        self.mcp_bridge_check.setToolTip(
            "Exposes Ember's tools over the Model Context Protocol on a loopback-only, "
            "token-secured local port. Point your MCP client at ember_mcp_server.py (see "
            "docs/MCP.md). Off by default.")
        layout.addRow(self.mcp_bridge_check)

        self.mcp_highrisk_check = QCheckBox(
            "Allow high-risk tools over MCP (no in-app confirmation) — advanced")
        self.mcp_highrisk_check.setChecked(bool(self.settings.get("mcp_bridge_allow_high_risk", False)))
        self.mcp_highrisk_check.setToolTip(
            "Off (recommended): high-risk actions are blocked over MCP since there's no human "
            "to approve them there. On: they run unattended — only enable if you trust the client.")
        layout.addRow(self.mcp_highrisk_check)

        # Both integrations get equal visual weight — neither client is the "main character".
        self.mcp_setup_btn = QPushButton("Set up Claude Desktop")
        self.mcp_setup_btn.setObjectName("mcpBtn")
        self.mcp_setup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mcp_setup_btn.clicked.connect(self._setup_mcp_claude)
        self.mcp_chatgpt_btn = QPushButton("Set up ChatGPT")
        self.mcp_chatgpt_btn.setObjectName("mcpBtn")
        self.mcp_chatgpt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mcp_chatgpt_btn.clicked.connect(self._setup_mcp_chatgpt)
        self.mcp_setup_status = QLabel("")
        self.mcp_setup_status.setWordWrap(True)
        self.mcp_setup_status.setStyleSheet("color: #8f99ad; font-size: 11px;")
        mcp_buttons = QHBoxLayout()
        mcp_buttons.setSpacing(9)
        mcp_buttons.addWidget(self.mcp_setup_btn)
        mcp_buttons.addWidget(self.mcp_chatgpt_btn)
        layout.addRow(mcp_buttons)
        layout.addRow(self.mcp_setup_status)

        self.keep_bg_check = QCheckBox(
            "Keep running in the background when closed (so “Hey Ember” still works)")
        self.keep_bg_check.setChecked(bool(self.settings.get("keep_running_in_background", True)))
        self.keep_bg_check.setToolTip(
            "On: closing the window hides Ember to the menu-bar/tray icon and it keeps "
            "listening for the wake word. Quit fully from the tray icon’s Quit.")
        layout.addRow(self.keep_bg_check)

        self.launch_login_check = QCheckBox(
            "Launch Ember at login && keep it running (true always-on “Hey Ember”)")
        try:
            import autostart
            self.launch_login_check.setChecked(autostart.is_installed())
        except Exception:
            self.launch_login_check.setChecked(bool(self.settings.get("launch_at_login", False)))
        self.launch_login_check.setToolTip(
            "Installs a login item (macOS LaunchAgent / Windows Run key / Linux autostart) so "
            "Ember starts at login and is always ready to hear the wake word.")
        self.launch_login_check.stateChanged.connect(self._toggle_launch_at_login)
        layout.addRow(self.launch_login_check)

        self.auto_update_check = QCheckBox(
            "Automatically check for Ember updates on launch")
        self.auto_update_check.setChecked(bool(self.settings.get("auto_update", True)))
        layout.addRow(self.auto_update_check)

        self.hotkey_input = _KeyCaptureEdit(self.settings.get("hotkey", "ctrl+shift+space"), chord=True)
        layout.addRow("Global summon hotkey:", self.hotkey_input)

        self.hotkey_daemon_check = QCheckBox(
            "Make the hotkey work even when Ember is fully quit")
        try:
            import hotkey_daemon
            self.hotkey_daemon_check.setChecked(hotkey_daemon.is_installed())
        except Exception:
            self.hotkey_daemon_check.setChecked(bool(self.settings.get("hotkey_daemon", False)))
        self.hotkey_daemon_check.setToolTip(
            "Installs a tiny always-on login helper that listens for the shortcut and brings "
            "Ember forward (launching it if needed) — so the hotkey works after you Quit, not "
            "just while Ember is open. macOS needs Input Monitoring permission for the helper.")
        self.hotkey_daemon_check.stateChanged.connect(self._toggle_hotkey_daemon)
        layout.addRow(self.hotkey_daemon_check)

        self.timeout_input = QLineEdit(str(self.settings.get("request_timeout_seconds", 15)))
        self.timeout_input.setPlaceholderText("seconds, 10-60 (lower = faster failover)")
        layout.addRow("API request timeout (s):", self.timeout_input)

        self.lean_tools_check = QCheckBox(
            "Compact built-in model tool list (MCP still exposes every tool)")
        self.lean_tools_check.setChecked(bool(self.settings.get("lean_tools", False)))
        self.lean_tools_check.setToolTip(
            "Off by default: Ember's built-in model receives every tool. Turn this on only if a "
            "provider rejects very large tool lists. External MCP clients always receive all tools.")
        layout.addRow(self.lean_tools_check)

        self.offline_mode_check = QCheckBox("Offline Mode — no internet (local AI + local tools only)")
        self.offline_mode_check.setChecked(bool(self.settings.get("offline_mode", False)))
        self.offline_mode_check.setToolTip(
            "Run with no internet: local brain (Ollama), offline voice, and all local tools "
            "(files, shell, screen, system info). Web/search/email and cloud AI fail fast with a "
            "clear notice instead of hanging, and Ember makes no outbound calls (no update check, "
            "cloud sync, or VirusTotal). Switch the model to Ollama for a fully-offline brain.")
        self.offline_mode_check.stateChanged.connect(self._toggle_offline_mode)
        layout.addRow(self.offline_mode_check)
        # (Real-time download protection lives on the Security tab — it's on by default there.)

        usage_btn = QPushButton("📊 Show usage dashboard (calls / tokens vs free-tier limits)")
        usage_btn.clicked.connect(self._show_usage_dashboard)
        layout.addRow("", usage_btn)

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
        note.setStyleSheet("color: #8f99ad; font-size: 11px;")
        note.setWordWrap(True)
        layout.addRow(note)
        self._add_tab(page, "Performance")

    def _build_automations_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)

        head = QLabel(
            "Background rules. When the trigger fires, Ember runs the action automatically - "
            "no API calls needed. Edit automations.json next to the exe for advanced tweaks."
        )
        head.setStyleSheet("color: #8f99ad; font-size: 11px;")
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
        head.setStyleSheet("color: #8f99ad; font-size: 11px;")
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
        """Security controls: malware protection, web protection, agent mode, VPN, and audit."""
        from PyQt6.QtWidgets import QScrollArea
        page = QWidget()
        v = QVBoxLayout(page)
        try:
            self._populate_security_tab(v)
        except Exception as e:
            v.addWidget(QLabel(f"Security panel unavailable: {e}"))
        # The panel is taller than the dialog — wrap it so the lower sections (VPN, audit)
        # are reachable by scrolling instead of being clipped off the bottom.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, "Security")

    def _populate_security_tab(self, v):
        import antivirus, web_policy, safety, audit, vpn

        dash_row = QHBoxLayout()
        dash_btn = QPushButton("🛡️  Security dashboard")
        dash_btn.setObjectName("send")
        dash_btn.clicked.connect(self._show_security_dashboard)
        dash_row.addWidget(dash_btn)
        upd_btn = QPushButton("Check for software updates")
        upd_btn.clicked.connect(self._check_software_updates)
        dash_row.addWidget(upd_btn)
        dash_row.addStretch()
        v.addLayout(dash_row)

        def _section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet("color:#8f99ad; font-size:11px; margin-top:8px;")
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

        self._sec_ai_hold = QCheckBox("AI-scan unconfirmed files and hold them until I confirm")
        self._sec_ai_hold.setChecked(bool(cfg.get("ai_scan_on_open", True))
                                     and bool(cfg.get("require_confirm_unconfirmed", True)))
        self._sec_ai_hold.setToolTip(
            "Before opening an unconfirmed executable/script (or anything the scanner flags), "
            "Ember runs an AI second-opinion scan and BLOCKS opening until you confirm the file "
            "is safe. Confirmed files are remembered by content hash and open normally after.")
        self._sec_ai_hold.stateChanged.connect(
            lambda s: antivirus.set_config(ai_scan_on_open=bool(s),
                                           require_confirm_unconfirmed=bool(s)))
        v.addWidget(self._sec_ai_hold)

        st = antivirus.security_status()
        eng = QLabel("Engines: " + ", ".join(st.get("engines_available", []))
                     + f"   ·   Sandbox: {st.get('sandbox_available')}")
        eng.setStyleSheet("color:#8f99ad; font-size:11px;")
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
        sandbox_btn = QPushButton("Run a file in sandbox…")
        sandbox_btn.clicked.connect(self._run_in_sandbox_ui)
        row.addWidget(sandbox_btn)
        row.addStretch()
        v.addLayout(row)

        # --- Real-time protection ---
        _section("Real-time protection")
        self._sec_dl_guard = QCheckBox("Real-time download protection (auto-scan new downloads)")
        self._sec_dl_guard.setChecked(bool(self.settings.get("download_protection", True)))
        self._sec_dl_guard.stateChanged.connect(self._toggle_download_guard)
        v.addWidget(self._sec_dl_guard)

        self._sec_fileless = QCheckBox(
            "Fileless-malware protection (monitor processes for in-memory / LOLBin attacks)")
        self._sec_fileless.setChecked(bool(self.settings.get("fileless_protection", True)))
        self._sec_fileless.stateChanged.connect(self._toggle_fileless_guard)
        v.addWidget(self._sec_fileless)

        self._sec_ioc = QCheckBox(
            "Deep static analysis (entropy + behavioral IOC signatures on files)")
        self._sec_ioc.setChecked(bool(cfg.get("ioc_scan", True)) and bool(cfg.get("entropy_scan", True)))
        self._sec_ioc.stateChanged.connect(
            lambda s: antivirus.set_config(ioc_scan=bool(s), entropy_scan=bool(s)))
        v.addWidget(self._sec_ioc)

        self._sec_autokill = QCheckBox(
            "Auto-terminate confirmed-malicious processes (uncheck for alert-only)")
        self._sec_autokill.setChecked(bool(cfg.get("fileless_auto_terminate", True)))
        self._sec_autokill.stateChanged.connect(
            lambda s: antivirus.set_config(fileless_auto_terminate=bool(s)))
        v.addWidget(self._sec_autokill)

        rt_row = QHBoxLayout()
        try:
            import fileless_guard
            rt_state = "running" if fileless_guard.is_running() else "stopped"
        except Exception:
            rt_state = "unavailable"
        self._sec_fileless_lbl = QLabel(f"Process monitor: {rt_state}")
        self._sec_fileless_lbl.setStyleSheet("color:#8f99ad; font-size:11px;")
        rt_row.addWidget(self._sec_fileless_lbl)
        proc_btn = QPushButton("Scan running processes now")
        proc_btn.clicked.connect(self._scan_processes_ui)
        rt_row.addWidget(proc_btn)
        rt_row.addStretch()
        v.addLayout(rt_row)

        # --- Emergency lockdown ("panic button") ---
        _section("Emergency lockdown — a hard local safety boundary")
        self._sec_auto_lockdown = QCheckBox(
            "Auto-lockdown on a CRITICAL threat (instantly stop AI, cut network, lock screen)")
        self._sec_auto_lockdown.setChecked(bool(self.settings.get("auto_lockdown_on_critical", False)))
        self._sec_auto_lockdown.setToolTip(
            "If Ember detects a confirmed-malicious event, it immediately stops its own AI, turns "
            "off Wi-Fi, and locks the screen. Off by default; review this response policy before enabling.")
        self._sec_auto_lockdown.stateChanged.connect(self._toggle_auto_lockdown)
        v.addWidget(self._sec_auto_lockdown)
        panic_row = QHBoxLayout()
        panic_btn = QPushButton("🚨  Lock down now")
        panic_btn.setObjectName("deny")
        panic_btn.clicked.connect(self._panic_now)
        panic_row.addWidget(panic_btn)
        restore_btn = QPushButton("Restore network")
        restore_btn.clicked.connect(self._panic_restore)
        panic_row.addWidget(restore_btn)
        panic_row.addStretch()
        v.addLayout(panic_row)

        # --- Security Center (unified active scanning) ---
        _section("Security Center (active scanning: processes · files · network · persistence)")
        self._sec_center = QCheckBox(
            "Always-on Security Center (continuous multi-surface scanning + self-healing watchdog)")
        self._sec_center.setChecked(bool(self.settings.get("realtime_security_center", True)))
        self._sec_center.stateChanged.connect(self._toggle_security_center)
        v.addWidget(self._sec_center)

        self._sec_center_lbl = QLabel(self._security_center_summary())
        self._sec_center_lbl.setStyleSheet("color:#8f99ad; font-size:11px;")
        self._sec_center_lbl.setWordWrap(True)
        v.addWidget(self._sec_center_lbl)

        sc_row = QHBoxLayout()
        for label, handler in (("Full scan now", self._full_scan_ui),
                               ("Scan network", self._scan_network_ui),
                               ("Scan persistence", self._scan_persistence_ui),
                               ("Activity…", self._show_security_events)):
            b = QPushButton(label)
            b.clicked.connect(handler)
            sc_row.addWidget(b)
        sc_row.addStretch()
        v.addLayout(sc_row)

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
        mhint.setStyleSheet("color:#8f99ad; font-size:11px;")
        mhint.setWordWrap(True)
        v.addWidget(mhint)

        # --- Run mode (how autonomously Ember acts) ---
        _section("Run mode")
        rmrow = QHBoxLayout()
        self._run_mode_combo = QComboBox()
        self._run_mode_combo.addItems(["auto", "plan", "chat", "read_only"])
        try:
            import agents as _ag
            self._run_mode_combo.setCurrentText(_ag.get_run_mode())
        except Exception:
            pass
        self._run_mode_combo.currentTextChanged.connect(self._set_run_mode)
        rmrow.addWidget(self._run_mode_combo)
        agents_btn = QPushButton("Agents…")
        agents_btn.clicked.connect(self._show_agents)
        rmrow.addWidget(agents_btn)
        rmrow.addStretch()
        v.addLayout(rmrow)
        rmhint = QLabel("auto = autonomous · plan = propose a plan and wait · "
                        "chat = talk only · read_only = investigate only")
        rmhint.setStyleSheet("color:#8f99ad; font-size:11px;")
        rmhint.setWordWrap(True)
        v.addWidget(rmhint)

        # --- Humanized mouse ---
        _section("Pointer")
        try:
            import human_mouse
            self._ember_pointer_chk = QCheckBox("Show Ember's own pointer while it acts")
            self._ember_pointer_chk.setChecked(bool(
                self.settings.get("show_ember_pointer", True)))
            self._ember_pointer_chk.stateChanged.connect(self._on_ember_pointer_toggled)
            self._ember_pointer_chk.setToolTip(
                "A click-through Ember pointer makes the agent's actions easy to distinguish")
            v.addWidget(self._ember_pointer_chk)

            self._human_mouse_chk = QCheckBox("Human-like mouse movement (curved, eased, natural)")
            self._human_mouse_chk.setChecked(bool(human_mouse.get_options().get("enabled", True)))
            self._human_mouse_chk.stateChanged.connect(self._on_mouse_humanize_toggled)
            v.addWidget(self._human_mouse_chk)

            # Mouse movement speed (0.25x slow & deliberate → 3.0x snappy). Applies live.
            cur_speed = float(self.settings.get("mouse_speed",
                                                human_mouse.get_options().get("speed", 1.0)))
            self._mouse_speed_slider = QSlider(Qt.Orientation.Horizontal)
            self._mouse_speed_slider.setRange(25, 300)   # value/100 = speed multiplier
            self._mouse_speed_slider.setValue(int(round(max(0.25, min(3.0, cur_speed)) * 100)))
            self._mouse_speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            self._mouse_speed_slider.setTickInterval(25)
            self._mouse_speed_value = QLabel(f"{self._mouse_speed_slider.value() / 100:.2f}×")
            self._mouse_speed_slider.valueChanged.connect(self._on_mouse_speed_changed)
            self._mouse_speed_slider.sliderReleased.connect(self._persist_mouse_speed)
            srow = QHBoxLayout()
            slow = QLabel("slow")
            slow.setStyleSheet("color:#8f99ad; font-size:11px;")
            fast = QLabel("fast")
            fast.setStyleSheet("color:#8f99ad; font-size:11px;")
            srow.addWidget(slow)
            srow.addWidget(self._mouse_speed_slider, 1)
            srow.addWidget(fast)
            srow.addWidget(self._mouse_speed_value)
            swrap = QWidget()
            swrap.setLayout(srow)
            mlbl = QLabel("Mouse movement speed")
            mlbl.setStyleSheet("color:#9aa0b4; font-size:12px;")
            v.addWidget(mlbl)
            v.addWidget(swrap)
        except Exception:
            pass

        # --- Notifications / connected channels ---
        _section("Notifications (Slack · Telegram · Discord · webhook)")
        try:
            import integrations
            chans = integrations.list_integrations().get("channels", [])
            self._intg_lbl = QLabel("Connected: " + (", ".join(c["channel"] for c in chans) or "none"))
            self._intg_lbl.setStyleSheet("color:#8f99ad; font-size:11px;")
            v.addWidget(self._intg_lbl)
            irow = QHBoxLayout()
            conn_btn = QPushButton("Connect a channel…")
            conn_btn.clicked.connect(self._connect_integration)
            irow.addWidget(conn_btn)
            test_btn = QPushButton("Send test")
            test_btn.clicked.connect(self._test_integration)
            irow.addWidget(test_btn)
            irow.addStretch()
            v.addLayout(irow)
            self._sc_notify_chk = QCheckBox("Push security threats to my channels")
            self._sc_notify_chk.setChecked(bool(antivirus.get_config().get("sc_notify", False)))
            self._sc_notify_chk.stateChanged.connect(
                lambda s: antivirus.set_config(sc_notify=bool(s)))
            v.addWidget(self._sc_notify_chk)
        except Exception:
            pass

        # --- VPN ---
        _section("VPN (bring-your-own WireGuard)")
        try:
            vs = vpn.status(quick=True)   # no blocking public-IP lookup on the UI thread
            vl = vpn.list_locations()
            locs = vl.get("locations", [])
            vtxt = ("Connected ✓" if vs.get("connected") else "Not connected")
            vtxt += f"   ·   {len(locs)} location(s)"
            if not vl.get("wireguard_installed"):
                vtxt += "   ·   Install the free WireGuard app (Mac App Store) to connect"
            self._vpn_status_lbl = QLabel(vtxt)
            self._vpn_status_lbl.setStyleSheet("color:#8f99ad; font-size:11px;")
            self._vpn_status_lbl.setWordWrap(True)
            v.addWidget(self._vpn_status_lbl)

            self._vpn_combo = QComboBox()
            for loc in locs:
                self._vpn_combo.addItem(loc.get("name", "?"))
            vrow = QHBoxLayout()
            vrow.addWidget(self._vpn_combo, 1)
            for lbl, fn in (("Connect", self._vpn_connect),
                            ("Disconnect", self._vpn_disconnect),
                            ("Add config…", self._vpn_add),
                            ("Get free config", self._vpn_get_free)):
                b = QPushButton(lbl)
                b.clicked.connect(fn)
                vrow.addWidget(b)
            v.addLayout(vrow)
        except Exception as e:
            v.addWidget(QLabel(f"VPN unavailable: {e}"))

        # --- Audit log ---
        _section("Tamper-evident audit log")
        arow = QHBoxLayout()
        averify = QPushButton("Verify audit log")
        averify.clicked.connect(self._verify_audit)
        arow.addWidget(averify)
        arow.addStretch()
        v.addLayout(arow)
        v.addStretch()

    def _refresh_vpn_status(self):
        try:
            import vpn
            vs = vpn.status(quick=True)
            vl = vpn.list_locations()
            if getattr(self, "_vpn_status_lbl", None) is not None:
                t = ("Connected ✓" if vs.get("connected") else "Not connected")
                t += f"   ·   {len(vl.get('locations', []))} location(s)"
                self._vpn_status_lbl.setText(t)
        except Exception:
            pass

    def _vpn_connect(self):
        import vpn
        name = self._vpn_combo.currentText() if getattr(self, "_vpn_combo", None) else ""
        if not name:
            QMessageBox.information(self, "VPN", "Add a WireGuard config first (Add config…).")
            return
        r = vpn.connect(name)
        QMessageBox.information(self, "VPN", "Connected ✓" if r.get("ok") else f"Failed: {r.get('error')}")
        self._refresh_vpn_status()

    def _vpn_disconnect(self):
        import vpn
        r = vpn.disconnect()
        QMessageBox.information(self, "VPN", "Disconnected" if r.get("ok") else f"Failed: {r.get('error')}")
        self._refresh_vpn_status()

    def _vpn_add(self):
        import vpn
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a WireGuard .conf file", "",
                                              "WireGuard config (*.conf);;All files (*)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "VPN location", "Name this location:")
        if not ok or not name.strip():
            return
        r = vpn.add_location(name.strip(), path)
        if r.get("ok"):
            if getattr(self, "_vpn_combo", None) is not None:
                self._vpn_combo.addItem(name.strip())
            QMessageBox.information(self, "VPN", "Added ✓")
        else:
            QMessageBox.warning(self, "VPN", f"Failed: {r.get('error')}")
        self._refresh_vpn_status()

    def _vpn_get_free(self):
        import vpn
        import webbrowser
        fp = vpn.free_providers()
        lines = [fp.get("note", "")]
        for p in fp.get("providers", []):
            lines.append(f"\n• {p['name']}\n  {p['how']}\n  {p['url']}")
        box = QMessageBox(self)
        box.setWindowTitle("Get a free VPN config")
        box.setText("Free WireGuard configs you can add to Ember:")
        box.setInformativeText("\n".join(lines))
        open_btn = box.addButton("Open ProtonVPN free", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn:
            try:
                webbrowser.open("https://protonvpn.com/free-vpn")
            except Exception:
                pass

    def _run_in_sandbox_ui(self):
        """Run a chosen file/app inside the strongest available sandbox (Docker or OS-native
        confinement) so it can't touch the real system. Surfaces antivirus.run_in_sandbox."""
        import antivirus
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file/app to run safely in the sandbox")
        if not path:
            return
        self._set_status("Running in sandbox…")
        r = antivirus.run_in_sandbox(path)
        self._set_status("Ready")
        if r.get("refused"):
            QMessageBox.warning(self, "Sandbox", r.get("message", "Refused: file is malicious."))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Sandbox", r.get("error", "Could not run in the sandbox."))
            return
        parts = [f"Ran via: {r.get('method', 'sandbox')}",
                 f"Result: {r.get('verdict_hint', '?')}",
                 f"Exit code: {r.get('exit_code')}"]
        out = (r.get("stdout") or "").strip()
        err = (r.get("stderr") or "").strip()
        if out:
            parts.append("\nOutput:\n" + out[:800])
        if err:
            parts.append("\nErrors:\n" + err[:400])
        QMessageBox.information(self, "Sandbox result", "\n".join(parts))

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

    def _toggle_download_guard(self, state):
        """Start/stop real-time download protection from the Security tab and persist it."""
        on = bool(state)
        self.settings["download_protection"] = on
        try:
            import download_guard
            r = download_guard.start() if on else download_guard.download_guard_stop()
            if not r.get("ok"):
                QMessageBox.warning(self, "Download protection", r.get("error", "failed"))
        except Exception as e:
            QMessageBox.warning(self, "Download protection", str(e))

    def _toggle_fileless_guard(self, state):
        """Start/stop always-on fileless protection from the Security tab and persist it."""
        on = bool(state)
        self.settings["fileless_protection"] = on
        try:
            import antivirus, fileless_guard
            antivirus.set_config(fileless_protection=on)
            r = fileless_guard.start() if on else fileless_guard.fileless_guard_stop()
            if not r.get("ok"):
                QMessageBox.warning(self, "Fileless protection", r.get("error", "failed"))
            try:
                self._sec_fileless_lbl.setText(
                    f"Process monitor: {'running' if fileless_guard.is_running() else 'stopped'}")
            except Exception:
                pass
        except Exception as e:
            QMessageBox.warning(self, "Fileless protection", str(e))

    def _scan_processes_ui(self):
        """One-shot scan of running processes for fileless-malware behavior."""
        try:
            import fileless_guard
            r = fileless_guard.scan_processes()
        except Exception as e:
            QMessageBox.warning(self, "Process scan", str(e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Process scan", r.get("error", "scan failed"))
            return
        flagged = r.get("flagged", [])
        if not flagged:
            QMessageBox.information(
                self, "Process scan",
                f"Scanned {r.get('scanned', 0)} processes — nothing suspicious found.")
            return
        blocks = []
        for f in flagged[:20]:
            reasons = "; ".join(f.get("reasons", []) or []) or ", ".join(f.get("categories", []))
            blocks.append(f"{f['verdict'].upper()}  {f.get('name')} (pid {f.get('pid')})\n  {reasons}")
        QMessageBox.warning(
            self, "Process scan",
            f"Scanned {r.get('scanned', 0)} processes — flagged "
            f"{r.get('flagged_count', 0)}.\n\n" + "\n\n".join(blocks))

    def _security_center_summary(self) -> str:
        try:
            import security_center
            st = security_center.security_center_status()
            if not st.get("ok"):
                return "Security Center: unavailable"
            bs = st.get("by_source", {})
            return (f"Security Center: {'running' if st.get('running') else 'stopped'} · "
                    f"{st.get('scan_cycles', 0)} scan cycles · {st.get('threats_found', 0)} findings "
                    f"(process {bs.get('process',0)} · file {bs.get('file',0)} · "
                    f"network {bs.get('network',0)} · persistence {bs.get('persistence',0)})")
        except Exception as e:
            return f"Security Center: {e}"

    def _toggle_launch_at_login(self, state):
        on = bool(state)
        self.settings["launch_at_login"] = on
        try:
            import autostart
            r = autostart.set_enabled(on)
            if not r.get("ok"):
                QMessageBox.warning(self, "Launch at login", r.get("error", "failed"))
        except Exception as e:
            QMessageBox.warning(self, "Launch at login", str(e))

    def _on_mouse_humanize_toggled(self, state):
        on = bool(state)
        self.settings["mouse_humanize"] = on
        try:
            import human_mouse
            human_mouse.set_options(enabled=on)
        except Exception:
            pass
        try:
            save_settings(self.settings)   # this tab applies live + sticks immediately
        except Exception:
            pass

    def _on_ember_pointer_toggled(self, state):
        on = bool(state)
        self.settings["show_ember_pointer"] = on
        try:
            import human_mouse
            human_mouse.set_options(show_pointer=on)
        except Exception:
            pass
        try:
            parent = self.parent()
            if parent is not None and hasattr(parent, "_set_ember_pointer_enabled"):
                parent._set_ember_pointer_enabled(on)
            save_settings(self.settings)
        except Exception:
            pass

    def _on_mouse_speed_changed(self, value):
        # Applies live on every tick; persistence happens on release (see _persist_mouse_speed)
        # so dragging doesn't hammer the settings file.
        speed = max(0.25, min(3.0, value / 100.0))
        self.settings["mouse_speed"] = speed
        if hasattr(self, "_mouse_speed_value"):
            self._mouse_speed_value.setText(f"{speed:.2f}×")
        try:
            import human_mouse
            human_mouse.set_options(speed=speed)
        except Exception:
            pass

    def _persist_mouse_speed(self):
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def _toggle_hotkey_daemon(self, state):
        on = bool(state)
        self.settings["hotkey_daemon"] = on
        combo = (self.hotkey_input.value().strip() if hasattr(self, "hotkey_input") else "") or \
            self.settings.get("hotkey", "ctrl+shift+space")
        try:
            import hotkey_daemon
            r = hotkey_daemon.set_enabled(on, combo)
            if not r.get("ok"):
                QMessageBox.warning(self, "Global hotkey helper", r.get("error", "failed"))
            elif on:
                self._set_status("Global hotkey helper installed — works even when Ember is quit.")
        except Exception as e:
            QMessageBox.warning(self, "Global hotkey helper", str(e))

    def _refresh_security_center_lbl(self):
        try:
            self._sec_center_lbl.setText(self._security_center_summary())
        except Exception:
            pass

    def _toggle_security_center(self, state):
        on = bool(state)
        self.settings["realtime_security_center"] = on
        try:
            import antivirus, security_center
            antivirus.set_config(realtime_security_center=on)
            r = security_center.start() if on else security_center.security_center_stop()
            if not r.get("ok"):
                QMessageBox.warning(self, "Security Center", r.get("error", "failed"))
            self._refresh_security_center_lbl()
        except Exception as e:
            QMessageBox.warning(self, "Security Center", str(e))

    def _toggle_auto_lockdown(self, state):
        on = bool(state)
        self.settings["auto_lockdown_on_critical"] = on
        try:
            import panic
            panic.arm_auto(on)
        except Exception:
            pass

    def _panic_now(self):
        if QMessageBox.question(
                self, "Lock down now",
                "Engage emergency lockdown?\n\nThis will immediately:\n"
                "  • stop Ember's AI\n  • turn off Wi-Fi / networking\n  • lock the screen\n\n"
                "Use 'Restore network' afterward to get back online.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            import panic
            r = panic.panic_lockdown(reason="user pressed the panic button", source="manual")
            done = ", ".join(r.get("succeeded", [])) or "nothing"
            failed = r.get("failed", [])
            msg = f"Lockdown engaged: {done}."
            if failed:
                msg += f"\nCouldn't: {', '.join(failed)} (may need admin permission)."
            self._set_status("🚨 Lockdown engaged")
            QMessageBox.information(self, "Lockdown", msg)
        except Exception as e:
            QMessageBox.warning(self, "Lockdown", f"Lockdown failed: {e}")

    def _panic_restore(self):
        try:
            import panic
            panic.restore_network()
            self._set_status("Network restored")
            QMessageBox.information(self, "Network", "Asked the OS to turn networking back on.")
        except Exception as e:
            QMessageBox.warning(self, "Network", str(e))

    # Each failing security check maps to something concrete this button can DO, instead of a
    # static "recommendation" line the user has no way to act on directly. Direct toggles apply
    # right here in Settings → Security; the rest open the dialog that owns that control.
    _SECURITY_RESOLVE_DIRECT = {
        "fileless_protection": "_sec_fileless",
        "web_protection": "_sec_web",
        "safe_open": "_sec_ai_hold",
        "auto_lockdown": "_sec_auto_lockdown",
    }
    _SECURITY_RESOLVE_OPENER = {
        "realtime_protection": "_open_antivirus_app",
        "network_monitoring": "_open_antivirus_app",
        "malware_engine": "_open_antivirus_app",
        "no_active_threats": "_open_antivirus_app",
        "vpn_available": "_open_vpn_manager",
        "password_vault": "_open_passwords_manager",
    }

    def _resolve_security_item(self, key: str, box):
        checkbox_attr = self._SECURITY_RESOLVE_DIRECT.get(key)
        if checkbox_attr and hasattr(self, checkbox_attr):
            getattr(self, checkbox_attr).setChecked(True)
            self._set_status("Fixed — save Settings to keep it.")
            box.accept()
            return
        if key == "updates_current":
            box.accept()
            self._check_software_updates()
            return
        opener = self._SECURITY_RESOLVE_OPENER.get(key)
        parent_w = self.parent()
        if opener and parent_w is not None and hasattr(parent_w, opener):
            box.accept()
            getattr(parent_w, opener)()
            return
        # Every current dashboard signal is covered by the two maps above; this only guards a
        # future new signal key that hasn't been wired to an action yet.
        QMessageBox.information(self, "Security dashboard", "See Settings → Security.")

    def _show_security_dashboard(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import security_suite
            d = security_suite.security_dashboard(check_updates=True)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Security dashboard", str(e))
            return
        QApplication.restoreOverrideCursor()

        box = QDialog(self)
        box.setWindowTitle("Security dashboard")
        box.setMinimumSize(480, 420)
        v = QVBoxLayout(box)
        head = QLabel(f"Security-control coverage: {d.get('score')}/100 ({d.get('rating')})")
        head.setStyleSheet("font-weight:700;font-size:15px;")
        head.setWordWrap(True)
        v.addWidget(head)
        honesty = QLabel(
            "This measures which protection controls are enabled. It is not proof that the "
            "device is safe and it is not a threat count.")
        honesty.setObjectName("dialogStatus")
        honesty.setWordWrap(True)
        v.addWidget(honesty)

        for c in d.get("components", []):
            row = QLabel(("✓ " if c["ok"] else "! ") + c["label"])
            row.setStyleSheet("color:#3ecf6a;" if c["ok"] else "color:#e0af52;")
            v.addWidget(row)

        upd = d.get("updates") or {}
        if upd.get("total"):
            updlbl = QLabel(f"Software Updater: {upd.get('summary')}")
            updlbl.setWordWrap(True)
            updlbl.setStyleSheet("margin-top:8px;")
            v.addWidget(updlbl)

        items = d.get("recommendation_items") or []
        if items:
            rec_head = QLabel("Recommended")
            rec_head.setStyleSheet("font-weight:700;margin-top:10px;")
            v.addWidget(rec_head)
            for it in items[:6]:
                row = QHBoxLayout()
                lab = QLabel(it["fix"])
                lab.setWordWrap(True)
                row.addWidget(lab, 1)
                fix_btn = QPushButton("Fix")
                fix_btn.clicked.connect(lambda _=False, k=it["key"]: self._resolve_security_item(k, box))
                row.addWidget(fix_btn)
                v.addLayout(row)

        close = QPushButton("Close")
        close.clicked.connect(box.accept)
        v.addWidget(close)
        box.exec()

    def _check_software_updates(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            import security_suite
            r = security_suite.software_update_check()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Software updates", str(e))
            return
        QApplication.restoreOverrideCursor()
        os_u = r.get("os_updates", [])
        app_u = r.get("app_updates", [])
        body = [r.get("summary", "")]
        if os_u:
            body += ["", "System:"] + [f"  • {u}" for u in os_u[:15]]
        if app_u:
            body += ["", "Apps:"] + [f"  • {u}" for u in app_u[:25]]
        QMessageBox.information(self, "Software updates", "\n".join(body) or "Up to date.")

    def _apply_offline_mode(self):
        """Publish the Offline Mode flag (from this dialog's settings) so the agent + voice
        honour it. The EmberWindow has its own copy of this; SettingsDialog needs one too because
        the Performance tab + get_settings() call it on `self` (the dialog)."""
        try:
            import offline
            offline.set_offline(bool(self.settings.get("offline_mode", False)))
        except Exception:
            pass

    def _toggle_offline_mode(self, state):
        """Live-track the Offline Mode checkbox into this dialog's settings + the global flag.
        (The user-facing chat note is posted by EmberWindow when the changed setting is saved.)"""
        self.settings["offline_mode"] = bool(state)
        self._apply_offline_mode()

    def _full_scan_ui(self):
        """On-demand full malware sweep of the watched roots (runs off the UI thread)."""
        from PyQt6.QtWidgets import QApplication
        try:
            import security_center
        except Exception as e:
            QMessageBox.warning(self, "Full scan", str(e))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            r = security_center.run_full_scan()
        finally:
            QApplication.restoreOverrideCursor()
        if not r.get("ok"):
            QMessageBox.warning(self, "Full scan", r.get("error", "scan failed"))
            return
        lines = [f"{res['root']}: {res['flagged_count']} flagged / {res['scanned']} scanned"
                 for res in r.get("results", [])]
        QMessageBox.information(
            self, "Full scan complete",
            f"Scanned {r.get('scanned', 0)} files across {r.get('roots', 0)} folders — "
            f"flagged {r.get('flagged_count', 0)}.\n\n" + ("\n".join(lines) or "Nothing flagged."))
        self._refresh_security_center_lbl()

    def _scan_network_ui(self):
        try:
            import security_center
            r = security_center.scan_network()
        except Exception as e:
            QMessageBox.warning(self, "Network scan", str(e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Network scan", r.get("error", "scan failed"))
            return
        flagged = r.get("flagged", [])
        summary = r.get("summary") or (
            f"Scanned {r.get('scanned', 0)} connections — flagged {r.get('flagged_count', 0)}.")
        if flagged:
            body = "\n\n".join(f"{f['severity'].upper()}: {f['detail']}" for f in flagged[:20])
        else:
            # No threats — still show something useful (who you're connected to).
            top = r.get("top_remote") or []
            if top:
                body = "Top remote hosts:\n" + "\n".join(
                    f"  • {t['ip']}  ({t['count']} connection{'s' if t['count'] != 1 else ''})"
                    for t in top)
            else:
                body = "Nothing to report."
        QMessageBox.information(self, "Network scan", f"{summary}\n\n{body}")

    def _scan_persistence_ui(self):
        try:
            import security_center
            r = security_center.scan_persistence()
        except Exception as e:
            QMessageBox.warning(self, "Persistence scan", str(e))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Persistence scan", r.get("error", "scan failed"))
            return
        flagged = r.get("flagged", [])
        detail = "\n\n".join(f"{f['severity'].upper()} {f.get('location')}: {f['detail']}"
                             for f in flagged[:20]) or "No suspicious autostart entries found."
        QMessageBox.information(
            self, "Persistence scan",
            f"Scanned {r.get('scanned', 0)} autostart entries — flagged "
            f"{r.get('flagged_count', 0)}.\n\n{detail}")

    def _show_security_events(self):
        try:
            import security_center
            evs = security_center.security_center_events(limit=40).get("events", [])
        except Exception as e:
            QMessageBox.warning(self, "Security activity", str(e))
            return
        if not evs:
            QMessageBox.information(self, "Security activity",
                                   "No security events recorded yet.")
            return
        lines = [f"[{e.get('time','')}] {e.get('source','').upper()} "
                 f"{e.get('severity','')}: {e.get('detail','')}" for e in evs[-40:]]
        QMessageBox.information(self, "Security activity (recent)", "\n".join(lines))

    def _refresh_integrations(self):
        try:
            import integrations
            chans = integrations.list_integrations().get("channels", [])
            self._intg_lbl.setText("Connected: " + (", ".join(c["channel"] for c in chans) or "none"))
        except Exception:
            pass

    def _connect_integration(self):
        from PyQt6.QtWidgets import QInputDialog
        import integrations
        chan, ok = QInputDialog.getItem(self, "Connect a channel", "Channel:",
                                        list(integrations.CHANNELS.keys()), 0, False)
        if not ok or not chan:
            return
        fields = {}
        for f in integrations.CHANNELS[chan]["fields"]:
            val, ok2 = QInputDialog.getText(self, f"{chan} — {f}", f"Enter {f}:")
            if not ok2 or not val.strip():
                return
            fields[f] = val.strip()
        r = integrations.set_integration(chan, **fields)
        if r.get("ok"):
            self._refresh_integrations()
            QMessageBox.information(self, "Notifications", f"Connected {chan}.")
        else:
            QMessageBox.warning(self, "Notifications", r.get("error", "failed"))

    def _test_integration(self):
        import integrations
        r = integrations.notify("✅ Ember test notification")
        sent = ", ".join(r.get("sent", []) or [])
        QMessageBox.information(self, "Notifications",
                               f"Sent to: {sent}" if sent else
                               "No channel configured (or send failed). Connect one first.")

    def _set_run_mode(self, mode):
        try:
            import agents
            r = agents.set_run_mode(mode)
            if r.get("ok"):
                # Keep the capability-mode combo in sync (run mode sets the capability).
                try:
                    self._sec_mode.setCurrentText(r.get("capability_applied") or self._sec_mode.currentText())
                except Exception:
                    pass
                # Apply to the live agent if one exists.
                ag = getattr(self, "_agent", None) or getattr(self, "agent", None)
                if ag is not None:
                    try:
                        ag.run_mode = mode
                    except Exception:
                        pass
            else:
                QMessageBox.warning(self, "Run mode", r.get("error", "failed"))
        except Exception as e:
            QMessageBox.warning(self, "Run mode", str(e))

    def _show_agents(self):
        try:
            import agents
            lst = agents.list_agents().get("agents", [])
        except Exception as e:
            QMessageBox.warning(self, "Agents", str(e))
            return
        if not lst:
            QMessageBox.information(
                self, "Agents",
                "No saved agents yet.\n\nAsk Ember in chat, e.g.:\n"
                "  \"Create an agent called 'Morning Brief' that summarizes my unread mail "
                "every day at 9am, read-only.\"\n\nOr use the agent_create / agent_run tools.")
            return
        lines = []
        for a in lst:
            sched = a.get("schedule")
            when = (f" · every {sched['every_minutes']}m" if sched and "every_minutes" in sched
                    else f" · daily {sched['daily_at']}" if sched and "daily_at" in sched else "")
            state = "" if a.get("enabled", True) else " · disabled"
            lines.append(f"• {a.get('display_name', a['name'])} [{a.get('run_mode')}]"
                         f"{when}{state}\n    {a.get('description','') or '(no description)'}")
        QMessageBox.information(self, "Saved agents", "\n\n".join(lines))

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

    def _show_usage_dashboard(self):
        show_usage_dashboard(self)

    def get_settings(self) -> dict:
        self.settings["gemini_api_key"] = self.gemini_key_input.text().strip()
        self.settings["gemini_api_key_secondary"] = self.gemini_key_secondary_input.text().strip()
        self.settings["gemini_api_key_3"] = self.gemini_key_3_input.text().strip()
        self.settings["gemini_api_key_4"] = self.gemini_key_4_input.text().strip()
        self.settings["dual_api_failover"] = self.dual_api_check.isChecked()
        self.settings["ai_chat_titles"] = self.ai_titles_check.isChecked()
        if hasattr(self, "title_model_combo"):
            self.settings["chat_title_model"] = (
                self.title_model_combo.currentData() or model_catalog.DEFAULT_TITLE_MODEL)
        self.settings["anthropic_api_key"] = self.anthropic_key_input.text().strip()
        if hasattr(self, "openai_key_input"):
            self.settings["openai_api_key"] = self.openai_key_input.text().strip()
        if hasattr(self, "openai_provider_combo"):
            _label, _base = self.openai_provider_combo.currentData() or ("OpenAI", "")
            self.settings["openai_base_url"] = _base
            self.settings["openai_provider_label"] = _label
        if hasattr(self, "gmail_addr_input"):
            addr = self.gmail_addr_input.text().strip()
            pw = self.gmail_pw_input.text().strip()
            self.settings["gmail_address"] = addr
            self.settings["gmail_app_password"] = pw
            # Mirror into the SMTP/IMAP keys so the SAME App Password powers send_email + gmail_*.
            self.settings["email_smtp_user"] = addr
            self.settings["email_smtp_password"] = pw
            if addr.lower().endswith("@gmail.com") or addr.lower().endswith("@googlemail.com"):
                self.settings.setdefault("email_smtp_host", "smtp.gmail.com")
                if not self.settings.get("email_smtp_host"):
                    self.settings["email_smtp_host"] = "smtp.gmail.com"
                self.settings["gmail_imap_host"] = "imap.gmail.com"
        sel_id = self.model_combo.currentData()
        self.settings["model_id"] = sel_id or "gemini-3.1-flash-lite"
        provider = model_catalog.provider_for(sel_id)
        self.settings["provider"] = provider
        if provider == "gemini":
            self.settings["gemini_model"] = sel_id
        elif provider == "claude":
            self.settings["anthropic_model"] = sel_id
        elif provider == "openai":
            self.settings["openai_model"] = sel_id
        if hasattr(self, "ollama_model_input"):
            self.settings["ollama_model"] = self.ollama_model_input.text().strip()
        self.settings["auto_screenshot"] = self.auto_shot_check.isChecked()
        self.settings["remote_autostart"] = self.remote_autostart_check.isChecked()
        if hasattr(self, "mcp_bridge_check"):
            self.settings["mcp_bridge_enabled"] = self.mcp_bridge_check.isChecked()
        if hasattr(self, "mcp_highrisk_check"):
            self.settings["mcp_bridge_allow_high_risk"] = self.mcp_highrisk_check.isChecked()
        if hasattr(self, "keep_bg_check"):
            self.settings["keep_running_in_background"] = self.keep_bg_check.isChecked()
        if hasattr(self, "launch_login_check"):
            self.settings["launch_at_login"] = self.launch_login_check.isChecked()
        self.settings["auto_update"] = self.auto_update_check.isChecked()
        self.settings["lean_tools"] = self.lean_tools_check.isChecked()
        if hasattr(self, "offline_mode_check"):
            self.settings["offline_mode"] = self.offline_mode_check.isChecked()
            self._apply_offline_mode()
        # download_protection is owned by the Security tab toggle (persisted live there).
        if hasattr(self, "vault_check"):
            self.settings["use_key_vault"] = self.vault_check.isChecked()
        if hasattr(self, "wake_word_check"):
            self.settings["wake_word"] = self.wake_word_check.isChecked()
        if hasattr(self, "wake_visual_combo"):
            self.settings["wake_visual"] = self.wake_visual_combo.currentData() or "glow"
        if hasattr(self, "glow_anim_check"):
            self.settings["glow_animation"] = self.glow_anim_check.isChecked()
        self.settings["voice_output"] = self.voice_check.isChecked()
        if hasattr(self, "tts_engine_combo"):
            self.settings["tts_engine"] = self.tts_engine_combo.currentData() or "system"
            self.settings["edge_tts_voice"] = self._voice_val(self.edge_voice_input, "en-US-AriaNeural")
            self.settings["gemini_tts_voice"] = self._voice_val(self.gemini_voice_input, "Kore")
            self.settings["soundtools_url"] = self.soundtools_url_input.text().strip()
        if hasattr(self, "live_voice_check"):
            self.settings["live_voice_enabled"] = self.live_voice_check.isChecked()
            self.settings["live_voice_voice"] = self._voice_val(self.live_voice_voice_input, "Zephyr")
        self.settings["voice_chat_spoken_replies"] = self.voice_chat_reply_check.isChecked()
        self.settings["voice_chat_auto_send"] = self.voice_auto_send_check.isChecked()
        self.settings["voice_chat_continue_after_silence"] = self.voice_continue_check.isChecked()
        if hasattr(self, "voice_phrase_combo"):
            self.settings["voice_chat_phrase_timeout"] = self.voice_phrase_combo.currentData() or "auto"
        if hasattr(self, "ptt_check"):
            self.settings["push_to_talk"] = self.ptt_check.isChecked()
            self.settings["push_to_talk_key"] = (self.ptt_key_input.value().strip().lower() or "f9")
            self.settings["stt_engine"] = self.stt_engine_combo.currentData() or "auto"
            self.settings["whisper_model"] = self.whisper_model_combo.currentData() or "base"
        self.settings["hotkey"] = self.hotkey_input.value().strip() or "ctrl+shift+space"
        # If the quit-proof hotkey helper is installed, refresh it with the (possibly new) combo.
        try:
            import hotkey_daemon
            if hotkey_daemon.is_installed():
                hotkey_daemon.install(self.settings["hotkey"])
        except Exception:
            pass
        try:
            self.settings["request_timeout_seconds"] = max(10, min(60, int(self.timeout_input.text().strip() or 15)))
        except ValueError:
            self.settings["request_timeout_seconds"] = 15
        self.settings["automation_enabled"] = self.auto_master_check.isChecked()
        self.settings["auto_confirm_popups"] = self.auto_confirm_check.isChecked()
        # Appearance tab
        if hasattr(self, "animations_check"):
            self.settings["animations_enabled"] = self.animations_check.isChecked()
            self.settings["motion_level"] = self.motion_combo.currentData() or "dynamic"
            self.settings["autocorrect_chat"] = self.autocorrect_check.isChecked()
            self.settings["show_thinking"] = self.show_thinking_check.isChecked()
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


class ChatInput(QTextEdit):
    """Chat input that accepts pasted (and dropped) FILES and IMAGES, not just text.

    Cmd/Ctrl+V of file(s) copied in Finder/Explorer attaches their paths; a copied image
    (e.g. a screenshot) is saved to a temp PNG and attached; plain text pastes as plain
    text. `on_attach(list_of_paths)` is the window callback that adds them to the message."""

    def __init__(self, on_attach, parent=None):
        super().__init__(parent)
        self._on_attach = on_attach

    def canInsertFromMimeData(self, source) -> bool:
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        paths: list[str] = []
        try:
            if source.hasUrls():
                paths = [u.toLocalFile() for u in source.urls() if u.toLocalFile()]
            if not paths and source.hasImage():
                p = _save_clipboard_image(source.imageData())
                if p:
                    paths = [p]
        except Exception:
            paths = []
        if paths:
            try:
                self._on_attach(paths)
                return
            except Exception:
                pass
        # Fall back to PLAIN text (avoids pasting rich HTML/fonts into the prompt box).
        if source.hasText():
            self.insertPlainText(source.text())
        else:
            super().insertFromMimeData(source)


def _save_clipboard_image(image) -> str | None:
    """Save a raw clipboard image (a copied screenshot/photo with no file path) to a temp
    PNG so Ember can read it like any other attached file. Returns the path or None."""
    try:
        from PyQt6.QtGui import QImage
        qimg = image if isinstance(image, QImage) else QImage(image)
        if qimg is None or qimg.isNull():
            return None
        d = Path(tempfile.gettempdir()) / "ember_pasted"
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"pasted_{int(time.time() * 1000)}.png"
        return str(dst) if qimg.save(str(dst), "PNG") else None
    except Exception:
        return None


class FeaturesDialog(QDialog):
    """A browsable, SEARCHABLE directory of everything Ember can do — so features are
    actually discoverable instead of hidden behind chat commands. Each row has a button
    that opens the feature, drops an example into the chat, or jumps to Settings.
    `on_action((kind, value))` is the window callback that performs the chosen action."""

    def __init__(self, on_action, parent=None):
        super().__init__(parent)
        self._on_action = on_action
        self.setWindowTitle("Ember Command Palette")
        self.setMinimumSize(660, 640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(9)
        outer.addWidget(_dialog_header(
            "Command Palette", "Search every action, app, automation, and setting in Ember.",
            eyebrow="DISCOVER & LAUNCH", mark="⌕"))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search actions… try “voice”, “organize”, or “security”")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first_visible)
        outer.addWidget(self.search)
        self._result_label = QLabel("")
        self._result_label.setObjectName("dialogStatus")
        outer.addWidget(self._result_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self._vbox = QVBoxLayout(inner)
        self._vbox.setSpacing(4)
        self._sections = []        # (section_label_widget, [row_widgets])
        self._rows = []            # (row_widget, haystack, section_label_widget, action)
        self._build_rows()
        self._vbox.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        footer = QHBoxLayout()
        foot_hint = QLabel("Enter opens the first result  ·  Esc closes")
        foot_hint.setObjectName("dialogStatus")
        footer.addWidget(foot_hint)
        footer.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        outer.addLayout(footer)
        _polish_dialog(self)
        self._filter("")
        QTimer.singleShot(0, self.search.setFocus)

    def _build_rows(self):
        for category, feats in FEATURE_CATALOG:
            sec = QLabel(category)
            sec.setObjectName("sectionTitle")
            sec.setStyleSheet("color:#cdd3ea; font-weight:600; margin-top:10px;")
            self._vbox.addWidget(sec)
            section_rows = []
            for emoji, name, desc, action in feats:
                row = QFrame()
                row.setObjectName("featureRow")
                h = QHBoxLayout(row)
                h.setContentsMargins(9, 8, 9, 8)
                h.setSpacing(10)
                glyph = QLabel(emoji)
                glyph.setObjectName("featureIcon")
                glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
                glyph.setFixedSize(34, 34)
                h.addWidget(glyph)
                copy = QVBoxLayout()
                copy.setSpacing(1)
                feat_name = QLabel(name)
                feat_name.setObjectName("featureName")
                feat_desc = QLabel(desc)
                feat_desc.setObjectName("featureDescription")
                feat_desc.setWordWrap(True)
                copy.addWidget(feat_name)
                copy.addWidget(feat_desc)
                h.addLayout(copy, 1)
                kind = action[0]
                btn_label = {"open": "Open", "type": "Try", "settings": "Settings"}.get(kind, "")
                if btn_label:
                    b = QPushButton(btn_label)
                    b.setObjectName("secondaryBtn")
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.setFixedWidth(96)
                    b.clicked.connect(lambda _=False, a=action: self._do(a))
                    h.addWidget(b)
                self._vbox.addWidget(row)
                section_rows.append(row)
                self._rows.append((row, f"{name} {desc} {category}".lower(), sec, action))
            self._sections.append((sec, section_rows))

    def _do(self, action):
        self.accept()
        try:
            self._on_action(action)
        except Exception as e:
            # Surface it - a bare `except: pass` here means a broken feature just closes this
            # dialog and does nothing else, which looks exactly like "the button does nothing"
            # (the underlying failure, if any, never reaches the user).
            traceback.print_exc()
            QMessageBox.warning(self.parent(), "Couldn't open that", f"{type(e).__name__}: {e}")

    def _filter(self, text):
        q = (text or "").strip().lower()
        visible = 0
        for row, hay, _sec, _action in self._rows:
            matches = (q in hay) if q else True
            row.setVisible(matches)
            visible += int(matches)
        # Hide a section header when every row under it is filtered out.
        for sec, rows in self._sections:
            sec.setVisible(True if not q else any(r.isVisible() for r in rows))
        self._result_label.setText(
            f"{visible} result{'s' if visible != 1 else ''}" + (f" for “{text.strip()}”" if q else ""))

    def _open_first_visible(self):
        for row, _hay, _sec, action in self._rows:
            if row.isVisible():
                self._do(action)
                return


class AntivirusDialog(QDialog):
    """Evidence-first endpoint security console.

    The interface deliberately distinguishes confirmed malware from heuristic findings, keeps
    destructive actions behind quarantine, exposes actual engine coverage, and never uses a
    marketing score as a substitute for scan evidence.
    """
    _scan_done = pyqtSignal(dict)
    _progress_sig = pyqtSignal(int, int, str)
    _reviewed_sig = pyqtSignal(object, object)   # (kept, cleared) after AI false-positive review

    def __init__(self, parent=None):
        super().__init__(parent)
        import antivirus
        self._av = antivirus
        self._settings = getattr(parent, "settings", {}) or {}
        self._last_flagged = []   # evidence records from the latest scan
        self.setWindowTitle("Ember Endpoint Security")
        self.setMinimumSize(900, 720)
        self._scan_cancel = threading.Event()
        self._scan_done.connect(self._on_scan_done)
        self._progress_sig.connect(self._on_progress)
        self._reviewed_sig.connect(self._on_reviewed)
        # AI may add a triage opinion, but it never clears or downgrades engine evidence.
        try:
            self._av.set_ai_judge(self._ai_judge)
        except Exception:
            pass

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(8)
        v.addWidget(_dialog_header(
            "Endpoint Security",
            "Evidence-led malware defense, behavioral monitoring, containment, and audit history.",
            eyebrow="PROTECTION CONSOLE", mark="◆"))

        self._health = QLabel("")
        self._health.setObjectName("securityHealth")
        self._health.setWordWrap(True)
        v.addWidget(self._health)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        v.addWidget(self._tabs, 1)

        overview = QWidget()
        overview_v = QVBoxLayout(overview)
        overview_v.setContentsMargins(12, 12, 12, 12)
        overview_v.setSpacing(9)
        self._tabs.addTab(overview, "Overview")

        findings = QWidget()
        findings_v = QVBoxLayout(findings)
        findings_v.setContentsMargins(12, 12, 12, 12)
        findings_v.setSpacing(8)
        self._tabs.addTab(findings, "Findings")

        quarantine = QWidget()
        quarantine_v = QVBoxLayout(quarantine)
        quarantine_v.setContentsMargins(12, 12, 12, 12)
        quarantine_v.setSpacing(8)
        self._tabs.addTab(quarantine, "Quarantine")

        activity = QWidget()
        activity_v = QVBoxLayout(activity)
        activity_v.setContentsMargins(12, 12, 12, 12)
        activity_v.setSpacing(8)
        self._tabs.addTab(activity, "Activity")

        self._status = QLabel("")
        self._status.setObjectName("dialogStatus")
        self._status.setWordWrap(True)
        overview_v.addWidget(self._status)

        scan_row = QHBoxLayout()
        for label, fn, primary in (("Scan a folder…", self._scan_folder, True),
                                   ("Quick scan", self._quick_scan, False),
                                   ("Processes", self._scan_processes, False),
                                   ("Network", lambda: self._investigate_surface("network"), False),
                                   ("Persistence", lambda: self._investigate_surface("persistence"), False),
                                   ("Sandbox…", self._sandbox, False)):
            b = QPushButton(label)
            if primary:
                b.setObjectName("send")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            scan_row.addWidget(b)
        overview_v.addLayout(scan_row)

        self._progress = QLabel("")
        self._progress.setStyleSheet("color:#e0af68; font-size:12px;")
        overview_v.addWidget(self._progress)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setVisible(False)
        overview_v.addWidget(self._bar)
        self._cancel_btn = QPushButton("Stop scan")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_scan)
        overview_v.addWidget(self._cancel_btn, 0, Qt.AlignmentFlag.AlignLeft)

        th = QLabel("Findings")
        th.setObjectName("sectionTitle")
        findings_v.addWidget(th)
        finding_note = QLabel(
            "Confirmed malware and heuristic findings are kept separate. “No known threats” "
            "does not mean a file is provably safe.")
        finding_note.setObjectName("dialogStatus")
        finding_note.setWordWrap(True)
        findings_v.addWidget(finding_note)
        self._threats_list = QListWidget()
        self._threats_list.setObjectName("dialogList")
        self._threats_list.itemSelectionChanged.connect(self._show_finding_details)
        findings_v.addWidget(self._threats_list, 1)
        self._finding_details = QLabel("Select a finding to inspect its evidence and coverage.")
        self._finding_details.setObjectName("dialogStatus")
        self._finding_details.setWordWrap(True)
        self._finding_details.setMinimumHeight(70)
        findings_v.addWidget(self._finding_details)
        trow = QHBoxLayout()
        qb = QPushButton("Quarantine selected")
        qb.clicked.connect(self._quarantine_threat)
        ib = QPushButton("Dismiss from view")
        ib.clicked.connect(self._ignore_threat)
        qa = QPushButton("Contain confirmed malware")
        qa.setObjectName("primaryBtn")
        qa.clicked.connect(self._quarantine_all)
        for b in (qb, ib):
            trow.addWidget(b)
        trow.addStretch()
        trow.addWidget(qa)
        findings_v.addLayout(trow)

        rt = QLabel("Real-time protection")
        rt.setObjectName("sectionTitle")
        rt.setStyleSheet("color:#cdd3ea; font-weight:600; margin-top:8px;")
        overview_v.addWidget(rt)
        self._chk_dl = QCheckBox("Download protection — auto-scan new downloads")
        self._chk_fl = QCheckBox("Fileless / behavioral protection (process monitor)")
        self._chk_sc = QCheckBox("Always-on Security Center (files · network · persistence)")
        self._chk_dl.setChecked(bool(self._settings.get("download_protection", True)))
        self._chk_fl.setChecked(bool(self._settings.get("fileless_protection", True)))
        self._chk_sc.setChecked(bool(self._settings.get("realtime_security_center", True)))
        self._chk_dl.stateChanged.connect(lambda s: self._toggle_guard("download", bool(s)))
        self._chk_fl.stateChanged.connect(lambda s: self._toggle_guard("fileless", bool(s)))
        self._chk_sc.stateChanged.connect(lambda s: self._toggle_guard("center", bool(s)))
        for c in (self._chk_dl, self._chk_fl, self._chk_sc):
            overview_v.addWidget(c)

        privacy = QLabel("Cloud reputation & data handling")
        privacy.setObjectName("sectionTitle")
        overview_v.addWidget(privacy)
        cfg = self._av.get_config()
        self._chk_vt_hash = QCheckBox("Check file hashes with VirusTotal reputation")
        self._chk_vt_hash.setChecked(bool(cfg.get("vt_hash_lookup", True)))
        self._chk_vt_upload = QCheckBox(
            "Share unknown file samples with VirusTotal (file contents leave this device)")
        self._chk_vt_upload.setChecked(bool(cfg.get("vt_upload_unknown", False)))
        self._chk_vt_upload.setToolTip(
            "Off by default. Enable only when your organisation permits third-party sample sharing.")
        self._chk_vt_hash.stateChanged.connect(
            lambda state: self._toggle_cloud("vt_hash_lookup", bool(state)))
        self._chk_vt_upload.stateChanged.connect(
            lambda state: self._toggle_cloud("vt_upload_unknown", bool(state)))
        overview_v.addWidget(self._chk_vt_hash)
        overview_v.addWidget(self._chk_vt_upload)
        privacy_note = QLabel(
            "Local scanning always runs. Hash lookups share only SHA-256. Sample sharing is "
            "separate, explicit, and disabled by default.")
        privacy_note.setObjectName("dialogStatus")
        privacy_note.setWordWrap(True)
        overview_v.addWidget(privacy_note)
        overview_v.addStretch()

        q = QLabel("Quarantine")
        q.setObjectName("sectionTitle")
        q.setStyleSheet("color:#cdd3ea; font-weight:600; margin-top:8px;")
        quarantine_v.addWidget(q)
        qnote = QLabel(
            "Contained files are neutralised and retained for investigation. Permanent deletion "
            "is available only here, after containment.")
        qnote.setObjectName("dialogStatus")
        qnote.setWordWrap(True)
        quarantine_v.addWidget(qnote)
        self._qlist = QListWidget()
        self._qlist.setObjectName("dialogList")
        quarantine_v.addWidget(self._qlist, 1)
        qrow = QHBoxLayout()
        rb = QPushButton("Restore selected")
        rb.clicked.connect(self._restore)
        vb = QPushButton("Verify integrity")
        vb.clicked.connect(self._verify_quarantine)
        db = QPushButton("Delete selected")
        db.setObjectName("dangerBtn")
        db.clicked.connect(self._delete)
        rf = QPushButton("Refresh")
        rf.clicked.connect(self._refresh)
        qrow.addWidget(rb)
        qrow.addWidget(vb)
        qrow.addWidget(db)
        qrow.addStretch()
        qrow.addWidget(rf)
        quarantine_v.addLayout(qrow)

        audit_head = QLabel("Security activity")
        audit_head.setObjectName("sectionTitle")
        activity_v.addWidget(audit_head)
        self._audit_status = QLabel("")
        self._audit_status.setObjectName("dialogStatus")
        self._audit_status.setWordWrap(True)
        activity_v.addWidget(self._audit_status)
        self._audit_list = QListWidget()
        self._audit_list.setObjectName("dialogList")
        activity_v.addWidget(self._audit_list, 1)
        audit_actions = QHBoxLayout()
        refresh_audit = QPushButton("Refresh activity")
        refresh_audit.clicked.connect(self._refresh)
        export_report = QPushButton("Export security report…")
        export_report.clicked.connect(self._export_report)
        audit_actions.addWidget(refresh_audit)
        audit_actions.addStretch()
        audit_actions.addWidget(export_report)
        activity_v.addLayout(audit_actions)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)
        _polish_dialog(self)
        self._refresh()

    # ---- status / quarantine ----
    def _refresh(self):
        try:
            st = self._av.security_status()
            state = st.get("protection_state", "attention")
            state_title = {
                "protected": "Protection active",
                "limited": "Local protection active — coverage is limited",
                "attention": "Protection needs attention",
                "off": "Protection is off",
            }.get(state, "Protection status unavailable")
            providers = st.get("providers") or {}
            active = [label for key, label in (
                ("local_static", "local static analysis"),
                ("platform_antivirus", "platform antivirus"),
                ("cloud_reputation", "cloud reputation"),
                ("behavior_monitor", "behavior monitoring"),
                ("security_center", "continuous monitoring"),
            ) if providers.get(key)]
            self._health.setText(
                f"{state_title}   ·   {len(active)} active protection layer(s)   ·   "
                f"{st.get('quarantine_count', 0)} contained item(s)")
            self._health.setProperty("state", state)
            self._health.style().unpolish(self._health)
            self._health.style().polish(self._health)
            gaps = st.get("coverage_gaps") or []
            self._status.setText(
                "Active layers: " + (", ".join(active) or "none") +
                f"\nIsolation: {st.get('sandbox_available', 'none')}" +
                ("\nCoverage notes: " + " ".join(gaps) if gaps else ""))
            if not st.get("vt_api_key_configured"):
                self._chk_vt_hash.setText(
                    "Check file hashes with VirusTotal reputation (API key required in Settings)")
                self._chk_vt_upload.setText(
                    "Share unknown file samples with VirusTotal (API key required; contents leave device)")
            else:
                self._chk_vt_hash.setText("Check file hashes with VirusTotal reputation")
                self._chk_vt_upload.setText(
                    "Share unknown file samples with VirusTotal (file contents leave this device)")
        except Exception as e:
            self._health.setText("Protection status unavailable")
            self._status.setText(f"status unavailable: {e}")
        self._qlist.clear()
        try:
            for it in self._av.list_quarantine().get("items", []):
                name = Path(it.get("original_path", "?")).name
                reasons = ", ".join(it.get("reasons", []) or [])[:90]
                item = QListWidgetItem(f"{name}   —   {reasons or 'flagged'}   ({it.get('quarantined_at','')})")
                item.setData(Qt.ItemDataRole.UserRole, it.get("id"))
                self._qlist.addItem(item)
        except Exception:
            pass
        self._audit_list.clear()
        try:
            audit = self._av.security_audit(250)
            self._audit_status.setText(
                ("Audit chain verified" if audit.get("integrity_ok") else
                 "Audit integrity warning — records may have been altered or damaged") +
                f"   ·   {audit.get('total', 0)} event(s)")
            for event in reversed(audit.get("events", [])):
                details = event.get("details") or {}
                summary = (details.get("original_path") or details.get("root") or
                           details.get("restored_to") or "")
                item = QListWidgetItem(
                    f"{event.get('time', '?')}   {str(event.get('action', '')).replace('_', ' ')}"
                    + (f"   ·   {summary}" if summary else ""))
                item.setToolTip(json.dumps(details, indent=2, ensure_ascii=False))
                self._audit_list.addItem(item)
        except Exception as exc:
            self._audit_status.setText(f"Activity unavailable: {exc}")

    def _toggle_cloud(self, key: str, enabled: bool):
        try:
            if key == "vt_upload_unknown" and enabled:
                if QMessageBox.question(
                        self, "Enable file sample sharing?",
                        "Unknown files may be uploaded to VirusTotal and shared with security "
                        "vendors. Do not enable this for confidential or regulated data unless "
                        "your organisation permits it.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        ) != QMessageBox.StandardButton.Yes:
                    self._chk_vt_upload.blockSignals(True)
                    self._chk_vt_upload.setChecked(False)
                    self._chk_vt_upload.blockSignals(False)
                    return
            self._av.set_config(**{key: enabled})
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Cloud reputation", str(exc))

    def _cancel_scan(self):
        self._scan_cancel.set()
        self._cancel_btn.setEnabled(False)
        self._progress.setText("Stopping after the current file…")

    def _export_report(self):
        default = str(Path.home() / f"Ember-Security-Report-{time.strftime('%Y%m%d-%H%M')}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export security report", default, "JSON report (*.json)")
        if not path:
            return
        try:
            report = self._av.security_report()
            Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
            self._av.audit_event("security_report_exported", {"destination": path})
            QMessageBox.information(self, "Security report", "Report exported successfully.")
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Security report", f"Could not export report: {exc}")

    def _show_finding_details(self):
        path = self._selected_threat_path()
        finding = next((f for f in self._last_flagged if f.get("path") == path), None)
        if not finding:
            self._finding_details.setText("Select a finding to inspect its evidence and coverage.")
            return
        reasons = "\n".join(f"• {r}" for r in (finding.get("reasons") or []))
        engines = ", ".join(finding.get("engines") or []) or "local analysis"
        advisory = finding.get("ai_assessment")
        self._finding_details.setText(
            f"{finding.get('classification', finding.get('verdict', 'finding'))}   ·   "
            f"confidence {finding.get('confidence', 'unknown')}   ·   "
            f"ID {finding.get('detection_id', 'pending')}\n"
            f"Engines: {engines}\n{reasons}"
            + (f"\nAI triage (advisory only): {advisory}" if advisory else ""))

    def _investigate_surface(self, surface: str):
        self._progress.setText(f"Inspecting {surface} activity…")
        self._bar.setRange(0, 0)
        self._bar.setVisible(True)
        def work():
            try:
                import security_center
                result = (security_center.scan_network() if surface == "network"
                          else security_center.scan_persistence())
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self._scan_done.emit({"_surface": surface, "result": result})
        threading.Thread(target=work, daemon=True).start()

    def _selected_quarantine_id(self):
        it = self._qlist.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _restore(self):
        qid = self._selected_quarantine_id()
        if not qid:
            return
        if QMessageBox.question(self, "Restore file",
                "Restore this file to its original location? It was flagged as dangerous."
                ) != QMessageBox.StandardButton.Yes:
            return
        r = self._av.restore_quarantined(qid)
        QMessageBox.information(self, "Restore", "Restored." if r.get("ok") else f"Failed: {r.get('error')}")
        self._refresh()

    def _verify_quarantine(self):
        qid = self._selected_quarantine_id()
        if not qid:
            return
        result = self._av.verify_quarantined(qid)
        if result.get("ok") and result.get("integrity_ok"):
            QMessageBox.information(self, "Evidence integrity", result.get("message"))
        else:
            QMessageBox.warning(
                self, "Evidence integrity warning",
                result.get("error") or result.get("message") or "Integrity verification failed.")
        self._refresh()

    def _delete(self):
        qid = self._selected_quarantine_id()
        if not qid:
            return
        if QMessageBox.question(
                self, "Delete quarantined file",
                "Permanently delete the selected quarantined file? This cannot be undone."
                ) != QMessageBox.StandardButton.Yes:
            return
        r = self._av.delete_quarantined(qid)
        QMessageBox.information(self, "Delete", "Deleted." if r.get("ok") else f"Failed: {r.get('error')}")
        self._refresh()

    # ---- scans (off the UI thread) ----
    def _scan_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder to scan", str(Path.home()))
        if d:
            self._run_scan([d])

    def _quick_scan(self):
        roots = [str(Path.home() / "Downloads"), str(Path.home() / "Desktop")]
        roots = [r for r in roots if Path(r).exists()]
        if roots:
            self._run_scan(roots)

    def _run_scan(self, paths):
        self._scan_cancel.clear()
        self._cancel_btn.setEnabled(True)
        self._cancel_btn.setVisible(True)
        self._progress.setText("Starting scan…")
        self._bar.setRange(0, 0)          # indeterminate until first progress tick
        self._bar.setVisible(True)
        self._threats_list.clear()
        self._last_flagged = []

        def _prog(scanned, flagged, cur):
            self._progress_sig.emit(scanned, flagged, cur)

        cloud = bool(self._chk_vt_hash.isChecked() or self._chk_vt_upload.isChecked())
        def work():
            agg = {"scanned": 0, "flagged": [], "errors": [], "cancelled": False}
            for p in paths:
                if self._scan_cancel.is_set():
                    agg["cancelled"] = True
                    break
                try:
                    r = self._av.scan_directory(
                        p, deep=cloud, max_files=100000, progress=_prog,
                        cancel=self._scan_cancel.is_set)
                    if r.get("ok"):
                        agg["scanned"] += r.get("scanned", 0)
                        agg["flagged"] += r.get("flagged", []) or []
                        agg["cancelled"] = agg["cancelled"] or bool(r.get("cancelled"))
                    else:
                        agg["errors"].append(r.get("error", "scan failed"))
                except Exception as e:
                    agg["errors"].append(str(e))
            self._scan_done.emit(agg)
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, scanned, flagged, cur):
        # Live progress data instead of a beachball.
        self._bar.setRange(0, 0)
        name = Path(cur).name if cur else ""
        self._progress.setText(f"Scanned {scanned} files · {flagged} finding(s)   {name[:48]}")

    def _on_scan_done(self, agg):
        self._progress.setText("")
        self._bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setEnabled(True)
        if "_surface" in agg:
            surface = agg.get("_surface", "security")
            result = agg.get("result") or {}
            if not result.get("ok"):
                QMessageBox.warning(self, f"{surface.title()} inspection",
                                    result.get("error", "Inspection failed."))
                return
            flagged = result.get("flagged") or result.get("findings") or []
            count = len(flagged) if isinstance(flagged, list) else int(flagged or 0)
            self._av.audit_event(f"{surface}_inspection_completed", {
                "findings": count,
                "observed": result.get("connections_seen") or result.get("entries_seen") or 0,
            })
            QMessageBox.information(
                self, f"{surface.title()} inspection",
                f"Inspection complete. {count} finding(s) require review.\n\n"
                "Open Activity for the recorded operation; detailed Security Center events "
                "remain available to Ember's security tools.")
            self._refresh()
            return
        # Process-scan result.
        if "_processes" in agg:
            r = agg["_processes"]
            if not r.get("ok"):
                QMessageBox.warning(self, "Process scan", r.get("error", "could not scan"))
                return
            flagged = r.get("flagged", [])
            if flagged:
                lines = "\n".join(f"• {f.get('name')} (pid {f.get('pid')}): {f.get('verdict')}"
                                  for f in flagged[:15])
                QMessageBox.warning(self, "Process scan",
                                    f"⚠ {len(flagged)} suspicious process(es):\n\n{lines}")
            else:
                QMessageBox.information(self, "Process scan",
                                       f"✓ Scanned {r.get('scanned', 0)} processes — nothing malicious.")
            return
        # Sandbox result.
        if "_sandbox" in agg:
            r = agg["_sandbox"]
            if r.get("refused"):
                QMessageBox.warning(self, "Sandbox", r.get("message", "Refused: file is malicious."))
            elif not r.get("ok"):
                QMessageBox.warning(self, "Sandbox", r.get("error", "Could not run in the sandbox."))
            else:
                QMessageBox.information(self, "Sandbox",
                    f"Ran via {r.get('method', 'sandbox')} · verdict: {r.get('verdict_hint', '?')} · "
                    f"exit {r.get('exit_code')}")
            return
        # Folder scan finished — populate the evidence-led Findings panel.
        flagged = agg.get("flagged", [])
        self._last_flagged = list(flagged)
        self._threats_list.clear()
        mal = [f for f in flagged if f.get("verdict") == "malicious"]
        if flagged:
            self._progress.setText(
                f"{len(flagged)} finding(s) in {agg.get('scanned', 0)} files: "
                f"{len(mal)} confirmed malware, {len(flagged) - len(mal)} require review.")
            for f in flagged:
                reasons = "; ".join(f.get("reasons", []) or [])[:90]
                label = "CONFIRMED" if f.get("verdict") == "malicious" else "REVIEW"
                item = QListWidgetItem(
                    f"{label}   {Path(f['path']).name}   ·   {reasons}")
                item.setData(Qt.ItemDataRole.UserRole, f["path"])
                item.setToolTip(f["path"])
                self._threats_list.addItem(item)
            self._tabs.setCurrentIndex(1)
        else:
            self._progress.setText(
                f"Scanned {agg.get('scanned', 0)} files — no known indicators found.")
        if agg.get("cancelled"):
            self._progress.setText(self._progress.text() + " Scan stopped by user.")
        if agg.get("errors"):
            self._progress.setText(self._progress.text() + "  (some errors occurred)")
        self._refresh()
        # AI adds advisory analyst context; it never clears engine findings.
        if any(f.get("verdict") == "suspicious" for f in flagged) and self._ai_available():
            self._progress.setText(self._progress.text() + " AI triage is adding context…")
            items = list(flagged)
            def review():
                try:
                    kept, cleared = self._av.ai_review_flagged(items)
                except Exception:
                    kept, cleared = items, []
                self._reviewed_sig.emit(kept, cleared)
            threading.Thread(target=review, daemon=True).start()

    def _ai_available(self) -> bool:
        return bool((self._settings.get("gemini_api_key") or self._settings.get("anthropic_api_key")
                     or self._settings.get("openai_api_key") or "").strip())

    def _ai_judge(self, items):
        """ONE batched model call: which flagged files could cause REAL harm? Returns list[bool].
        Delegates to the shared ai_detect.judge_harmful so the Antivirus window and the always-on
        guards use identical logic + the same model."""
        import ai_detect
        return ai_detect.judge_harmful(items, self._settings)

    def _on_reviewed(self, kept, cleared):
        """Attach AI triage context without clearing, restoring, or downgrading a finding."""
        self._last_flagged = list(kept or [])
        advisory = sum(bool(f.get("ai_assessment")) for f in self._last_flagged)
        base = self._progress.text().split(" AI triage")[0]
        self._progress.setText(
            base + f" AI triage added advisory context to {advisory} finding(s); verdicts unchanged.")
        self._show_finding_details()
        self._refresh()

    def _selected_threat_path(self):
        it = self._threats_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _quarantine_threat(self):
        path = self._selected_threat_path()
        if not path:
            return
        finding = next((f for f in self._last_flagged if f.get("path") == path), {})
        if finding.get("verdict") != "malicious":
            if QMessageBox.question(
                    self, "Contain item under review?",
                    "This is a heuristic finding, not confirmed malware. Move it into quarantine "
                    "for investigation anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    ) != QMessageBox.StandardButton.Yes:
                return
        r = self._av.quarantine_file(
            path, reasons=finding.get("reasons") or ["contained from Endpoint Security"],
            sha256=finding.get("sha256", ""))
        if r.get("ok"):
            self._take_threat_row()
            self._refresh()
        else:
            QMessageBox.warning(self, "Quarantine", r.get("error", "could not quarantine"))

    def _quarantine_all(self):
        confirmed = [f for f in self._last_flagged if f.get("verdict") == "malicious"
                     and Path(f.get("path", "")).exists()]
        if not confirmed:
            QMessageBox.information(
                self, "Contain confirmed malware",
                "There is no uncontained confirmed malware. Review findings remain untouched.")
            return
        if QMessageBox.question(
                self, "Contain confirmed malware",
                f"Move {len(confirmed)} confirmed-malware item(s) into quarantine?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                ) != QMessageBox.StandardButton.Yes:
            return
        n = 0
        contained_paths = set()
        for f in confirmed:
            if self._av.quarantine_file(
                    f["path"], reasons=f.get("reasons"), sha256=f.get("sha256", "")).get("ok"):
                n += 1
                contained_paths.add(f["path"])
        self._last_flagged = [f for f in self._last_flagged if f.get("path") not in contained_paths]
        for row in range(self._threats_list.count() - 1, -1, -1):
            if self._threats_list.item(row).data(Qt.ItemDataRole.UserRole) in contained_paths:
                self._threats_list.takeItem(row)
        self._refresh()
        QMessageBox.information(self, "Containment", f"Contained {n} confirmed item(s).")

    def _delete_threat(self):
        # Kept as a compatibility method for older UI callers. Findings are never directly
        # deleted: contain first, preserve evidence, then delete from the Quarantine tab.
        QMessageBox.information(
            self, "Contain before deletion",
            "Direct deletion is disabled. Quarantine the item first, review its evidence, then "
            "delete it from the Quarantine tab if your response policy permits.")

    def _ignore_threat(self):
        path = self._selected_threat_path()
        if path:
            self._av.audit_event("finding_dismissed_from_view", {"path": path})
        self._take_threat_row()

    def _take_threat_row(self):
        row = self._threats_list.currentRow()
        if row >= 0:
            it = self._threats_list.takeItem(row)
            p = it.data(Qt.ItemDataRole.UserRole) if it else None
            self._last_flagged = [f for f in self._last_flagged if f.get("path") != p]
            self._show_finding_details()

    def _scan_processes(self):
        self._progress.setText("🔄 Scanning running processes…")
        def work():
            try:
                import fileless_guard
                r = fileless_guard.scan_processes()
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            self._scan_done.emit({"_processes": r})
        threading.Thread(target=work, daemon=True).start()

    def _sandbox(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file to run safely in the sandbox")
        if not path:
            return
        self._progress.setText("🔄 Running in sandbox…")
        def work():
            try:
                r = self._av.run_in_sandbox(path)
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            self._scan_done.emit({"_sandbox": r})
        threading.Thread(target=work, daemon=True).start()

    # _on_scan_done also handles the process/sandbox payloads:
    def _toggle_guard(self, which, on):
        try:
            if which == "download":
                import download_guard
                self._settings["download_protection"] = on
                download_guard.start() if on else download_guard.download_guard_stop()
            elif which == "fileless":
                import fileless_guard
                self._av.set_config(fileless_protection=on)
                self._settings["fileless_protection"] = on
                fileless_guard.start() if on else fileless_guard.fileless_guard_stop()
            elif which == "center":
                import security_center
                self._av.set_config(realtime_security_center=on)
                self._settings["realtime_security_center"] = on
                security_center.start() if on else security_center.security_center_stop()
        except Exception as e:
            QMessageBox.warning(self, "Protection", f"{type(e).__name__}: {e}")


class AdBlockerDialog(QDialog):
    """A standalone graphical Ad Blocker app (like the Antivirus window): toggle system-wide
    ad/tracker blocking, manage your custom blocked + allow-listed domains, and pull a much
    stronger public list — instead of a one-shot message box. The slow ops (hosts write +
    admin prompt + network fetch) run off the UI thread and report back via _done_sig."""
    _done_sig = pyqtSignal(str, dict)   # (op, result)

    def __init__(self, parent=None):
        super().__init__(parent)
        import network_adblock as ab
        self._ab = ab
        self._busy = False
        self.setWindowTitle("Ember Ad Blocker")
        self.setMinimumSize(620, 640)
        self._done_sig.connect(self._on_done)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(8)
        v.addWidget(_dialog_header(
            "Network Blocking", "Manage system-wide ad and tracker blocking for every app.",
            eyebrow="PRIVACY", mark="⊘"))
        # Honest caveat: this is a DNS-level (hosts file) blocker, which browsers using "Secure
        # DNS" / DNS-over-HTTPS (now the default in Chrome/Firefox for a lot of users) bypass
        # entirely - the browser never asks the OS to resolve the domain, so a hosts-file
        # sinkhole has nothing to intercept. Ember Browser's ad blocking (Chromium's own request
        # interceptor) isn't affected by that and stays fully in effect either way.
        doh_note = QLabel(
            "Note: some browsers' \"Secure DNS\" setting bypasses this (it never asks the OS to "
            "resolve a blocked domain). Ember Browser's own ad blocking always works regardless.")
        doh_note.setStyleSheet("color:#e0af68; font-size:11px;")
        doh_note.setWordWrap(True)
        v.addWidget(doh_note)

        self._status = QLabel("")
        self._status.setStyleSheet("font-size:14px; font-weight:700; margin-top:4px;")
        self._status.setWordWrap(True)
        v.addWidget(self._status)

        row = QHBoxLayout()
        self._toggle_btn = QPushButton("…")
        self._toggle_btn.setObjectName("send")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._toggle)
        self._stronger_btn = QPushButton("⬇  Use stronger list")
        self._stronger_btn.setToolTip("Merge a large public blocklist (StevenBlack) for far more coverage")
        self._stronger_btn.clicked.connect(self._stronger)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh)
        row.addWidget(self._toggle_btn)
        row.addWidget(self._stronger_btn)
        row.addStretch()
        row.addWidget(refresh)
        v.addLayout(row)

        self._progress = QLabel("")
        self._progress.setStyleSheet("color:#e0af68; font-size:12px;")
        self._progress.setWordWrap(True)
        v.addWidget(self._progress)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setRange(0, 0)   # indeterminate (these ops have no measurable %)
        self._bar.setVisible(False)
        v.addWidget(self._bar)

        addrow = QHBoxLayout()
        self._add_in = QLineEdit()
        self._add_in.setPlaceholderText("Block another domain, e.g. ads.example.com")
        self._add_in.returnPressed.connect(self._add)
        addb = QPushButton("Block")
        addb.clicked.connect(self._add)
        addrow.addWidget(self._add_in, 1)
        addrow.addWidget(addb)
        v.addLayout(addrow)

        alrow = QHBoxLayout()
        self._allow_in = QLineEdit()
        self._allow_in.setPlaceholderText("Allow (whitelist) a domain the blocker would catch")
        self._allow_in.returnPressed.connect(self._allow)
        alb = QPushButton("Allow")
        alb.clicked.connect(self._allow)
        alrow.addWidget(self._allow_in, 1)
        alrow.addWidget(alb)
        v.addLayout(alrow)

        bl = QLabel("Your blocked domains (custom)")
        bl.setStyleSheet("color:#cdd3ea; font-weight:600; margin-top:8px;")
        v.addWidget(bl)
        self._blocked_list = QListWidget()
        self._blocked_list.setObjectName("dialogList")
        v.addWidget(self._blocked_list, 1)

        al = QLabel("Allowed (whitelisted)")
        al.setStyleSheet("color:#cdd3ea; font-weight:600; margin-top:8px;")
        v.addWidget(al)
        self._allow_list = QListWidget()
        self._allow_list.setObjectName("dialogList")
        self._allow_list.setMaximumHeight(110)
        v.addWidget(self._allow_list)

        remrow = QHBoxLayout()
        rmb = QPushButton("Remove selected")
        rmb.setToolTip("Forget the selected custom/allowed domain (back to default behaviour)")
        rmb.clicked.connect(self._remove_selected)
        remrow.addStretch()
        remrow.addWidget(rmb)
        v.addLayout(remrow)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)
        _polish_dialog(self)
        self._refresh()

    # -- helpers ----------------------------------------------------------
    def _set_busy(self, on: bool, label: str = ""):
        self._busy = on
        for b in (self._toggle_btn, self._stronger_btn):
            b.setEnabled(not on)
        self._bar.setVisible(on)
        self._progress.setText(label)
        if on:
            self._progress.setStyleSheet("color:#e0af68; font-size:12px;")

    def _run_async(self, op: str, fn, busy_label: str):
        if self._busy:
            return
        self._set_busy(True, busy_label)

        def work():
            try:
                r = fn()
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            self._done_sig.emit(op, r or {})
        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, op: str, result: dict):
        self._set_busy(False, "")
        if result.get("ok"):
            if op == "stronger":
                msg = f"Added {result.get('added', 0)} domains ({result.get('blocked_domains', 0):,} total)."
            else:
                msg = result.get("message") or "Done."
            self._progress.setText("✓ " + msg)
            self._progress.setStyleSheet("color:#9ece6a; font-size:12px;")
        else:
            self._progress.setText("⚠ " + (result.get("error") or "failed"))
            self._progress.setStyleSheet("color:#ff6b6b; font-size:12px;")
        self._refresh()

    def _refresh(self):
        try:
            st = self._ab.adblock_status()
            on = bool(st.get("enabled"))
            self._status.setText(
                ("🟢  ON" if on else "⚫  OFF")
                + f"  —  {st.get('blocked_domains', 0):,} domains blocked"
                + f"   ·   {st.get('user_added', 0)} custom · {st.get('allowlisted', 0)} allowed")
            self._status.setStyleSheet(
                ("color:#9ece6a;" if on else "color:#9aa0b5;") + " font-size:14px; font-weight:700;")
            self._toggle_btn.setText("Disable ad blocking" if on else "Enable ad blocking")
            lists = self._ab.adblock_lists()
            self._blocked_list.clear()
            extra = lists.get("extra", []) or []
            if extra:
                self._blocked_list.addItems(extra)
            else:
                it = QListWidgetItem("(no custom domains yet)")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self._blocked_list.addItem(it)
            self._allow_list.clear()
            allow = lists.get("allow", []) or []
            if allow:
                self._allow_list.addItems(allow)
            else:
                it = QListWidgetItem("(nothing whitelisted)")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self._allow_list.addItem(it)
        except Exception as e:
            self._status.setText(f"Ad blocker unavailable: {e}")

    # -- actions ----------------------------------------------------------
    def _toggle(self):
        on = bool(self._ab.adblock_status().get("enabled"))
        if on:
            self._run_async("disable", self._ab.adblock_disable, "Turning OFF — enter your password if asked…")
        else:
            self._run_async("enable", self._ab.adblock_enable, "Turning ON — enter your password if asked…")

    def _stronger(self):
        self._run_async("stronger", self._ab.adblock_update_from_url,
                        "Fetching a large public blocklist (StevenBlack)…")

    def _add(self):
        d = self._add_in.text().strip()
        if not d:
            return
        self._add_in.clear()
        self._run_async("add", lambda: self._ab.adblock_add_domain(d), f"Blocking {d}…")

    def _allow(self):
        d = self._allow_in.text().strip()
        if not d:
            return
        self._allow_in.clear()
        self._run_async("allow", lambda: self._ab.adblock_allow_domain(d), f"Allowing {d}…")

    def _remove_selected(self):
        for lw in (self._blocked_list, self._allow_list):
            it = lw.currentItem()
            if it and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                d = it.text().strip()
                if d and not d.startswith("("):
                    self._run_async("remove", lambda d=d: self._ab.adblock_remove(d), f"Removing {d}…")
                    return


class StorageInspectorDialog(QDialog):
    """Read-first storage browser with safe, reversible cleanup actions."""

    _scan_done = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Storage Inspector")
        self.setMinimumSize(760, 590)
        self._scan_done.connect(self._on_scan_done)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(9)
        v.addWidget(_dialog_header(
            "Storage Inspector", "Find space-hungry files and duplicate copies before removing anything.",
            eyebrow="FILES", mark="▤"))

        path_row = QHBoxLayout()
        self.path_input = QLineEdit(str(Path.home() / "Downloads"))
        self.path_input.setPlaceholderText("Folder to inspect")
        path_row.addWidget(self.path_input, 1)
        browse = QPushButton("Choose folder…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        v.addLayout(path_row)

        options = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("Largest files", "large")
        self.mode.addItem("Duplicate files", "duplicates")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        options.addWidget(self.mode)
        self.threshold = QComboBox()
        for label, value in (("10 MB+", 10), ("100 MB+", 100), ("500 MB+", 500), ("1 GB+", 1024)):
            self.threshold.addItem(label, value)
        self.threshold.setCurrentIndex(1)
        options.addWidget(self.threshold)
        options.addStretch()
        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setObjectName("primaryBtn")
        self.scan_btn.clicked.connect(self._scan)
        options.addWidget(self.scan_btn)
        v.addLayout(options)

        self.status = QLabel("Scanning is read-only. Nothing moves until you choose an item.")
        self.status.setObjectName("dialogStatus")
        v.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.results = QListWidget()
        self.results.setObjectName("dialogList")
        self.results.itemSelectionChanged.connect(self._selection_changed)
        v.addWidget(self.results, 1)

        actions = QHBoxLayout()
        self.copy_btn = QPushButton("Copy path")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_path)
        self.reveal_btn = QPushButton("Show in folder")
        self.reveal_btn.setEnabled(False)
        self.reveal_btn.clicked.connect(self._reveal)
        self.trash_btn = QPushButton("Move to Trash")
        self.trash_btn.setObjectName("dangerBtn")
        self.trash_btn.setEnabled(False)
        self.trash_btn.clicked.connect(self._trash)
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.reveal_btn)
        actions.addStretch()
        actions.addWidget(self.trash_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        v.addLayout(actions)
        _polish_dialog(self)

    def _mode_changed(self):
        if self.mode.currentData() == "duplicates" and self.threshold.currentData() == 100:
            self.threshold.setCurrentIndex(0)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", self.path_input.text())
        if chosen:
            self.path_input.setText(chosen)

    def _scan(self):
        path = self.path_input.text().strip()
        if not path or not Path(path).expanduser().is_dir():
            QMessageBox.warning(self, "Storage Inspector", "Choose a folder that exists.")
            return
        self.results.clear()
        self.scan_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status.setText("Scanning…")
        mode = self.mode.currentData()
        threshold = int(self.threshold.currentData() or 100)

        def work():
            try:
                import file_ops
                if mode == "duplicates":
                    result = file_ops.find_duplicate_files(path, min_size_kb=max(1, threshold * 1024))
                else:
                    result = file_ops.find_large_files(path, min_mb=threshold, max_results=200)
                result["_mode"] = mode
            except Exception as exc:
                result = {"ok": False, "error": str(exc), "_mode": mode}
            self._scan_done.emit(result)

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_done(self, result: dict):
        self.scan_btn.setEnabled(True)
        self.progress.setVisible(False)
        if not result.get("ok"):
            self.status.setText("Scan failed: " + str(result.get("error", "unknown error")))
            return
        if result.get("_mode") == "duplicates":
            groups = result.get("groups") or []
            for n, group in enumerate(groups, 1):
                for file_path in group.get("files") or []:
                    item = QListWidgetItem(
                        f"Duplicate group {n}  ·  {group.get('size_mb', 0):,.2f} MB  ·  {file_path}")
                    item.setData(Qt.ItemDataRole.UserRole, file_path)
                    self.results.addItem(item)
            self.status.setText(
                f"{len(groups)} duplicate group(s) · about {result.get('wasted_mb', 0):,.1f} MB recoverable")
        else:
            files = result.get("files") or []
            for entry in files:
                item = QListWidgetItem(f"{entry.get('size_mb', 0):,.2f} MB  ·  {entry.get('path', '')}")
                item.setData(Qt.ItemDataRole.UserRole, entry.get("path"))
                self.results.addItem(item)
            self.status.setText(f"{len(files)} file(s) found")
        if not self.results.count():
            self.status.setText("No matching files found in this folder.")

    def _selected_path(self) -> str:
        item = self.results.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _selection_changed(self):
        enabled = bool(self._selected_path())
        for button in (self.copy_btn, self.reveal_btn, self.trash_btn):
            button.setEnabled(enabled)

    def _copy_path(self):
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(path)
            self.status.setText("Path copied.")

    def _reveal(self):
        path = self._selected_path()
        if not path:
            return
        try:
            import subprocess
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(Path(path))])
            else:
                subprocess.Popen(["xdg-open", str(Path(path).parent)])
        except Exception as exc:
            QMessageBox.warning(self, "Show in folder", str(exc))

    def _trash(self):
        path = self._selected_path()
        if not path:
            return
        if QMessageBox.question(
                self, "Move file to Trash", f"Move this file to Trash?\n\n{path}"
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from send2trash import send2trash
            send2trash(path)
            row = self.results.currentRow()
            self.results.takeItem(row)
            self.status.setText("Moved to Trash. You can restore it from the system Trash.")
        except Exception as exc:
            QMessageBox.warning(self, "Move to Trash", str(exc))


class NetworkInspectorDialog(QDialog):
    """Cross-platform, read-only view of the network information Ember already exposes."""

    _result = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Network Inspector")
        self.setMinimumSize(760, 560)
        self._result.connect(self._show_result)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(9)
        v.addWidget(_dialog_header(
            "Network Inspector", "Review local network activity without changing system settings.",
            eyebrow="NETWORK", mark="⌁"))

        actions = QHBoxLayout()
        for label, key in (("Wi-Fi", "wifi"), ("Active connections", "connections"),
                           ("Listening ports", "ports"), ("Nearby devices", "devices")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, k=key: self._load(k))
            actions.addWidget(button)
        actions.addStretch()
        v.addLayout(actions)

        self.status = QLabel("Choose a view above.")
        self.status.setObjectName("dialogStatus")
        v.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.results = QListWidget()
        self.results.setObjectName("dialogList")
        v.addWidget(self.results, 1)

        footer = QHBoxLayout()
        privacy = QLabel("Read-only · no information is uploaded")
        privacy.setObjectName("dialogStatus")
        footer.addWidget(privacy)
        footer.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        v.addLayout(footer)
        _polish_dialog(self)

    def _load(self, key: str):
        self.results.clear()
        self.progress.setVisible(True)
        self.status.setText("Loading…")

        def work():
            try:
                if key == "ports":
                    import utilities
                    result = utilities.list_open_ports()
                else:
                    import nettools
                    result = {"wifi": nettools.wifi_info,
                              "connections": nettools.network_connections,
                              "devices": nettools.network_devices}[key]()
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            self._result.emit(key, result)

        threading.Thread(target=work, daemon=True).start()

    def _show_result(self, key: str, result: dict):
        self.progress.setVisible(False)
        if not result.get("ok"):
            self.status.setText(str(result.get("error", "Could not read network information.")))
            return
        rows = []
        if key == "wifi":
            rows = [f"{name.replace('_', ' ').title()}: {value}" for name, value in result.items()
                    if name != "ok" and value not in (None, "")]
        elif key == "connections":
            rows = [f"{r.get('process') or 'Unknown process'}  ·  {r.get('remote', '')}"
                    for r in result.get("connections", [])]
        elif key == "ports":
            rows = [f"{r.get('proto', '').upper()}  {r.get('addr', '')}  ·  "
                    f"{r.get('process') or 'Unknown process'}  ·  PID {r.get('pid') or '—'}"
                    for r in result.get("listening", [])]
        else:
            rows = [f"{r.get('ip', '')}  ·  {r.get('mac', '')}" for r in result.get("devices", [])]
        for text in rows:
            self.results.addItem(text)
        self.status.setText(f"{len(rows)} item(s)" if rows else "No items found.")


class ClipboardHistoryDialog(QDialog):
    """Ephemeral, window-scoped clipboard history. Nothing is persisted or uploaded."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clipboard History")
        self.setMinimumSize(720, 540)
        self._items: list[str] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(9)
        v.addWidget(_dialog_header(
            "Clipboard History", "Reuse recently copied text while this window remains open.",
            eyebrow="PRODUCTIVITY", mark="▣"))

        self.capture = QCheckBox("Capture clipboard changes while this window is open")
        self.capture.setChecked(True)
        v.addWidget(self.capture)

        split = QHBoxLayout()
        self.list = QListWidget()
        self.list.setObjectName("dialogList")
        self.list.setMaximumWidth(300)
        self.list.currentRowChanged.connect(self._show_selected)
        split.addWidget(self.list)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("codeSurface")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Select an item to preview it.")
        split.addWidget(self.preview, 1)
        v.addLayout(split, 1)

        footer = QHBoxLayout()
        clear = QPushButton("Clear history")
        clear.setObjectName("dangerBtn")
        clear.clicked.connect(self._clear)
        footer.addWidget(clear)
        footer.addStretch()
        self.use_btn = QPushButton("Copy selected")
        self.use_btn.setObjectName("primaryBtn")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self._use_selected)
        footer.addWidget(self.use_btn)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        v.addLayout(footer)

        note = QLabel("History is kept in memory only and is cleared when this window closes.")
        note.setObjectName("dialogStatus")
        v.addWidget(note)
        _polish_dialog(self)

        self._clipboard = QApplication.clipboard()
        self._clipboard.dataChanged.connect(self._capture)
        self._capture()

    def _capture(self):
        if not self.capture.isChecked():
            return
        text = (self._clipboard.text() or "").strip()
        if not text or (self._items and self._items[0] == text):
            return
        self._items.insert(0, text[:10000])
        self._items = self._items[:50]
        self._refresh()

    def _refresh(self):
        current = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for text in self._items:
            one_line = " ".join(text.split())
            self.list.addItem(one_line[:70] + ("…" if len(one_line) > 70 else ""))
        if self._items:
            self.list.setCurrentRow(max(0, min(current, len(self._items) - 1)))
        self.list.blockSignals(False)
        self._show_selected(self.list.currentRow())

    def _show_selected(self, row: int):
        valid = 0 <= row < len(self._items)
        self.preview.setPlainText(self._items[row] if valid else "")
        self.use_btn.setEnabled(valid)

    def _use_selected(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._items):
            self.capture.setChecked(False)
            self._clipboard.setText(self._items[row])
            self.capture.setChecked(True)

    def _clear(self):
        self._items.clear()
        self._refresh()

    def closeEvent(self, event):
        try:
            self._clipboard.dataChanged.disconnect(self._capture)
        except Exception:
            pass
        self._items.clear()
        super().closeEvent(event)


class SetupTourDialog(QDialog):
    """A friendly first-run wizard for people new to AI. Asks how comfortable they are, then
    sets up an AI brain in plain language — including a one-click install of the free offline
    AI — and applies good defaults. Calls on_finish(updates) with the settings to save."""

    def __init__(self, settings: dict, on_finish, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QStackedWidget, QRadioButton, QButtonGroup
        import setup_tour
        self._st = setup_tour
        self._settings = settings
        self._on_finish = on_finish
        self._level = (settings.get("experience_level") or "beginner")
        self.setWindowTitle("Welcome to Ember")
        self.setMinimumSize(620, 540)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)
        root.addWidget(_dialog_header(
            "Set up Ember", "Four quick choices, then your computer assistant is ready.",
            eyebrow="WELCOME", mark=DIAMOND_MARK))

        step_rail = QFrame()
        step_rail.setObjectName("stepRail")
        step_row = QHBoxLayout(step_rail)
        step_row.setContentsMargins(8, 0, 8, 0)
        self._step_labels = []
        for i, label in enumerate(("1  About you", "2  AI model", "3  Email", "4  Ready")):
            step = QLabel(label)
            step.setObjectName("stepDot")
            self._step_labels.append(step)
            step_row.addWidget(step)
            if i < 3:
                step_row.addStretch(1)
        root.addWidget(step_rail)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # ---- Page 0: experience level ----
        p0 = QWidget(); l0 = QVBoxLayout(p0)
        t = QLabel("👋  Welcome to Ember"); t.setObjectName("title")
        l0.addWidget(t)
        l0.addWidget(self._wrap("Setup takes about a minute. First, choose how much detail you "
                                "want Ember to show while you work."))
        self._level_group = QButtonGroup(self)
        for lvl in setup_tour.LEVELS:
            rb = QRadioButton(setup_tour.LEVEL_LABELS[lvl])
            rb.setProperty("level", lvl)
            rb.setChecked(lvl == self._level)
            self._level_group.addButton(rb)
            l0.addWidget(rb)
        l0.addStretch(1)
        self._stack.addWidget(p0)

        # ---- Page 1: choose the AI brain ----
        p1 = QWidget(); l1 = QVBoxLayout(p1)
        t1 = QLabel("Choose your AI"); t1.setObjectName("title")
        l1.addWidget(t1)
        l1.addWidget(self._wrap("Pick how Ember thinks. You can change this any time in Settings."))
        self._brain_group = QButtonGroup(self)
        self._rb_offline = QRadioButton("🟢  Free offline AI — runs on your computer (no internet, "
                                        "no account, completely private)")
        self._rb_offline.setChecked(True)
        self._brain_group.addButton(self._rb_offline)
        l1.addWidget(self._rb_offline)
        self._offline_status = QLabel("")
        self._offline_status.setStyleSheet("color:#9aa0b5; font-size:11px; margin-left:24px;")
        self._offline_status.setWordWrap(True)
        l1.addWidget(self._offline_status)
        self._install_btn = QPushButton("⬇  Install the free offline AI")
        self._install_btn.clicked.connect(self._install_offline_ai)
        l1.addWidget(self._install_btn)

        self._rb_gemini = QRadioButton("☁️  Free online AI (Google Gemini) — needs a free key")
        self._brain_group.addButton(self._rb_gemini)
        l1.addWidget(self._rb_gemini)
        self._gemini_key = QLineEdit(self._settings.get("gemini_api_key", ""))
        self._gemini_key.setPlaceholderText("Paste your free Google AI key here")
        l1.addWidget(self._gemini_key)
        getkey = QLabel('<a href="https://aistudio.google.com/apikey" '
                        'style="color:#7aa2f7;">Get a free key →</a>')
        getkey.setOpenExternalLinks(True)
        getkey.setStyleSheet("font-size:11px; margin-left:2px;")
        l1.addWidget(getkey)
        l1.addStretch(1)
        self._stack.addWidget(p1)

        # ---- Page 2: connect Gmail (optional) ----
        pg = QWidget(); lg = QVBoxLayout(pg)
        tg = QLabel("📧  Organise your email"); tg.setObjectName("title")
        lg.addWidget(tg)
        lg.addWidget(self._wrap(setup_tour.gmail_setup_hint()))
        self._gmail_addr = QLineEdit(self._settings.get("gmail_address")
                                     or self._settings.get("email_smtp_user", ""))
        self._gmail_addr.setPlaceholderText("you@gmail.com")
        lg.addWidget(self._gmail_addr)
        self._gmail_pw = QLineEdit(self._settings.get("gmail_app_password")
                                   or self._settings.get("email_smtp_password", ""))
        self._gmail_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self._gmail_pw.setPlaceholderText("16-character Google App Password")
        lg.addWidget(self._gmail_pw)
        gkey = QLabel(f'<a href="{setup_tour.APP_PASSWORD_URL}" style="color:#7aa2f7;">'
                      'Create an App Password →</a>  (needs 2-Step Verification)')
        gkey.setOpenExternalLinks(True)
        gkey.setStyleSheet("font-size:11px; margin-left:2px;")
        lg.addWidget(gkey)
        lg.addStretch(1)
        self._stack.addWidget(pg)

        # ---- Page 3: finish + what Ember can do ----
        p2 = QWidget(); l2 = QVBoxLayout(p2)
        t2 = QLabel("You're all set"); t2.setObjectName("title")
        l2.addWidget(t2)
        self._finish_note = QLabel("")
        self._finish_note.setWordWrap(True)
        l2.addWidget(self._finish_note)
        cando = QLabel("<b>Here's what you can ask Ember to do:</b><br>"
                       + "<br>".join(setup_tour.feature_highlights()))
        cando.setWordWrap(True)
        cando.setTextFormat(Qt.TextFormat.RichText)
        cando.setStyleSheet("color:#c3c9db; font-size:12px; margin-top:6px;")
        l2.addWidget(cando)
        l2.addStretch(1)
        self._stack.addWidget(p2)

        # ---- nav bar ----
        nav = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(lambda: self._goto(self._stack.currentIndex() - 1))
        self._skip_btn = QPushButton("Set up later")
        self._skip_btn.clicked.connect(self.reject)
        self._next_btn = QPushButton("Next")
        self._next_btn.setObjectName("send")
        self._next_btn.clicked.connect(self._next)
        nav.addWidget(self._back_btn)
        nav.addWidget(self._skip_btn)
        nav.addStretch(1)
        nav.addWidget(self._next_btn)
        root.addLayout(nav)
        _polish_dialog(self)
        self._refresh_offline_status()
        self._goto(0)

    def _wrap(self, text):
        lb = QLabel(text); lb.setWordWrap(True)
        lb.setStyleSheet("color:#c3c9db; font-size:13px;")
        return lb

    def _goto(self, idx):
        idx = max(0, min(self._stack.count() - 1, idx))
        self._stack.setCurrentIndex(idx)
        self._back_btn.setEnabled(idx > 0)
        last = idx == self._stack.count() - 1
        self._next_btn.setText("Finish" if last else "Next")
        self._skip_btn.setVisible(not last)
        for i, step in enumerate(self._step_labels):
            step.setProperty("active", i == idx)
            step.style().unpolish(step)
            step.style().polish(step)
        if last:
            self._finish_note.setText(self._summary())

    def _next(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            btn = self._level_group.checkedButton()
            self._level = btn.property("level") if btn else "beginner"
        if idx >= self._stack.count() - 1:
            self._finish()
            return
        self._goto(idx + 1)

    def _refresh_offline_status(self):
        if self._st.ollama_installed():
            self._offline_status.setText("✓ Installed and ready on this computer.")
            self._install_btn.setVisible(False)
        else:
            plan = self._st.ollama_install_plan()
            self._offline_status.setText("Not installed yet — one click sets it up.")
            self._install_btn.setText("⬇  " + plan["label"])

    def _install_offline_ai(self):
        import webbrowser
        import subprocess
        plan = self._st.ollama_install_plan()
        try:
            if plan["method"] == "download":
                webbrowser.open(plan["url"])
                self._offline_status.setText("Opened the download page — install it, then come back.")
            elif sys.platform == "darwin" and plan.get("command"):
                cmd = " ".join(plan["command"])
                subprocess.Popen(["osascript", "-e",
                                  f'tell application "Terminal" to do script "{cmd}"'])
                self._offline_status.setText("Installing in Terminal… follow any prompts there, "
                                             "then reopen this tour.")
            elif plan.get("command"):
                subprocess.Popen(plan["command"])
                self._offline_status.setText("Installing… this can take a few minutes.")
        except Exception as e:
            webbrowser.open("https://ollama.com/download")
            self._offline_status.setText(f"Couldn't auto-install ({e}) — opened the download page.")

    def _summary(self):
        if self._rb_offline.isChecked():
            pull = self._st.recommended_model_pull(self._level)
            return ("Ember will use the **free offline AI**. If you haven't yet, install it above, "
                    f"then it'll fetch a model (`ollama pull {pull}`) the first time. "
                    "Finish to start.")
        if self._rb_gemini.isChecked():
            return ("Ember will use **free online AI (Google)**. Make sure your key is pasted above. "
                    "Finish to start.")
        return "Finish to start."

    def _finish(self):
        updates = self._st.recommended_settings(self._level)
        if self._rb_offline.isChecked():
            updates["model_id"] = "ollama"
            updates["gemini_model"] = "ollama"
            updates["provider"] = "ollama"
        elif self._rb_gemini.isChecked():
            key = self._gemini_key.text().strip()
            if key:
                updates["gemini_api_key"] = key
            updates["model_id"] = "auto"
            updates["gemini_model"] = "auto"
            updates["provider"] = "gemini"
        # Optional Gmail connect — one App Password powers organising + sending.
        updates.update(self._st.gmail_settings_from(self._gmail_addr.text(),
                                                    self._gmail_pw.text()))
        try:
            self._on_finish(updates)
        except Exception:
            pass
        self.accept()


class TerminalDialog(QDialog):
    """Built-in terminal + Python runner — run shell commands and Python without leaving Ember
    (beats Open Interpreter: it's in-app and the Python session persists between runs). Shell runs
    on a worker thread so the UI never freezes; Python runs in an in-process REPL (terminal.py)."""

    _out_sig = pyqtSignal(str)
    _busy_sig = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ember Terminal")
        self.setMinimumSize(720, 520)
        import terminal
        self._terminal = terminal
        self._repl = terminal.PyRepl()
        self._busy = False

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(9)
        v.addWidget(_dialog_header(
            "Terminal", "Run shell commands or use a persistent Python session without leaving Ember.",
            eyebrow="DEVELOPER TOOL", mark=">_"))

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("codeSurface")
        self.output.setPlaceholderText("Command output appears here.")
        v.addWidget(self.output, 1)

        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Shell", "Python"])
        row.addWidget(self.mode)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command and press Enter…")
        self.input.returnPressed.connect(self._run)
        row.addWidget(self.input, 1)
        self.run_btn = QPushButton("Run")
        self.run_btn.setObjectName("primaryBtn")
        self.run_btn.clicked.connect(self._run)
        row.addWidget(self.run_btn)
        v.addLayout(row)

        btns = QHBoxLayout()
        clr = QPushButton("Clear")
        clr.setObjectName("secondaryBtn")
        clr.clicked.connect(self.output.clear)
        rst = QPushButton("Reset Python")
        rst.setObjectName("secondaryBtn")
        rst.clicked.connect(self._reset)
        btns.addWidget(clr)
        btns.addWidget(rst)
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        v.addLayout(btns)

        self._out_sig.connect(self._append)
        self._busy_sig.connect(self._set_busy)
        _polish_dialog(self)
        self.input.setFocus()

    def _append(self, text: str):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.run_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if not busy:
            self.input.setFocus()

    def _reset(self):
        self._repl.reset()
        self._append("\n[python session reset]\n")

    def _run(self):
        if self._busy:
            return
        line = self.input.text().strip()
        if not line:
            return
        self.input.clear()
        mode = self.mode.currentText().lower()
        s = line.lstrip()
        if s[:1] in ("!", "$") or s.startswith(">>>") or s[:3].lower() == "py ":
            mode = self._terminal.classify(line)
            line = self._terminal.strip_marker(line)
        if mode == "python":
            self._append(f"\n>>> {line}\n")
            res = self._repl.run(line)
            if res.get("stdout"):
                self._append(res["stdout"])
            if res.get("stderr"):
                self._append(res["stderr"])
            return
        # shell — run off the UI thread
        self._append(f"\n$ {line}\n")
        self._set_busy(True)
        import threading

        def work():
            try:
                res = self._terminal.run_shell(line)
                if isinstance(res, dict):
                    out = res.get("output") or res.get("stdout") or res.get("result") or ""
                    if not res.get("ok", True) and res.get("error"):
                        out = (out + "\n" + str(res["error"])).strip()
                    if res.get("stderr"):
                        out = (out + "\n" + str(res["stderr"])).strip()
                else:
                    out = str(res)
                self._out_sig.emit((out or "(no output)") + "\n")
            except Exception as e:
                self._out_sig.emit(f"[error] {type(e).__name__}: {e}\n")
            finally:
                self._busy_sig.emit(False)

        threading.Thread(target=work, daemon=True).start()


class AgentsDialog(QDialog):
    """Parallel agent-tasks dashboard — start several Ember jobs and watch them run side by side
    (beats Nimbalyst's single-track flow). Backed by an agent_tasks.TaskManager passed in."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parallel Runs")
        self.setMinimumSize(780, 560)
        self._m = manager
        self._ids: list = []

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(9)
        v.addWidget(_dialog_header(
            "Parallel Runs", "Start independent background tasks and inspect each live result.",
            eyebrow="AGENT ORCHESTRATION", mark="⇶"))

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Describe a task, e.g. ‘research the best budget laptops and summarize’")
        self.input.returnPressed.connect(self._start)
        row.addWidget(self.input, 1)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self._start)
        row.addWidget(self.start_btn)
        v.addLayout(row)

        split = QHBoxLayout()
        self.list = QListWidget()
        self.list.setObjectName("dialogList")
        self.list.setMaximumWidth(300)
        self.list.currentRowChanged.connect(lambda _i: self._refresh_output())
        split.addWidget(self.list)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("codeSurface")
        self.output.setPlaceholderText("Select a run to inspect its output.")
        split.addWidget(self.output, 1)
        v.addLayout(split, 1)

        btns = QHBoxLayout()
        self.stop_btn = QPushButton("Stop selected")
        self.stop_btn.setObjectName("dangerBtn")
        self.stop_btn.clicked.connect(self._stop)
        clr = QPushButton("Clear finished")
        clr.setObjectName("secondaryBtn")
        clr.clicked.connect(self._clear)
        btns.addWidget(self.stop_btn)
        btns.addWidget(clr)
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        v.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(700)
        _polish_dialog(self)
        self._refresh()
        self.input.setFocus()

    def _start(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        try:
            self._m.start(text)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't start task", str(e))
        self._refresh()

    def _selected_id(self):
        i = self.list.currentRow()
        return self._ids[i] if 0 <= i < len(self._ids) else None

    def _stop(self):
        tid = self._selected_id()
        if tid:
            self._m.stop(tid)
            self._refresh()

    def _clear(self):
        self._m.clear_finished()
        self._refresh()

    def _refresh(self):
        tasks = self._m.list()
        sel = self._selected_id()
        self._ids = [t["id"] for t in tasks]
        icon = {"running": "⏳", "done": "✅", "error": "⚠️", "stopped": "⏹"}
        self.list.blockSignals(True)
        self.list.clear()
        for t in tasks:
            self.list.addItem(f"{icon.get(t['status'], '•')}  {t['label']}")
        if sel in self._ids:
            self.list.setCurrentRow(self._ids.index(sel))
        elif self._ids:
            self.list.setCurrentRow(len(self._ids) - 1)
        self.list.blockSignals(False)
        self._refresh_output()

    def _refresh_output(self):
        tid = self._selected_id()
        if not tid:
            self.output.setPlainText("")
            return
        t = self._m.get(tid)
        if not t:
            return
        body = f"[{t['status']}] {t['prompt']}\n\n{t['output']}"
        if t.get("error"):
            body += f"\n\n[error] {t['error']}"
        if self.output.toPlainText() != body:
            sb = self.output.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self.output.setPlainText(body)
            if at_bottom:
                self.output.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, e):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(e)


class RemoteLinkDialog(QDialog):
    """Ember Link: control this computer from a phone/tablet — on the same Wi-Fi, or (opt-in)
    from anywhere via a public tunnel. Pairing works like this: a device on the Wi-Fi enters the
    PIN once and the phone silently exchanges it for a long-lived token; that token (not the PIN)
    is what remote connections use, so the short PIN never has to face the public internet.
    Tunnel start/stop runs off the UI thread (opening a tunnel can take a few seconds)."""

    _remote_result = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ember Link")
        self.setMinimumSize(520, 420)
        import remote_server
        self._rs = remote_server
        self._remote_result.connect(self._on_remote_result)
        self._busy = False

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)
        v.addWidget(_dialog_header(
            "Ember Link", "Use this computer securely from your phone or tablet.",
            eyebrow="REMOTE CONTROL", mark="↗"))

        local_card = QFrame()
        local_card.setObjectName("surfaceCard")
        local_layout = QVBoxLayout(local_card)
        local_layout.setContentsMargins(12, 9, 12, 11)
        local_title = QLabel("ON THIS WI-FI")
        local_title.setObjectName("fieldLabel")
        local_layout.addWidget(local_title)

        self.local_info = QLabel("")
        self.local_info.setWordWrap(True)
        self.local_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        local_layout.addWidget(self.local_info)

        local_layout.addWidget(self._hint(
            "Open the address above on a nearby device, then enter the one-time PIN."))
        v.addWidget(local_card)

        remote_card = QFrame()
        remote_card.setObjectName("surfaceCard")
        remote_layout = QVBoxLayout(remote_card)
        remote_layout.setContentsMargins(12, 9, 12, 11)
        remote_title = QLabel("AWAY FROM HOME")
        remote_title.setObjectName("fieldLabel")
        remote_layout.addWidget(remote_title)

        self.remote_check = QCheckBox("Connect from anywhere (beyond this Wi-Fi)")
        self.remote_check.toggled.connect(self._toggle_remote)
        remote_layout.addWidget(self.remote_check)

        self._remote_url = ""   # the raw URL behind remote_info's HTML, for the Copy button
        remote_row = QHBoxLayout()
        self.remote_info = QLabel("")
        self.remote_info.setWordWrap(True)
        self.remote_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        remote_row.addWidget(self.remote_info, 1)
        self.copy_link_btn = QPushButton("Copy")
        self.copy_link_btn.setVisible(False)
        self.copy_link_btn.clicked.connect(self._copy_remote_url)
        remote_row.addWidget(self.copy_link_btn)
        remote_layout.addLayout(remote_row)

        remote_layout.addWidget(self._hint(
            "Uses an outbound Cloudflare Tunnel (free, no account) — nothing is opened on your "
            "router. The link carries a long, unguessable token instead of the short PIN, so the "
            "PIN is never exposed to the internet. Treat the link like a password; 'Revoke all "
            "pairings' below invalidates every issued link instantly."))

        self.paired_label = QLabel("")
        self.paired_label.setObjectName("dialogStatus")
        remote_layout.addWidget(self.paired_label)
        v.addWidget(remote_card, 1)

        btns = QHBoxLayout()
        self.revoke_btn = QPushButton("Revoke all pairings")
        self.revoke_btn.setObjectName("dangerBtn")
        self.revoke_btn.clicked.connect(self._revoke)
        btns.addWidget(self.revoke_btn)
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        v.addLayout(btns)

        _polish_dialog(self)
        self._refresh()

    def _hint(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("muted")
        return lab

    def _set_remote_url_display(self, link: str):
        """`link` is the FULL magic link (tunnel URL + an embedded #tok= pairing token). The token
        is what makes it work from a phone that never paired on this exact origin AND what keeps it
        safe (43 random chars, unguessable), so it belongs on the clipboard via Copy rather than
        being read or typed off the screen."""
        self._remote_url = link  # what the Copy button puts on the clipboard (full link)
        if link:
            base = link.split("#", 1)[0]
            self.remote_info.setText(
                "<b>Your private link is ready.</b> Tap <b>Copy</b>, then open it on your phone "
                "from any network — it signs in automatically, no PIN needed. Keep it secret: "
                "anyone with this link can control this computer.<br>"
                f"<span style='color:#8a8f98'>{base}#…</span>")
        else:
            self.remote_info.setText("")
        self.copy_link_btn.setVisible(bool(link))

    def _refresh(self):
        st = self._rs.status()
        if st.get("running"):
            self.local_info.setText(
                f"<b>{st.get('url','')}</b>  ·  PIN <b>{st.get('pin','')}</b>")
        else:
            self.local_info.setText("Ember Link is not running.")
        link = st.get("remote_link") or ""
        remote_on = bool(link or st.get("remote_url"))
        self.remote_check.blockSignals(True)
        self.remote_check.setChecked(remote_on)
        self.remote_check.blockSignals(False)
        self._set_remote_url_display(link)
        n = st.get("paired", 0)
        self.paired_label.setText(f"{n} device(s) paired for remote access." if n
                                  else "No devices paired yet.")

    def _toggle_remote(self, on: bool):
        if self._busy:
            return
        if not self._rs.status().get("running"):
            QMessageBox.warning(self, "Ember Link", "Start Ember Link first (open it from the "
                                "Command Center), then enable remote access.")
            self.remote_check.blockSignals(True); self.remote_check.setChecked(False)
            self.remote_check.blockSignals(False)
            return
        self._busy = True
        self.remote_check.setEnabled(False)
        self.copy_link_btn.setVisible(False)
        self.remote_info.setText("Starting tunnel…" if on else "Stopping…")

        def work():
            res = self._rs.enable_remote() if on else self._rs.disable_remote()
            res["_enabling"] = on
            self._remote_result.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_remote_result(self, res: dict):
        self._busy = False
        self.remote_check.setEnabled(True)
        if res.get("_enabling") and not res.get("ok"):
            self.remote_check.blockSignals(True); self.remote_check.setChecked(False)
            self.remote_check.blockSignals(False)
            hint = res.get("install") or ""
            QMessageBox.warning(self, "Couldn't enable remote access",
                                (res.get("error") or "Unknown error") + (f"\n\n{hint}" if hint else ""))
        elif res.get("_enabling") and res.get("ok") and (res.get("link") or res.get("url")):
            # Trust the just-returned link for the immediate UI update rather than depending on a
            # second status() round-trip landing before _refresh() below reads it.
            self._set_remote_url_display(res.get("link") or res.get("url"))
        self._refresh()

    def _copy_remote_url(self):
        if self._remote_url:
            QApplication.clipboard().setText(self._remote_url)
            self._set_status("Link copied.")

    def _set_status(self, text: str) -> None:
        """Forward to the main window's status bar (this dialog has none of its own)."""
        try:
            win = self.parent()
            if win is not None and hasattr(win, "_set_status"):
                win._set_status(text)
        except Exception:
            pass

    def _revoke(self):
        if QMessageBox.question(
                self, "Revoke remote access",
                "Revoke every paired device and private link? Devices will need to pair again."
                ) != QMessageBox.StandardButton.Yes:
            return
        r = self._rs.revoke_pairings()
        QMessageBox.information(self, "Ember Link", f"Revoked {r.get('revoked', 0)} paired device(s). "
                                "They'll need to re-pair on your Wi-Fi.")
        self._refresh()


class DownloadGuardDialog(QDialog):
    """Front-and-centre review popup for a freshly-downloaded executable/threat.

    A downloaded executable is a real risk, so instead of a passive toast Ember pops this
    dialog OVER the current screen (the caller surfaces the window first) and holds the user's
    attention with clear actions:
      * Scan with AI  — a deep scan (static engines + an AI second opinion from whatever model
        the user has a key for) run OFF the UI thread; the verdict updates the dialog and, if
        malicious, the file is auto-quarantined so it can't be opened.
      * Quarantine    — move it out of Downloads so a double-click can't run it (the 'block'.)
      * Delete        — remove it.
      * Keep          — dismiss and leave it in place.
    """
    _scan_done = pyqtSignal(dict)

    def __init__(self, parent=None, *, name="", detail="", level="caution", path="", settings=None):
        super().__init__(parent)
        self._path = path
        self._name = name or (Path(path).name if path else "a file")
        self._settings = settings or {}
        self._scanning = False
        self._scan_done.connect(self._on_scan_done)
        # Sit above other windows so it's genuinely in front of whatever the user was doing.
        try:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        except Exception:
            pass
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle("⚠ Download protection")
        self.setMinimumWidth(460)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        head = QLabel(("⚠ Threat in your download" if level == "threat"
                       else "⚠ You just downloaded an executable"))
        head.setStyleSheet("font-size:16px; font-weight:700; color:#ff8a5c;")
        v.addWidget(head)

        import html as _html
        info = QLabel(f"<b>{_html.escape(self._name)}</b><br>"
                      f"<span style='color:#9aa0b5'>{_html.escape(detail)}</span>")
        info.setWordWrap(True)
        v.addWidget(info)

        self._status = QLabel("Don't open it unless you trust the source. "
                              "Run a scan to be sure.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#c0c6dc; font-size:12.5px;")
        v.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)      # indeterminate
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setVisible(False)
        v.addWidget(self._bar)

        row = QHBoxLayout()
        self._scan_btn = QPushButton("🔍  Scan with AI")
        try:
            self._scan_btn.setObjectName("send")   # primary styling if the theme defines it
        except Exception:
            pass
        self._scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_btn.clicked.connect(self._start_scan)
        self._quar_btn = QPushButton("🔒  Quarantine")
        self._quar_btn.clicked.connect(self._quarantine)
        self._del_btn = QPushButton("🗑  Delete")
        self._del_btn.setObjectName("dangerBtn")
        self._del_btn.clicked.connect(self._delete)
        self._keep_btn = QPushButton("Keep")
        self._keep_btn.clicked.connect(self.reject)
        for b in (self._scan_btn, self._quar_btn, self._del_btn, self._keep_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        v.addLayout(row)

        # No key configured -> tell the user the scan will only use local engines.
        try:
            import ai_detect
            if not ai_detect.ai_available(self._settings):
                self._scan_btn.setText("🔍  Scan")
                self._scan_btn.setToolTip("Add a Gemini or Claude API key in Settings for an AI "
                                          "second opinion. Scans use local engines meanwhile.")
        except Exception:
            pass
        _polish_dialog(self)

    # -- scan (off the UI thread) --
    def _start_scan(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.setEnabled(False)
        self._status.setText("Scanning… (static engines + AI second opinion)")
        self._bar.setVisible(True)
        path = self._path

        def work():
            out = {}
            try:
                import antivirus
                scan = antivirus.scan_file(path, deep=False)
                out["scan"] = scan
                reasons = scan.get("reasons") if isinstance(scan, dict) else None
                out["ai"] = antivirus.ai_assess_file(path, reasons)
            except Exception as e:
                out["error"] = str(e)
            self._scan_done.emit(out)

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_done(self, out: dict):
        self._scanning = False
        self._bar.setVisible(False)
        self._scan_btn.setEnabled(True)
        if out.get("error"):
            self._status.setText(f"Scan failed: {out['error']}")
            return
        scan = out.get("scan") or {}
        ai = out.get("ai") or {}
        verdict = scan.get("verdict", "unknown")
        ai_verdict = ai.get("verdict", "unknown")
        reasons = scan.get("reasons") or []
        # Malicious by either the engines or the AI -> quarantine immediately.
        if verdict == "malicious" or ai_verdict == "malicious":
            src = "the engines" if verdict == "malicious" else "an AI review"
            done = self._do_quarantine(reason=f"malicious ({src})")
            self._status.setText(
                f"<span style='color:#ff6b6b'><b>Malicious</b> — flagged by {src}. "
                + ("Quarantined so it can't run." if done else "Please delete it.") + "</span>")
            self._quar_btn.setEnabled(not done)
            self._keep_btn.setText("Close")
            return
        if verdict == "suspicious":
            extra = ("; ".join(str(r) for r in reasons[:3])) if reasons else "heuristics"
            note = (" AI didn't find anything malicious, but " if ai.get("available") else " ")
            self._status.setText(
                f"<span style='color:#e0af68'><b>Suspicious</b> ({extra}).</span>{note}"
                "Only keep it if you trust the source.")
            return
        # Clean.
        if ai.get("available"):
            self._status.setText("<span style='color:#6cc07a'>No known indicators found; AI "
                                 "triage also found no obvious malicious intent.</span> AI is "
                                 "advisory, not a malware engine. Only open files you trust.")
        else:
            self._status.setText("<span style='color:#6cc07a'>No known indicators found by the "
                                 "local engines.</span> This is not proof the file is safe. Only "
                                 "open files you trust.")

    # -- actions --
    def _do_quarantine(self, reason="user quarantined a download") -> bool:
        try:
            import antivirus
            return bool(antivirus.quarantine_file(self._path, reasons=[reason]).get("ok"))
        except Exception:
            return False

    def _quarantine(self):
        if self._do_quarantine():
            QMessageBox.information(self, "Quarantined",
                                    f"'{self._name}' was moved to quarantine — it can't be opened "
                                    "from Downloads now.")
            self.accept()
        else:
            QMessageBox.warning(self, "Quarantine", "Could not quarantine the file.")

    def _delete(self):
        if QMessageBox.question(
                self, "Delete download",
                f"Permanently delete ‘{self._name}’? This cannot be undone."
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            Path(self._path).unlink()
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Delete", f"Could not delete: {e}")


class UpdateDialog(QDialog):
    """A friendly 'a new version is ready' dialog with Skip / Install on Quit / Install &
    Relaunch, plus an 'auto-update in future' checkbox — the standard desktop-app update prompt."""

    def __init__(self, new_version: str, current: str, notes: str = "",
                 auto_update: bool = True, parent=None):
        super().__init__(parent)
        self.choice = None                 # "relaunch" | "on_quit" | "skip" | None
        self.auto_update = auto_update
        self.setWindowTitle("Update Ember")
        self.setMinimumWidth(560)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(16)
        icon = QLabel()
        try:
            p = _base_dir() / "icon.png"
            if not p.exists() and getattr(sys, "frozen", False):
                p = Path(getattr(sys, "_MEIPASS", _base_dir())) / "icon.png"
            if p.exists():
                icon.setPixmap(QPixmap(str(p)).scaled(
                    64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass
        icon.setFixedSize(64, 64)
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(6)
        title = QLabel("A new version of Ember is ready to install!")
        title.setStyleSheet("font-size: 16px; font-weight: 800;")
        title.setWordWrap(True)
        col.addWidget(title)
        sub = QLabel(f"Ember {new_version} is available (you have {current}). "
                     "Would you like to install it and relaunch Ember now?")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #c8d0e0;")
        col.addWidget(sub)
        if notes:
            nlbl = QLabel(notes[:300] + ("…" if len(notes) > 300 else ""))
            nlbl.setWordWrap(True)
            nlbl.setStyleSheet("color: #8b93a7; font-size: 11px;")
            col.addWidget(nlbl)
        top.addLayout(col, 1)
        v.addLayout(top)

        self.auto_check = QCheckBox("Automatically download and install updates in the future")
        self.auto_check.setChecked(bool(auto_update))
        v.addWidget(self.auto_check)

        row = QHBoxLayout()
        skip = QPushButton("Skip this version")
        skip.clicked.connect(lambda: self._pick("skip"))
        row.addWidget(skip)
        row.addStretch()
        on_quit = QPushButton("Install when I quit")
        on_quit.clicked.connect(lambda: self._pick("on_quit"))
        row.addWidget(on_quit)
        relaunch = QPushButton("Install and relaunch")
        relaunch.setObjectName("primaryBtn")
        relaunch.setDefault(True)
        relaunch.clicked.connect(lambda: self._pick("relaunch"))
        row.addWidget(relaunch)
        v.addLayout(row)
        _polish_dialog(self)

    def _pick(self, choice: str):
        self.choice = choice
        self.auto_update = self.auto_check.isChecked()
        self.accept()


class OllamaChatPanel(QWidget):
    """A fully-offline chat with local Ollama models — no API key, no internet. Also hosts
    'Council' mode, where several local models draft, critique each other, and combine into one
    answer (ollama_council). Original, clean design — not a copy of any other app's interface."""

    _reply = pyqtSignal(str)      # final assistant text -> append to transcript
    _status = pyqtSignal(str)     # progress line (council phases / thinking)
    _fail = pyqtSignal(str)
    _confirm = pyqtSignal(object)  # a tool needs approval (PendingConfirmation)

    OLLAMA_BASE = "http://localhost:11434"

    def __init__(self, settings: dict | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or {}
        self._messages: list[dict] = []   # [{role, content}] for context
        self._busy = False
        self._agent = None                # tool-capable offline OllamaAgent (lazy)
        self._agent_model = None
        self._stream_buf = ""
        self._build()
        self._reply.connect(self._on_reply)
        self._status.connect(lambda s: self.status_label.setText(s))
        self._fail.connect(self._on_fail)
        self._confirm.connect(self._on_confirm)
        QTimer.singleShot(0, self._refresh_models)

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 12)
        v.setSpacing(8)

        header = QLabel("Local chat")
        header.setObjectName("sectionTitle")
        v.addWidget(header)

        top = QHBoxLayout()
        top.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        top.addWidget(self.model_combo, 1)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedWidth(34)
        self.refresh_btn.setToolTip("Refresh the list of installed Ollama models")
        self.refresh_btn.clicked.connect(self._refresh_models)
        top.addWidget(self.refresh_btn)
        v.addLayout(top)

        self.council_check = QCheckBox("Council — let several models team up on each answer")
        self.council_check.setToolTip(
            "The selected models each draft an answer, read each other's drafts to improve, then "
            "one combines them into a single smarter answer. Slower, but better than any one model.")
        self.council_check.toggled.connect(self._on_council_toggled)
        v.addWidget(self.council_check)

        self.council_list = QListWidget()
        self.council_list.setMaximumHeight(96)
        self.council_list.setVisible(False)
        self.council_list.setToolTip("Tick the models that should join the council.")
        v.addWidget(self.council_list)

        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setObjectName("chatTranscript")
        v.addWidget(self.transcript, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("panelHint")
        self.status_label.setWordWrap(True)
        v.addWidget(self.status_label)

        row = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setMaximumHeight(84)
        self.input.setPlaceholderText("Message your local models…  (offline · no API key)")
        row.addWidget(self.input, 1)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self._on_send)
        row.addWidget(self.send_btn)
        v.addLayout(row)

        hint = QLabel("Runs fully offline on your local Ollama models — no internet, no API key.")
        hint.setObjectName("panelHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

    # -- models ---------------------------------------------------------------
    def _base(self) -> str:
        return self.settings.get("ollama_base") or self.OLLAMA_BASE

    def _refresh_models(self):
        try:
            import ollama_council
            models = ollama_council.available_models(self._base())
        except Exception:
            models = []
        cur = self.model_combo.currentText()
        self.model_combo.clear()
        self.council_list.clear()
        if not models:
            self.model_combo.addItem("(no Ollama models found — install Ollama + pull a model)")
            self.model_combo.setEnabled(False)
            self.status_label.setText(
                "No local models detected. Install Ollama (ollama.com), then e.g. `ollama pull "
                "llama3.1` and `ollama pull qwen2.5`, and press ↻.")
            return
        self.model_combo.setEnabled(True)
        for m in models:
            self.model_combo.addItem(m)
        if cur in models:
            self.model_combo.setCurrentText(cur)
        for i, m in enumerate(models):
            it = QListWidgetItem(m)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if i < 3 else Qt.CheckState.Unchecked)
            self.council_list.addItem(it)
        self.status_label.setText("")

    def _on_council_toggled(self, on: bool):
        self.council_list.setVisible(on)

    def _selected_council(self) -> list:
        out = []
        for i in range(self.council_list.count()):
            it = self.council_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.text())
        return out

    # -- send / receive -------------------------------------------------------
    def _on_send(self):
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        council = self.council_check.isChecked()
        models = self._selected_council() if council else [self.model_combo.currentText()]
        models = [m for m in models if m and not m.startswith("(")]
        if not models:
            self.status_label.setText("Pick at least one model first (press ↻ if the list is empty).")
            return
        self.input.clear()
        self._append("You", text)
        self._messages.append({"role": "user", "content": text})
        self._busy = True
        self.send_btn.setEnabled(False)
        self.status_label.setText("Thinking…")
        if council and len(models) > 1:
            threading.Thread(target=self._worker, args=(text, council, models), daemon=True).start()
        else:
            # Single model → the tool-capable offline agent, so the chat can SEE the screen and
            # control the mouse/keyboard/files (all local, no API key).
            self._send_via_agent(text, models[0])

    def _send_via_agent(self, text: str, model: str):
        try:
            self._ensure_agent(model)
            self._stream_buf = ""
            self._agent.send_user_message(text)
        except Exception as e:
            self._fail.emit(f"{type(e).__name__}: {e}")

    def _ensure_agent(self, model: str):
        """Build (or rebuild on model change) the offline tool-driving OllamaAgent and subscribe."""
        if self._agent is not None and self._agent_model == model:
            return
        from ollama_agent import OllamaAgent
        self._agent = OllamaAgent(model_name=model,
                                  auto_screenshot=bool(self.settings.get("auto_screenshot", True)))
        self._agent_model = model
        self._agent.subscribe(self._on_agent_event)

    def _on_agent_event(self, ev):
        """Marshal OllamaAgent events onto the UI thread via signals (they fire on its thread)."""
        kind = getattr(ev, "kind", None)
        payload = getattr(ev, "payload", None)
        if kind == "stream_chunk" and payload:
            self._stream_buf += str(payload)
        elif kind == "message" and payload:
            self._stream_buf += str(payload)
        elif kind == "tool_call":
            try:
                self._status.emit(f"Running {payload.get('name')}…")
            except Exception:
                self._status.emit("Using a tool…")
        elif kind == "confirm":
            self._confirm.emit(payload)   # ask on the UI thread
        elif kind == "error":
            self._fail.emit(str(payload)[:200])
        elif kind == "done":
            self._reply.emit(self._stream_buf.strip() or "(done)")

    def _on_confirm(self, pending):
        """A tool wants approval — ask, then hand the answer back to the agent thread."""
        try:
            name = getattr(pending, "tool_name", "an action")
            reason = getattr(pending, "reason", "")
            ok = QMessageBox.question(
                self, "Allow this action?",
                f"The local chat wants to run:\n\n{name}\n\n{reason}\n\nAllow it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes
            pending.response.put(ok)
        except Exception:
            try:
                pending.response.put(False)
            except Exception:
                pass

    def _worker(self, text: str, council: bool, models: list):
        try:
            import ollama_council
            history = "\n".join(f"{m['role']}: {m['content']}" for m in self._messages[:-1][-6:])
            if council and len(models) > 1:
                r = ollama_council.run_council(
                    text, models, ollama_council.default_completer(self._base()),
                    rounds=1, history=history,
                    on_event=lambda phase, m: self._status.emit(
                        {"propose": f"{m} is drafting…",
                         "refine": f"{m} is improving on the others…",
                         "synthesize": f"{m} is combining everyone's answers…"}.get(phase, m)))
                if not r.get("ok"):
                    self._fail.emit(r.get("error", "the council could not produce an answer"))
                    return
                self._reply.emit(r["final"])
            else:
                import ollama_agent
                prompt = (history + "\nuser: " + text) if history else text
                out = ollama_agent.quick_complete(prompt, model=models[0], base_url=self._base(),
                                                  timeout=120)
                if not out:
                    self._fail.emit("no reply (is Ollama running and the model pulled?)")
                    return
                self._reply.emit(out)
        except Exception as e:
            self._fail.emit(f"{type(e).__name__}: {e}")

    def _on_reply(self, text: str):
        self._messages.append({"role": "assistant", "content": text})
        self._append("Ember (local)", text)
        self.status_label.setText("")
        self._busy = False
        self.send_btn.setEnabled(True)

    def _on_fail(self, msg: str):
        self.status_label.setText(msg)
        self._busy = False
        self.send_btn.setEnabled(True)

    def _append(self, who: str, text: str):
        import html
        safe = html.escape(text).replace("\n", "<br>")
        colour = "#7aa2f7" if who == "You" else "#9ece6a"
        self.transcript.append(
            f'<div style="margin:6px 0;"><b style="color:{colour}">{html.escape(who)}</b><br>{safe}</div>')
        try:
            sb = self.transcript.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass


class EmberWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.chat_history = load_chat_history()
        self.active_chat_id = self.chat_history.get("active_id")
        self._history_loading = False
        self._really_quit = False     # set by _do_quit so closeEvent really exits
        self._tray = None             # set by main() so closeEvent can post a tray message
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
        self._bridge.update_progress.connect(self._on_update_progress)
        self._bridge.notice.connect(lambda m: self._add_bubble("system", m))
        self._bridge.source_updated.connect(self._on_source_updated)
        self._bridge.wake_detected.connect(self._on_wake_word)
        self._bridge.download_alert.connect(self._on_download_alert)
        self._bridge.live_voice_event.connect(self._on_live_voice_event)
        self._bridge.timer_fired.connect(self._on_timer_fired_ui)
        # Push-to-talk: key events + transcript marshalled from background threads to the UI.
        self._bridge.ptt_press.connect(self._on_ptt_press)
        self._bridge.ptt_release.connect(self._on_ptt_release)
        self._bridge.ptt_text.connect(self._on_ptt_text)
        self._bridge.ptt_state.connect(self._on_ptt_state)
        self._bridge.ptt_error.connect(self._on_ptt_error)
        self._ptt = None                 # PushToTalk coordinator (built on first install)
        self._ptt_recorder = None        # active voice.HoldRecorder during a press
        self._ptt_listener = None        # pynput key listener (non-macOS)
        self._ns_ptt_global = None       # macOS NSEvent monitors (key down/up)
        self._ns_ptt_local = None
        self._ns_ptt_up_global = None
        self._ns_ptt_up_local = None
        self._ptt_status = "off"
        self._pending_update: dict | None = None
        # macOS never auto-prompts for Accessibility, and Screen Recording / Microphone / Input
        # Monitoring only "sometimes" auto-prompt on unsigned builds — so on first launch Ember
        # explicitly primes EVERY permission it needs, in one pass, with a single clear guide.
        if not _SAFE_MODE:
            QTimer.singleShot(900, self._prime_permissions)
        self._listening = False
        self._voice_chat_enabled = False
        self._voice_waiting_for_reply = False
        self._voice_turns = 0
        self._live_voice = None          # active LiveVoice session (natural voice), if any
        self._lv_user_buf = ""           # accumulates the user's live transcript for the turn
        self._lv_ember_buf = ""          # accumulates Ember's live transcript for the turn
        self._orb_conversation = False   # True during a hands-free "Hey Ember" conversation
        self._title_jobs: set[str] = set()
        self._build_ui()
        self._ember_pointer = None
        self._install_ember_pointer()
        self._restore_position()
        # A successful replacement keeps its .old backup until this new build reaches the UI.
        # Confirm the result here, then clean up; failed swaps are reported after rollback.
        QTimer.singleShot(1200, self._report_update_result)
        if not _SAFE_MODE:
            self._install_hotkey()
            self._install_ptt()
        # macOS: record workflows via main-thread NSEvent monitors, NOT pynput (whose background
        # event tap SIGTRAP-crashes the app). Register a capture backend the recorder will use.
        self._mac_input_recorder = None
        if sys.platform == "darwin":
            try:
                import workflow_recorder as _wfr
                self._mac_input_recorder = _MacInputRecorder(self)
                _wfr.set_capture_backend(self._mac_input_recorder)
            except Exception:
                self._mac_input_recorder = None
        # Countdown timers: when one elapses (on a background thread) surface it in chat + speak.
        try:
            import timers as _timers
            _timers.set_fire_callback(self._on_timer_fired)
        except Exception:
            pass
        # Emergency lockdown: teach the panic module how to stop OUR agent (not just Ollama), and
        # arm auto-lockdown if the user enabled it.
        try:
            import panic as _panic
            def _stop_ai_for_panic():
                try:
                    if getattr(self, "agent", None) is not None:
                        self.agent.stop()
                except Exception:
                    pass
                return _panic._default_kill_ai()   # also stop the local LLM
            _panic.set_hooks(kill_ai=_stop_ai_for_panic)
            _panic.arm_auto(bool(self.settings.get("auto_lockdown_on_critical", False)))
        except Exception:
            pass
        self._overlay_timer = QTimer(self)
        self._overlay_timer.timeout.connect(self._keep_overlay_on_top)
        # Only re-assert top-most when the user actually wants always-on-top; otherwise this
        # timer would keep yanking Ember in front of whatever app you switched to.
        if bool(self.settings.get("always_on_top", False)):
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
        if self.settings.get("mcp_bridge_enabled", False) and not _SAFE_MODE:
            # Expose Ember's tools to external MCP clients (Claude Desktop, Cursor). Loopback
            # only + token-secured; off by default, so this only runs when the user opted in.
            QTimer.singleShot(1400, self._autostart_mcp_bridge)
        if self.settings.get("download_protection", True) and not _SAFE_MODE:
            # Real-time download protection: start the Downloads watcher in the background.
            QTimer.singleShot(1800, self._autostart_download_protection)
        if self.settings.get("fileless_protection", True) and not _SAFE_MODE:
            # Always-on fileless / behavioral malware protection (process monitor).
            QTimer.singleShot(2200, self._autostart_fileless_protection)
        if self.settings.get("realtime_security_center", True) and not _SAFE_MODE:
            # Unified always-on Security Center: continuously scans processes, files,
            # network and persistence, and keeps the other monitors alive (watchdog).
            QTimer.singleShot(2600, self._autostart_security_center)
        if self.settings.get("agent_scheduler", True) and not _SAFE_MODE:
            # Background agent scheduler: runs saved agents on their schedules.
            QTimer.singleShot(3000, self._autostart_agent_scheduler)
        if self.settings.get("wake_word", True) and not _SAFE_MODE:
            # Always-on "Hey Ember" wake word — starts listening shortly after launch.
            QTimer.singleShot(2400, self._autostart_wake_word)
        QTimer.singleShot(300, self._apply_tts_config)   # push read-aloud engine to voice
        QTimer.singleShot(350, self._apply_mouse_options)  # restore saved pointer speed/humanize
        self._apply_offline_mode()   # set the global offline flag from settings
        _offline_on = bool(self.settings.get("offline_mode", False))
        if self.settings.get("auto_update", True) and not _offline_on:
            # Auto-update on every launch (skipped in Offline Mode — no outbound calls). Frozen
            # .app: check + auto-install a published release. Git/source checkout: fast-forward.
            QTimer.singleShot(5000, self._check_for_update_async)
            QTimer.singleShot(1500, self._git_self_update)
        # Enough to start: a Gemini key, OR Claude selected with an Anthropic key, OR the
        # local Ollama brain (which needs no key at all).
        _prov = self.settings.get("provider") or model_catalog.provider_for(
            self.settings.get("model_id") or self.settings.get("gemini_model") or "")
        _can_start = bool(self.settings.get("gemini_api_key")) or _prov == "ollama" or (
            _prov == "claude" and self.settings.get("anthropic_api_key")) or (
            _prov == "openai" and self.settings.get("openai_api_key"))
        if _can_start:
            # Defer agent init (and the heavy google.genai import) so the window paints first.
            self._set_status("Starting…")
            QTimer.singleShot(0, self._init_agent)
        else:
            # Brand-new user with nothing set up: show the friendly Setup Tour, not the raw
            # Settings dialog. Falls back to Settings if the tour can't load.
            _show_tour = False
            try:
                import setup_tour
                _show_tour = setup_tour.should_show(self.settings)
            except Exception:
                _show_tour = False
            QTimer.singleShot(400, self._open_setup_tour if _show_tour else self._first_run_settings)

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
        for attr in ("_ns_hotkey_global", "_ns_hotkey_local"):
            m = getattr(self, attr, None)
            if m is not None:
                try:
                    from AppKit import NSEvent
                    NSEvent.removeMonitor_(m)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._hotkey_combo = None
        self._hotkey_status = "off"

    def _install_hotkey_mac(self, combo: str) -> bool:
        """macOS global hotkey via NSEvent monitors, which fire on the main run loop. This
        replaces pynput on macOS: pynput's background Quartz event tap builds NSEvents off the
        main thread, and macOS input-source/TSM calls (e.g. on CapsLock) then assert and crash
        the process (SIGTRAP). NSEvent monitors avoid that entirely."""
        try:
            from AppKit import NSEvent
        except Exception as e:
            print(f"[hotkey] AppKit unavailable: {e}")
            return False
        SHIFT, CTRL, OPT, CMD = 1 << 17, 1 << 18, 1 << 19, 1 << 20
        MODMASK = SHIFT | CTRL | OPT | CMD
        mods, keychar, keycode = 0, None, None
        for part in combo.split("+"):
            part = part.strip()
            if part in ("ctrl", "control"):
                mods |= CTRL
            elif part == "shift":
                mods |= SHIFT
            elif part in ("alt", "option", "opt"):
                mods |= OPT
            elif part in ("cmd", "command", "super", "win", "meta"):
                mods |= CMD
            elif part == "space":
                keycode = 49
            elif part:
                keychar = part

        def _matches(ev) -> bool:
            try:
                if (int(ev.modifierFlags()) & MODMASK) != mods:
                    return False
                if keycode is not None:
                    return ev.keyCode() == keycode
                if keychar is not None:
                    return (ev.charactersIgnoringModifiers() or "").lower() == keychar
            except Exception:
                pass
            return False

        def _on_global(ev):
            if _matches(ev):
                self._bridge.summon.emit()

        def _on_local(ev):
            if _matches(ev):
                self._bridge.summon.emit()
            return ev

        KEYDOWN = 1 << 10  # NSEventMaskKeyDown
        try:
            self._ns_hotkey_global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEYDOWN, _on_global)
            self._ns_hotkey_local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(KEYDOWN, _on_local)
        except Exception as e:
            print(f"[hotkey] NSEvent monitor failed: {e}")
            return False
        self._hotkey_combo = combo
        self._hotkey_status = f"on ({combo})"
        return True

    def _install_hotkey(self):
        """Register the global summon hotkey. Tries pynput first (more reliable on Windows
        without admin), then falls back to `keyboard`. Stores status for the UI to display."""
        self._uninstall_hotkey()
        combo = (self.settings.get("hotkey") or "ctrl+shift+space").lower().strip()
        errors = []

        # macOS needs Accessibility for any global key monitoring. Don't even try until it's
        # granted (the first-run _prime_permissions primer requests it; re-run once granted).
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
            # Use a main-thread NSEvent monitor, NEVER pynput, on macOS (see _install_hotkey_mac:
            # pynput's background event tap crashes on input-source/CapsLock events).
            if not self._install_hotkey_mac(combo):
                self._hotkey_combo = None
                self._hotkey_status = "off (hotkey unavailable)"
            return

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

    # --- Push-to-talk (hold a key to talk; zero-latency, no wake word) -------------------
    def _ensure_ptt(self):
        """Build the PushToTalk coordinator once, wiring its side effects to a HoldRecorder,
        the STT engine, and the chat-submit path (delivered to the UI thread via signals)."""
        if self._ptt is not None:
            return self._ptt
        import push_to_talk
        self._ptt = push_to_talk.PushToTalk(
            start_record=self._ptt_start_record,
            stop_record=self._ptt_stop_record,
            transcribe=self._ptt_transcribe,
            on_text=lambda t: self._bridge.ptt_text.emit(t),
            on_state=lambda s: self._bridge.ptt_state.emit(s),
            on_error=lambda e: self._bridge.ptt_error.emit(e),
        )
        return self._ptt

    def _ptt_start_record(self):
        import voice
        # Take the mic from the wake-word loop for the duration of the hold.
        try:
            import wake_word
            wake_word.pause()
        except Exception:
            pass
        rec = voice.HoldRecorder(sample_rate=16000)
        if not rec.start():
            raise RuntimeError(rec.error or "microphone unavailable")
        self._ptt_recorder = rec

    def _ptt_stop_record(self):
        rec = self._ptt_recorder
        self._ptt_recorder = None
        return rec.stop() if rec is not None else None

    def _ptt_transcribe(self, wav_path):
        import stt
        key = (self.settings.get("gemini_api_key") or self.settings.get("gemini_api_key_secondary")
               or self.settings.get("gemini_api_key_3") or self.settings.get("gemini_api_key_4") or "")
        offline = False
        try:
            import offline as _off
            offline = _off.is_offline()
        except Exception:
            pass
        res = stt.transcribe_audio(
            wav_path, prefer=self.settings.get("stt_engine", "auto"),
            gemini_key=key, offline=offline,
            whisper_model=self.settings.get("whisper_model", "base"))
        if not res.get("ok") and res.get("error"):
            self._bridge.ptt_error.emit(res["error"])
        return res.get("text", "")

    def _on_ptt_press(self):
        try:
            self._ensure_ptt().press()
        except Exception as e:
            self._set_status(f"Push-to-talk: {e}")

    def _on_ptt_release(self):
        if self._ptt is not None:
            self._ptt.release()

    def _on_ptt_text(self, text: str):
        text = (text or "").strip()
        if text:
            self._submit_user_text(text, meta="🎙️ push-to-talk")

    def _on_ptt_state(self, state: str):
        import push_to_talk as pt
        if state == pt.RECORDING:
            self._set_siri("listening")
            self._set_status("Push-to-talk: listening…")
        elif state == pt.TRANSCRIBING:
            self._set_siri("thinking")
            self._set_status("Push-to-talk: transcribing…")
        else:  # idle
            # Only dim the glow / hand the mic back if no other voice mode owns it.
            if not self._voice_chat_enabled and not self._listening:
                self._set_siri(None)
                try:
                    import wake_word
                    wake_word.resume()
                except Exception:
                    pass

    def _on_ptt_error(self, msg: str):
        self._set_status(f"Push-to-talk: {msg}")

    def _install_ptt(self):
        """Register the push-to-talk key listener (key down → record, key up → transcribe).
        No-op (and uninstalls) when push-to-talk is disabled. macOS uses NSEvent monitors;
        elsewhere pynput. Stores a status string for the UI."""
        self._uninstall_ptt()
        if not bool(self.settings.get("push_to_talk", False)):
            self._ptt_status = "off"
            return
        key = (self.settings.get("push_to_talk_key") or "f9").lower().strip()
        if sys.platform == "darwin":
            if not self._install_ptt_mac(key):
                self._ptt_status = "off (grant Accessibility to enable push-to-talk)"
            return
        try:
            from pynput import keyboard as pk
            import push_to_talk
            target = push_to_talk.pynput_key(key)
            if target is None:
                self._ptt_status = f"off (unknown key '{key}')"
                return

            def _matches(k):
                try:
                    if isinstance(target, pk.Key):
                        return k == target
                    kc = getattr(k, "char", None)
                    return kc is not None and kc == getattr(target, "char", None)
                except Exception:
                    return False

            def _on_press(k):
                if _matches(k):
                    self._bridge.ptt_press.emit()

            def _on_release(k):
                if _matches(k):
                    self._bridge.ptt_release.emit()

            self._ptt_listener = pk.Listener(on_press=_on_press, on_release=_on_release)
            self._ptt_listener.start()
            self._ptt_status = f"on ({key})"
        except Exception as e:
            self._ptt_status = f"off ({e})"

    def _install_ptt_mac(self, key: str) -> bool:
        """macOS push-to-talk via NSEvent key-down + key-up monitors (pynput's background tap
        crashes on macOS). Matches a single key by virtual key code."""
        try:
            from AppKit import NSEvent
            import mac_permissions
            if not mac_permissions.request_accessibility(prompt=False):
                return False
        except Exception:
            pass
        try:
            from AppKit import NSEvent
            import push_to_talk
        except Exception as e:
            print(f"[ptt] AppKit/push_to_talk unavailable: {e}")
            return False
        keycode = push_to_talk.mac_keycode(key)
        if keycode is None:
            self._ptt_status = f"off (unsupported macOS key '{key}')"
            return False
        self._ptt_held = False  # NSEvent KeyDown auto-repeats while held; emit press only once

        def _on_down(ev):
            try:
                if ev.keyCode() == keycode and not self._ptt_held:
                    self._ptt_held = True
                    self._bridge.ptt_press.emit()
            except Exception:
                pass

        def _on_up(ev):
            try:
                if ev.keyCode() == keycode and self._ptt_held:
                    self._ptt_held = False
                    self._bridge.ptt_release.emit()
            except Exception:
                pass

        def _on_down_local(ev):
            _on_down(ev)
            return ev

        def _on_up_local(ev):
            _on_up(ev)
            return ev

        KEYDOWN, KEYUP = 1 << 10, 1 << 11
        try:
            self._ns_ptt_global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEYDOWN, _on_down)
            self._ns_ptt_local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(KEYDOWN, _on_down_local)
            self._ns_ptt_up_global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(KEYUP, _on_up)
            self._ns_ptt_up_local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(KEYUP, _on_up_local)
        except Exception as e:
            print(f"[ptt] NSEvent monitor failed: {e}")
            return False
        self._ptt_status = f"on ({key})"
        return True

    def _uninstall_ptt(self):
        listener = getattr(self, "_ptt_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
            self._ptt_listener = None
        for attr in ("_ns_ptt_global", "_ns_ptt_local", "_ns_ptt_up_global", "_ns_ptt_up_local"):
            m = getattr(self, attr, None)
            if m is not None:
                try:
                    from AppKit import NSEvent
                    NSEvent.removeMonitor_(m)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._ptt_status = "off"

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

    def _attach_paths(self, paths, intro: str = "I'm attaching these files for you to read / discuss:"):
        """Add file/photo paths to the next message. Shared by the upload dialog, drag-drop
        AND Cmd/Ctrl+V paste so all three behave identically."""
        paths = [p for p in (paths or []) if p]
        if not paths:
            return
        listing = "\n".join(f"- {p}" for p in paths[:20])
        existing = self.input_box.toPlainText().strip()
        prefix = (existing + "\n\n") if existing else ""
        self.input_box.setPlainText(f"{prefix}{intro}\n{listing}\n\n")
        cursor = self.input_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input_box.setTextCursor(cursor)
        self.input_box.setFocus()
        self._set_status(f"{len(paths)} file(s) attached")

    def _open_upload_dialog(self):
        """File picker - the chosen paths get attached to the user's next message
        the same way drag-and-drop and paste do."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Upload files for Ember",
            str(Path.home()),
            "All files (*);;Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;"
            "Documents (*.pdf *.docx *.txt *.md *.csv *.json);;Spreadsheets (*.xlsx *.csv)",
        )
        self._attach_paths(paths, "I'm uploading these files for you to read / discuss:")

    def _toggle_mic(self):
        if self._voice_chat_enabled:
            self._stop_voice_chat("Voice chat off")
            return
        if self._listening:
            return
        self._start_voice_listen(mode="dictation")

    def _toggle_voice_chat(self):
        # A running Live (natural) voice session toggles off here too.
        if getattr(self, "_live_voice", None) is not None and self._live_voice.is_running():
            self._stop_live_voice("Voice chat off")
            return
        if self._voice_chat_enabled:
            self._stop_voice_chat("Voice chat off")
            return
        if not self.agent:
            QMessageBox.warning(self, "No API key", "Open settings (gear) and add your Gemini API key first.")
            self._open_settings()
            return
        # Natural voice (Gemini Live API) — real-time, full-duplex — when enabled + available.
        if self.settings.get("live_voice_enabled") and (self.settings.get("gemini_api_key") or "").strip():
            try:
                import live_voice
                if live_voice.available():
                    self._start_live_voice()
                    return
                self._add_bubble("system",
                    "Natural voice needs the google-genai SDK plus a microphone backend "
                    "(sounddevice, installed by default; PyAudio also works) — "
                    "falling back to standard voice chat.")
            except Exception:
                pass
        try:
            import voice
        except ImportError:
            QMessageBox.warning(self, "Voice not available",
                "Voice deps missing. Run: pip install SpeechRecognition sounddevice pyttsx3")
            return
        # Fail loudly + helpfully instead of silently looping "no speech" forever when the
        # mic can't be opened (the #1 cause: macOS microphone permission not granted).
        ok, detail = voice.mic_available()
        if not ok:
            QMessageBox.warning(self, "Microphone unavailable", detail)
            self._set_status("Mic unavailable")
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
        # Hands back the mic to the wake word and dims the glow.
        self._set_siri(None)
        try:
            import wake_word
            wake_word.resume()
        except Exception:
            pass

    # --- Natural voice (Gemini Live API) --------------------------------------
    def _live_voice_system_instruction(self) -> str:
        return ("You are Ember, a friendly voice assistant that can ACTUALLY control this "
                "computer using the provided tools — take screenshots and read the screen, open "
                "apps and websites, manage files, search the web, set timers, control volume/"
                "media, check email, and more. When the user asks you to DO something, CALL the "
                "relevant tool to really do it — don't just say you will. Acknowledge briefly in "
                "one short spoken line, run the tool, then say what happened. Keep replies short, "
                "natural and conversational.")

    def _start_live_voice(self):
        import live_voice
        try:
            import wake_word
            wake_word.pause()   # the Live session owns the mic continuously
        except Exception:
            pass
        self._lv_user_buf = ""
        self._lv_ember_buf = ""
        b = self._bridge
        # Give the voice model Ember's tools so it can ACT, not just talk (the thing GPT Live
        # can't do). Curated to a voice-appropriate set; high-risk tools stay gated by the same
        # MCP setting unless the user opts in.
        lv_tools, lv_exec = [], None
        try:
            import agent as _ag
            lv_tools = live_voice.curate_voice_tools(_ag.TOOL_DECLARATIONS)
            lv_exec = live_voice.default_tool_executor(
                allow_high_risk=bool(self.settings.get("mcp_bridge_allow_high_risk", False)))
        except Exception:
            lv_tools, lv_exec = [], None
        self._live_voice = live_voice.LiveVoice(
            (self.settings.get("gemini_api_key") or ""),
            model=self.settings.get("live_voice_model") or live_voice.DEFAULT_MODEL,
            voice=self.settings.get("live_voice_voice") or live_voice.DEFAULT_VOICE,
            api_version=self.settings.get("live_voice_api_version") or live_voice.DEFAULT_API_VERSION,
            system_instruction=self._live_voice_system_instruction(),
            on_user_text=lambda t: b.live_voice_event.emit("user", t),
            on_ember_text=lambda t: b.live_voice_event.emit("ember", t),
            on_state=lambda s: b.live_voice_event.emit("state", s),
            on_turn_complete=lambda: b.live_voice_event.emit("turn", ""),
            on_interrupted=lambda: b.live_voice_event.emit("state", "listening"),
            on_error=lambda e: b.live_voice_event.emit("error", e),
            on_tool=lambda name: b.live_voice_event.emit("tool", name),
            tools=lv_tools,
            tool_executor=lv_exec,
        )
        r = self._live_voice.start()
        if not r.get("ok"):
            self._add_bubble("system", "Natural voice: " + r.get("error", "could not start"))
            self._live_voice = None
            try:
                import wake_word
                wake_word.resume()
            except Exception:
                pass
            return
        orb = self._ensure_orb()
        if orb:
            orb.popup("listening")
            orb.set_caption("Natural voice on — just talk.")
        self._set_siri("listening")
        self._add_bubble("system",
            "🎙️ Natural voice (Gemini Live) is on — just talk, no button presses. "
            "Click Voice Chat again (or say “stop voice chat”) to end.")
        self._set_status("Natural voice listening…")
        self._update_live_voice_btn(True)

    def _stop_live_voice(self, status: str = "Voice chat off"):
        lv = getattr(self, "_live_voice", None)
        self._live_voice = None
        if lv is not None:
            try:
                lv.stop()
            except Exception:
                pass
        # Flush any partial Ember reply so it isn't lost.
        if getattr(self, "_lv_ember_buf", "").strip():
            self._add_bubble("assistant", self._lv_ember_buf.strip())
            self._append_history("assistant", self._lv_ember_buf.strip())
        self._lv_user_buf = ""
        self._lv_ember_buf = ""
        self._set_siri(None)
        orb = self._ensure_orb()
        if orb:
            orb.dismiss_after(1200)
        try:
            import wake_word
            wake_word.resume()
        except Exception:
            pass
        self._set_status(status)
        self._update_live_voice_btn(False)

    def _update_live_voice_btn(self, on: bool):
        btn = getattr(self, "voice_chat_btn", None)
        if btn is None:
            return
        btn.setText("Voice On" if on else "Voice Chat")
        btn.setObjectName("voiceToggleOn" if on else "voiceToggle")
        try:
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        except Exception:
            pass

    def _on_live_voice_event(self, kind: str, text: str):
        """Marshal Live API callbacks (fired on the asyncio thread) onto the UI thread."""
        if kind == "user":
            self._lv_user_buf += text
            low = self._lv_user_buf.lower()
            if "stop voice chat" in low or "stop the voice chat" in low:
                self._stop_live_voice("Voice chat off")
                return
            self._set_status("🎙️ " + self._lv_user_buf.strip()[-60:])
        elif kind == "ember":
            # The user's turn is done once Ember starts replying — commit it to the chat.
            if self._lv_user_buf.strip():
                u = self._lv_user_buf.strip()
                self._lv_user_buf = ""
                self._add_bubble("user", u)
                self._append_history("user", u)
            self._lv_ember_buf += text
            orb = self._ensure_orb()
            if orb:
                orb.set_state("speaking")
                orb.set_caption(self._lv_ember_buf.strip()[-200:])
        elif kind == "turn":
            if self._lv_ember_buf.strip():
                e = self._lv_ember_buf.strip()
                self._lv_ember_buf = ""
                self._add_bubble("assistant", e)
                self._append_history("assistant", e)
            self._set_status("Natural voice listening…")
        elif kind == "state":
            if text == "idle":
                if getattr(self, "_live_voice", None) is not None:
                    self._stop_live_voice("Natural voice ended")
            elif text in ("listening", "thinking", "speaking"):
                self._set_siri(text)
                orb = self._ensure_orb()
                if orb and text in ("listening", "speaking"):
                    orb.set_state(text)
        elif kind == "tool":
            # The voice model is operating the computer — show what it's doing.
            self._set_status(f"🔧 {text}…")
            orb = self._ensure_orb()
            if orb:
                orb.set_caption(f"running {text}…")
        elif kind == "error":
            self._set_status("Natural voice: " + (text or "")[:80])

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
                "Voice deps missing. Run: pip install SpeechRecognition sounddevice pyttsx3")
            return
        if mode == "voice_chat" and not self._voice_chat_enabled:
            return
        self._listening = True
        self._listening_mode = mode
        # The mic is ours now — pause the wake-word loop so it doesn't fight for it,
        # and light up the listening glow.
        try:
            import wake_word
            wake_word.pause()
        except Exception:
            pass
        self._set_siri("listening")
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
            raw = self.settings.get("voice_chat_phrase_timeout", "auto")
            if str(raw).strip().lower() in ("auto", "", "0", "none"):
                phrase_timeout = None      # Auto: end the turn when the user pauses (silence)
            else:
                try:
                    phrase_timeout = float(raw)
                except Exception:
                    phrase_timeout = None
        # When Auto, wait ~10s for speech to START; the silence tail ends the turn after that.
        listen_timeout = 10.0 if phrase_timeout is None else max(8.0, phrase_timeout + 2.0)
        started = False
        try:
            import audio_level
            # Metered capture publishes the live mic level so the glow/orb pulse with your voice.
            started = audio_level.listen_metered(_cb, phrase_timeout=phrase_timeout,
                                                 listen_timeout=listen_timeout)
        except Exception:
            started = False
        if not started:
            voice.listen_once(_cb, phrase_timeout=phrase_timeout, listen_timeout=listen_timeout)

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
        if mode == "orb":
            self._handle_orb_transcript(text, err)
            return
        # Dictation is one-shot: hand the mic back to the wake word and dim the glow.
        try:
            import wake_word
            wake_word.resume()
        except Exception:
            pass
        self._set_siri(None)
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
        text = _fix_assistant_name((text or "").strip())   # "amber" -> "Ember"
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
                alpha = 72
            glow = QGraphicsDropShadowEffect()
            glow.setBlurRadius(34 if self.settings.get("liquid_glass", False) else 22)
            glow.setColor(QColor(col.red(), col.green(), col.blue(), alpha))
            glow.setOffset(0, 0)
            root.setGraphicsEffect(glow)
        else:
            root.setGraphicsEffect(None)

    def _theme_overrides(self) -> str:
        """Extra QSS appended to the base theme so the user's accent colour AND chat text
        size actually take effect (the base STYLE was static, which is why both 'did nothing').
        QSS later-rules win, so these override the base."""
        accent = self.settings.get("accent_color", "#7aa2f7") or "#7aa2f7"
        try:
            fs = max(10, min(22, int(self.settings.get("font_size", 12))))
        except Exception:
            fs = 12
        a = accent
        a_soft = self._accent_rgba(accent, 0.18)   # translucent accent for hovers/fills
        a_mid = self._accent_rgba(accent, 0.30)
        return f"""
/* One accent, applied consistently across the whole UI so it reads as designed, not default-Qt. */
QPushButton#send {{ background-color: {a}; border-color: {a}; color: #ffffff; }}
QPushButton#send:hover {{ background-color: {a}; border-color: {a}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {a}; }}
QComboBox:focus, QComboBox:hover {{ border-color: {a}; }}
QComboBox QAbstractItemView {{ selection-background-color: {a}; }}
QPushButton#chip:hover {{ border-color: {a}; background-color: {a_soft}; color: #ffffff; }}
QListWidget::item:selected {{ background-color: {a}; color: #ffffff; }}
QListWidget#historyList::item:selected {{ background-color: {a_soft}; color: #ffffff; }}
QListWidget#historyList::item:hover {{ background-color: {a_soft}; }}
/* selected settings tab + hover get the accent underline/tint */
QTabBar::tab:selected {{ color: #ffffff; border-bottom: 2px solid {a}; }}
QTabBar::tab:hover {{ color: {a}; }}
/* checked checkbox uses the accent so on/off is unmistakable */
QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}
QMenu::item:selected {{ background-color: {a_mid}; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {a_mid}; }}
QLabel#bubbleBody {{ font-size: {fs}px; }}
"""

    @staticmethod
    def _accent_rgba(hex_color: str, alpha: float) -> str:
        """Turn an #rrggbb accent into an rgba(...) string at the given alpha (for soft fills)."""
        try:
            h = (hex_color or "#7aa2f7").lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r}, {g}, {b}, {alpha:.2f})"
        except Exception:
            return f"rgba(122, 162, 247, {alpha:.2f})"

    def _apply_window_theme(self):
        """Set the main window stylesheet = base theme (glass or flat) + accent/font overrides.
        Central place so accent + chat text size always apply, glass on or off."""
        try:
            self.setStyleSheet(STYLE + WORKSPACE_POLISH + self._theme_overrides())
        except Exception:
            self.setStyleSheet(STYLE)

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
        # Apply the glass stylesheet whenever Liquid Glass is on. _glass_style keeps the
        # window near-opaque when there's no real blur behind it (so the desktop doesn't show
        # through and make it unreadable — that was the see-through bug), and thins the veil
        # when a blur IS mounted (Windows acrylic / opt-in EMBER_NATIVE_BLUR).
        if enabled:
            base = _glass_style(200, self.settings.get("accent_color", "#58a6ff"),
                                see_through=blur_level, blurred=blurred)
        else:
            base = STYLE
        self.setStyleSheet(base + WORKSPACE_POLISH + self._theme_overrides())
        self.update()

    def _toggle_max(self):
        """Cycle the window size via the title-bar button:
        normal -> full screen -> compact chat (a website-style chat widget) -> normal."""
        order = ["normal", "full", "chatbot"]
        cur = getattr(self, "_size_mode", "normal")
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "full"
        # Remember the user's hand-sized geometry the first time we leave 'normal'.
        if cur == "normal":
            self._pre_max_geometry = self.geometry()
        self._apply_size_mode(nxt)

    def _apply_size_mode(self, mode: str):
        screen = QApplication.primaryScreen().availableGeometry()
        compact = (mode == "chatbot")
        # Relax the minimum width for the narrow chat widget; restore it for the full layout
        # (the 3-column layout needs the room).
        self.setMinimumSize(320, 480) if compact else self.setMinimumSize(780, 600)
        # The side panels don't fit a narrow chat-widget width, so hide them in compact mode.
        sidebar = getattr(self, "_sidebar", None)
        if sidebar is not None:
            sidebar.setVisible(not compact)
        command_panel = getattr(self, "_command_panel", None)
        if command_panel is not None:
            command_panel.setVisible(not compact and bool(getattr(self, "_tools_open", False)))
        for w in (getattr(self, "workspace_info", None), getattr(self, "mode_widget", None),
                  getattr(self, "composer_hint", None), getattr(self, "quick_chips", None),
                  getattr(self, "status_label", None), getattr(self, "reset_btn", None),
                  getattr(self, "settings_btn", None), getattr(self, "min_btn", None),
                  getattr(self, "empty_hint", None)):
            if w is not None:
                w.setVisible(not compact)
        if mode == "full":
            self.setGeometry(screen)
            self.max_btn.setText("❐")
            self.max_btn.setToolTip("Window: full screen — click for compact chat")
        elif mode == "chatbot":
            w, margin = 340, 24
            h = min(640, screen.height() - 2 * margin)
            self.setGeometry(screen.right() - w - margin, screen.bottom() - h - margin, w, h)
            self.max_btn.setText("▭")
            self.max_btn.setToolTip("Window: compact chat — click to restore normal")
        else:  # normal
            g = getattr(self, "_pre_max_geometry", None)
            if g is not None:
                self.setGeometry(g)
            else:
                self.resize(1180, 800)
            self.max_btn.setText("□")
            self.max_btn.setToolTip("Window: normal — click for full screen")
        self._size_mode = mode
        # Re-clamp bubbles to the new chat width.
        QTimer.singleShot(0, self._clamp_bubble_widths)

    def _build_ui(self):
        # NOTE: removed Qt.WindowType.Tool - it was hiding Ember from the taskbar, which
        # blocked taskbar pinning. With just FramelessWindowHint + StaysOnTop, the window
        # shows up in the taskbar like a normal app and can be pinned.
        # Stay-on-top is OFF by default — otherwise Ember floats above every app and the
        # re-raise timer steals focus/clicks when you switch windows. Opt in with the
        # "always_on_top" setting.
        flags = Qt.WindowType.FramelessWindowHint
        if bool(self.settings.get("always_on_top", False)):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1180, 800)
        self.setMinimumSize(780, 600)
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
        root_row.setContentsMargins(10, 10, 10, 10)
        root_row.setSpacing(10)

        sidebar = QFrame()
        sidebar.setObjectName("historyPanel")
        sidebar.setFixedWidth(248)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 11, 10, 10)
        side_layout.setSpacing(9)

        brand = QFrame()
        brand.setObjectName("brandRow")
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(1, 0, 1, 5)
        brand_row.setSpacing(9)
        mark = DiamondMark(34)   # Ember's diamond brand logo (resizable vector), was the letter "E"
        brand_row.addWidget(mark)
        brand_copy = QVBoxLayout()
        brand_copy.setContentsMargins(0, 0, 0, 0)
        brand_copy.setSpacing(0)
        brand_name = QLabel("Ember")
        brand_name.setObjectName("brandName")
        brand_copy.addWidget(brand_name)
        brand_row.addLayout(brand_copy, 1)
        side_layout.addWidget(brand)

        self.new_chat_btn = QPushButton("＋  New task")
        self.new_chat_btn.setObjectName("newTask")
        self.new_chat_btn.clicked.connect(self._new_chat)
        side_layout.addWidget(self.new_chat_btn)

        self.history_search = QLineEdit()
        self.history_search.setObjectName("historySearch")
        self.history_search.setPlaceholderText("Search tasks…")
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self._filter_history)
        side_layout.addWidget(self.history_search)

        side_title = QLabel("RECENT")
        side_title.setObjectName("sideHint")
        side_layout.addWidget(side_title)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.currentItemChanged.connect(self._on_history_selected)
        side_layout.addWidget(self.history_list, 1)

        self.history_hint = QLabel("Task history stays on this device.")
        self.history_hint.setObjectName("sideHint")
        self.history_hint.setWordWrap(True)
        side_layout.addWidget(self.history_hint)
        self._sidebar = sidebar
        root_row.addWidget(sidebar)

        main_panel = QWidget()
        main_panel.setObjectName("mainPanel")
        layout = QVBoxLayout(main_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # One conversation-first workspace. Local/cloud models and capability modes all operate
        # inside this same view; there is deliberately no competing Work/Chat tab.
        center = QWidget()
        center_col = QVBoxLayout(center)
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(0)
        center_col.addWidget(main_panel, 1)
        root_row.addWidget(center, 1)

        # Agent behavior remains a lightweight control in the unified header.
        self.mode_widget = QWidget()
        mode_box = QHBoxLayout(self.mode_widget)
        mode_box.setContentsMargins(0, 0, 0, 0)
        mode_box.setSpacing(0)
        self.main_mode_combo = QComboBox()
        self.main_mode_combo.setObjectName("modePicker")
        self.main_mode_combo.addItem("Autopilot", "auto")
        self.main_mode_combo.addItem("Plan first", "plan")
        self.main_mode_combo.addItem("Conversation", "chat")
        self.main_mode_combo.addItem("Read only", "read_only")
        try:
            import agents as _ag
            current_mode = _ag.get_run_mode()
            for i in range(self.main_mode_combo.count()):
                if self.main_mode_combo.itemData(i) == current_mode:
                    self.main_mode_combo.setCurrentIndex(i)
                    break
        except Exception:
            pass
        self.main_mode_combo.currentIndexChanged.connect(self._on_main_mode_changed)
        mode_box.addWidget(self.main_mode_combo)

        command_panel = QFrame()
        command_panel.setObjectName("commandPanel")
        command_panel.setFixedWidth(278)
        command_layout = QVBoxLayout(command_panel)
        command_layout.setContentsMargins(10, 10, 10, 10)
        command_layout.setSpacing(7)

        command_title = QLabel("TOOLS & CONTROLS")
        command_title.setObjectName("sectionTitle")
        command_layout.addWidget(command_title)

        palette_btn = QPushButton("⌕  Find anything                         ⌘K")
        palette_btn.setObjectName("commandPalette")
        palette_btn.setToolTip("Search every Ember capability (Ctrl/Cmd+K)")
        palette_btn.clicked.connect(lambda: self._run_slash("__features__"))
        command_layout.addWidget(palette_btn)

        self.voice_chat_btn = QPushButton("Voice Chat")
        self.voice_chat_btn.setObjectName("voiceToggle")
        self.voice_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.voice_chat_btn.setToolTip("Toggle hands-free voice conversation")
        self.voice_chat_btn.clicked.connect(self._toggle_voice_chat)
        command_layout.addWidget(self.voice_chat_btn)

        self.voice_status_label = QLabel("Voice idle")
        self.voice_status_label.setObjectName("panelHint")
        command_layout.addWidget(self.voice_status_label)

        pointer_card = QFrame()
        pointer_card.setObjectName("pointerCard")
        pointer_layout = QVBoxLayout(pointer_card)
        pointer_layout.setContentsMargins(10, 8, 10, 9)
        pointer_layout.setSpacing(5)
        pointer_title = QLabel("POINTER CONTROL")
        pointer_title.setObjectName("controlTitle")
        pointer_layout.addWidget(pointer_title)
        try:
            import human_mouse
            pointer_opts = human_mouse.get_options()
        except Exception:
            pointer_opts = {"enabled": True, "speed": 1.0}
        self.main_mouse_toggle = QCheckBox("Human motion")
        self.main_mouse_toggle.setObjectName("pointerToggle")
        self.main_mouse_toggle.setChecked(bool(self.settings.get(
            "mouse_humanize", pointer_opts.get("enabled", True))))
        self.main_mouse_toggle.setToolTip("Curved, eased pointer paths that move like a careful person")
        self.main_mouse_toggle.toggled.connect(self._on_main_mouse_toggled)
        pointer_layout.addWidget(self.main_mouse_toggle)
        self.main_ember_pointer_toggle = QCheckBox("Show Ember pointer")
        self.main_ember_pointer_toggle.setObjectName("pointerToggle")
        self.main_ember_pointer_toggle.setChecked(bool(
            self.settings.get("show_ember_pointer", True)))
        self.main_ember_pointer_toggle.setToolTip(
            "Show a separate, click-through Ember marker while the agent moves and clicks")
        self.main_ember_pointer_toggle.toggled.connect(self._on_main_ember_pointer_toggled)
        pointer_layout.addWidget(self.main_ember_pointer_toggle)
        pointer_speed_row = QHBoxLayout()
        pointer_speed_row.setContentsMargins(0, 0, 0, 0)
        self.main_mouse_speed = QSlider(Qt.Orientation.Horizontal)
        self.main_mouse_speed.setObjectName("pointerSpeed")
        self.main_mouse_speed.setRange(25, 300)
        _speed = float(self.settings.get("mouse_speed", pointer_opts.get("speed", 1.0)))
        self.main_mouse_speed.setValue(int(round(max(0.25, min(3.0, _speed)) * 100)))
        self.main_mouse_speed.setToolTip("Pointer travel speed")
        self.main_mouse_speed.valueChanged.connect(self._on_main_mouse_speed_changed)
        self.main_mouse_speed.sliderReleased.connect(self._persist_main_mouse_speed)
        self.main_mouse_speed_value = QLabel(f"{self.main_mouse_speed.value() / 100:.2f}×")
        self.main_mouse_speed_value.setObjectName("speedValue")
        pointer_speed_row.addWidget(self.main_mouse_speed, 1)
        pointer_speed_row.addWidget(self.main_mouse_speed_value)
        pointer_layout.addLayout(pointer_speed_row)
        command_layout.addWidget(pointer_card)

        status_strip = QFrame()
        status_strip.setObjectName("statusStrip")
        status_layout = QVBoxLayout(status_strip)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(3)
        live_row = QHBoxLayout()
        live_row.setContentsMargins(0, 0, 0, 2)
        live_dot = QLabel("●")
        live_dot.setObjectName("liveDot")
        live_label = QLabel("STATUS")
        live_label.setObjectName("liveLabel")
        live_row.addWidget(live_dot)
        live_row.addWidget(live_label)
        live_row.addStretch()
        status_layout.addLayout(live_row)
        self.capability_metric = QLabel("Screen · pointer · keyboard available")
        self.capability_metric.setObjectName("statusMetric")
        self.model_metric = QLabel("Model warming up")
        self.model_metric.setObjectName("statusMetric")
        self.tool_metric = QLabel("Risky actions require approval")
        self.tool_metric.setObjectName("statusMetric")
        status_layout.addWidget(self.capability_metric)
        status_layout.addWidget(self.model_metric)
        status_layout.addWidget(self.tool_metric)
        command_layout.addWidget(status_strip)

        # Scrollable actions area so the panel can offer many apps/tools without overflowing
        # the fixed-width column on smaller screens.
        actions_scroll = QScrollArea()
        actions_scroll.setWidgetResizable(True)
        actions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        actions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        actions_inner = QWidget()
        actions_layout = QVBoxLayout(actions_inner)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(7)

        action_title = QLabel("LAUNCHERS & QUICK TASKS")
        action_title.setObjectName("sectionTitle")
        actions_layout.addWidget(action_title)

        for section_title, items in COMMAND_CENTER_GROUPS:
            sub = QLabel(section_title)
            sub.setObjectName("panelHint")
            actions_layout.addWidget(sub)
            for label, cmd, tip in items:
                b = QPushButton(label)
                is_feature = cmd.startswith("__")
                # Features OPEN something (solid button); quick tasks TYPE a request (outlined).
                b.setObjectName("commandAction" if is_feature else "commandTask")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                if not tip and not is_feature:
                    sent = SLASH_COMMANDS.get(cmd, cmd)
                    if isinstance(sent, str) and not sent.startswith("__"):
                        tip = "Sends this request to Ember:\n" + (sent[:160] + ("…" if len(sent) > 160 else ""))
                if tip:
                    b.setToolTip(tip)
                b.clicked.connect(lambda _=False, c=cmd: self._run_slash(c))
                actions_layout.addWidget(b)

        actions_layout.addStretch(1)
        actions_scroll.setWidget(actions_inner)
        command_layout.addWidget(actions_scroll, 1)
        self._command_panel = command_panel
        self._tools_open = False
        command_panel.hide()
        root_row.addWidget(command_panel)

        # Title bar
        title_row = QHBoxLayout()
        self.sidebar_btn = QPushButton("☰")
        self.sidebar_btn.setObjectName("titleBtn")
        self.sidebar_btn.setFixedSize(30, 30)
        self.sidebar_btn.setToolTip("Show or hide task history")
        self.sidebar_btn.clicked.connect(self._toggle_history_panel)
        title_row.addWidget(self.sidebar_btn)

        self.workspace_info = QWidget()
        workspace_copy = QVBoxLayout(self.workspace_info)
        workspace_copy.setContentsMargins(4, 0, 8, 0)
        workspace_copy.setSpacing(0)
        self.workspace_title = QLabel("New task")
        self.workspace_title.setObjectName("workspaceTitle")
        self.workspace_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusBar")
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        workspace_copy.addWidget(self.workspace_title)
        workspace_copy.addWidget(self.status_label)
        title_row.addWidget(self.workspace_info)
        title_row.addStretch()

        model_name = (self.settings.get("model_id") or self.settings.get("gemini_model")
                      or "Choose model")
        self.model_btn = QPushButton(str(model_name))
        self.model_btn.setObjectName("modelPickerBtn")
        self.model_btn.setMaximumWidth(180)
        self.model_btn.setToolTip("Change the model, including local Ollama")
        self.model_btn.clicked.connect(lambda: self._open_settings("Models"))
        title_row.addWidget(self.model_btn)

        title_row.addWidget(self.mode_widget)

        self.tools_btn = QPushButton("Tools")
        self.tools_btn.setObjectName("toolsBtn")
        self.tools_btn.setToolTip("Open tools and computer controls (Ctrl/Cmd+K searches all)")
        self.tools_btn.clicked.connect(self._toggle_tools_panel)
        title_row.addWidget(self.tools_btn)

        BTN = 30
        self.reset_btn = QPushButton("↺")
        self.reset_btn.setObjectName("titleBtn")
        self.reset_btn.setFixedSize(BTN, BTN)
        self.reset_btn.setToolTip("Reset this task")
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
        self.close_btn.setToolTip("Quit Ember (use — to minimize and keep “Hey Ember” running)")
        # X QUITS the app, as users expect. The — button minimizes to the corner pill if you
        # want Ember to keep running in the background for the wake word.
        self.close_btn.clicked.connect(self._do_quit)
        title_row.addWidget(self.close_btn)

        layout.addLayout(title_row)
        self.quick_chips = None

        # Chat area
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Block the chat scroll area from EVER being wider than its viewport - that's
        # what was causing the rogue horizontal scrollbar.
        self.chat_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chat_container = QWidget()
        self.chat_container.setObjectName("chatViewport")
        self.chat_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(24, 18, 24, 20)
        self.chat_layout.setSpacing(2)
        # Stretch at end keeps bubbles top-aligned when chat isn't full yet.
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_container)
        layout.addWidget(self.chat_scroll, 1)
        # "Stick to bottom": auto-follow new content (incl. streaming growth) UNLESS the user
        # has scrolled up. rangeChanged fires whenever the content height changes, so we always
        # catch the FINAL height (fixes "have to scroll to see the latest message" caused by
        # scrolling on a stale, pre-layout maximum). valueChanged tells us if the user left the
        # bottom (programmatic scroll-to-max keeps us stuck; dragging up un-sticks).
        self._chat_stick_bottom = True
        _csb = self.chat_scroll.verticalScrollBar()
        _csb.rangeChanged.connect(lambda _mn, _mx: self._chat_follow_bottom())
        _csb.valueChanged.connect(self._on_chat_scrolled)

        # Expandable run activity: collapsed by default, detailed enough to understand the
        # agent when opened. Tool arguments/results are sanitized before they reach this view.
        activity_shell = QWidget()
        activity_shell.setObjectName("activityShell")
        activity_shell_layout = QHBoxLayout(activity_shell)
        activity_shell_layout.setContentsMargins(24, 2, 24, 2)
        activity_shell_layout.addStretch(1)
        self.activity_card = QFrame()
        self.activity_card.setObjectName("taskActivity")
        self.activity_card.setMaximumWidth(860)
        self.activity_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        activity_layout = QVBoxLayout(self.activity_card)
        activity_layout.setContentsMargins(10, 6, 10, 8)
        activity_layout.setSpacing(5)
        activity_header = QHBoxLayout()
        activity_header.setContentsMargins(0, 0, 0, 0)
        self.activity_toggle = QPushButton("▸  Task activity")
        self.activity_toggle.setObjectName("activityToggle")
        self.activity_toggle.clicked.connect(self._toggle_activity_details)
        activity_header.addWidget(self.activity_toggle, 1)
        self.activity_progress = QProgressBar()
        self.activity_progress.setObjectName("activityProgress")
        self.activity_progress.setTextVisible(False)
        self.activity_progress.setFixedSize(74, 4)
        activity_header.addWidget(self.activity_progress)
        activity_layout.addLayout(activity_header)
        self.activity_details = QPlainTextEdit()
        self.activity_details.setObjectName("activityDetails")
        self.activity_details.setReadOnly(True)
        self.activity_details.setMaximumHeight(0)
        self.activity_details.hide()
        activity_layout.addWidget(self.activity_details)
        activity_shell_layout.addWidget(self.activity_card, 6)
        activity_shell_layout.addStretch(1)
        self.activity_card.hide()
        self._activity_entries = []
        self._activity_expanded = False
        self._activity_count = 0
        layout.addWidget(activity_shell)

        # Composer: one calm, unified surface instead of a text box plus a stack of floating
        # controls. ChatInput still accepts pasted/dropped files + images (Cmd/Ctrl+V).
        composer_shell = QWidget()
        composer_shell.setObjectName("composerShell")
        composer_shell_layout = QHBoxLayout(composer_shell)
        composer_shell_layout.setContentsMargins(24, 6, 24, 6)
        composer_shell_layout.setSpacing(0)
        composer_shell_layout.addStretch(1)
        composer = QFrame()
        composer.setObjectName("composer")
        composer.setMaximumWidth(860)
        composer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(6, 4, 6, 6)
        composer_layout.setSpacing(2)
        self.input_box = ChatInput(on_attach=self._attach_paths)
        self.input_box.setObjectName("composerInput")
        self.input_box.setMinimumHeight(46)
        self.input_box.setMaximumHeight(96)
        self.input_box.setPlaceholderText("Message Ember or describe a task…")
        self.input_box.installEventFilter(self)
        self.input_box.textChanged.connect(self._resize_composer)
        composer_layout.addWidget(self.input_box)

        composer_bar = QHBoxLayout()
        composer_bar.setContentsMargins(4, 0, 2, 0)
        composer_bar.setSpacing(5)

        self.upload_btn = QPushButton("＋")
        self.upload_btn.setObjectName("composerTool")
        self.upload_btn.setFixedSize(34, 34)
        self.upload_btn.setToolTip("Upload files for Ember to read (images, PDFs, text, etc.)")
        self.upload_btn.clicked.connect(self._open_upload_dialog)
        composer_bar.addWidget(self.upload_btn)

        self.mic_btn = QPushButton("◉")
        self.mic_btn.setObjectName("composerTool")
        self.mic_btn.setFixedSize(34, 34)
        self.mic_btn.setToolTip("Click to dictate (auto-stops on silence)")
        self.mic_btn.clicked.connect(self._toggle_mic)
        composer_bar.addWidget(self.mic_btn)

        self.composer_hint = QLabel("Shift+Enter for a new line")
        self.composer_hint.setObjectName("composerHint")
        composer_bar.addWidget(self.composer_hint, 1)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setObjectName("stopAgent")
        self.stop_btn.setFixedSize(34, 34)
        self.stop_btn.setToolTip("Stop the current agent run")
        self.stop_btn.clicked.connect(self._on_stop)
        composer_bar.addWidget(self.stop_btn)

        self.send_btn = QPushButton("↑")
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(42, 38)
        self.send_btn.setToolTip("Run task")
        self.send_btn.clicked.connect(self._on_send)
        composer_bar.addWidget(self.send_btn)
        composer_layout.addLayout(composer_bar)
        composer_shell_layout.addWidget(composer, 6)
        composer_shell_layout.addStretch(1)
        layout.addWidget(composer_shell)

        self.setStyleSheet(STYLE + WORKSPACE_POLISH + self._theme_overrides())
        # Mark root widget so the stylesheet applies
        root.setProperty("class", "root")

        self._refresh_history_sidebar()
        self._load_active_chat_into_view()
        self._update_voice_chat_ui()
        self._install_workspace_shortcuts()
        QTimer.singleShot(0, self._resize_composer)

    def _install_workspace_shortcuts(self):
        """Fast paths for the two things people do most: find a capability and start fresh."""
        self._workspace_shortcuts = []
        for sequence, handler in (("Ctrl+K", self._open_features),
                                  ("Meta+K", self._open_features),
                                  ("Ctrl+N", self._new_chat),
                                  ("Meta+N", self._new_chat)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._workspace_shortcuts.append(shortcut)

    def _toggle_history_panel(self):
        sidebar = getattr(self, "_sidebar", None)
        if sidebar is None:
            return
        if getattr(self, "_size_mode", "normal") == "chatbot":
            self._apply_size_mode("normal")
            sidebar.show()
            return
        opening = not sidebar.isVisible()
        self._animate_drawer(sidebar, opening, 248)
        self.sidebar_btn.setToolTip("Hide task history" if opening else "Show task history")
        QTimer.singleShot(0, self._clamp_bubble_widths)

    def _motion_mode(self) -> str:
        if not bool(self.settings.get("animations_enabled", True)):
            return "off"
        return str(self.settings.get("motion_level", "dynamic") or "dynamic").lower()

    def _motion_duration(self, base: int = 220) -> int:
        return {"dynamic": int(base * 1.15), "smooth": base,
                "reduced": max(80, int(base * 0.45)), "off": 0}.get(
                    self._motion_mode(), base)

    def _keep_animation(self, animation) -> None:
        if not hasattr(self, "_anims"):
            self._anims = []
        self._anims.append(animation)
        self._anims = self._anims[-80:]

    def _start_ambient_pulse(self, widget) -> None:
        """Give focal marks a slow living glow without changing layout geometry."""
        if self._motion_mode() not in ("dynamic", "smooth"):
            return
        try:
            effect = QGraphicsDropShadowEffect(widget)
            effect.setColor(QColor(self.settings.get("accent_color", "#7aa2f7")))
            effect.setOffset(0, 0)
            effect.setBlurRadius(8)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"blurRadius", widget)
            animation.setDuration(self._motion_duration(1500))
            animation.setStartValue(8.0)
            animation.setKeyValueAt(0.5, 30.0 if self._motion_mode() == "dynamic" else 20.0)
            animation.setEndValue(8.0)
            animation.setLoopCount(-1)
            animation.setEasingCurve(QEasingCurve.Type.InOutSine)
            animation.start()
            self._keep_animation(animation)
        except Exception:
            pass

    def _animate_drawer(self, widget, opening: bool, width: int) -> None:
        """Animate a layout-owned side panel without graphics effects or ghost surfaces."""
        duration = self._motion_duration(230)
        if duration <= 0:
            widget.setFixedWidth(width)
            widget.setVisible(opening)
            return
        widget.setMinimumWidth(0)
        if opening:
            widget.setMaximumWidth(0)
            widget.show()
            start, end = 0, width
        else:
            start, end = max(1, widget.width()), 0
        animation = QPropertyAnimation(widget, b"maximumWidth", self)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(
            QEasingCurve.Type.OutBack if opening and self._motion_mode() == "dynamic"
            else QEasingCurve.Type.InOutCubic)

        def finish():
            if opening:
                widget.setMinimumWidth(width)
                widget.setMaximumWidth(width)
            else:
                widget.hide()
                widget.setMinimumWidth(width)
                widget.setMaximumWidth(width)
            QTimer.singleShot(0, self._clamp_bubble_widths)

        animation.finished.connect(finish)
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._keep_animation(animation)

    def _toggle_tools_panel(self):
        """Reveal secondary tools only on demand so the conversation remains the canvas."""
        panel = getattr(self, "_command_panel", None)
        if panel is None:
            return
        if getattr(self, "_size_mode", "normal") == "chatbot":
            self._apply_size_mode("normal")
        self._tools_open = not bool(getattr(self, "_tools_open", False))
        self._animate_drawer(panel, self._tools_open, 278)
        if hasattr(self, "tools_btn"):
            self.tools_btn.setText("Close tools" if self._tools_open else "Tools")
            self.tools_btn.setProperty("open", self._tools_open)
            self.tools_btn.style().unpolish(self.tools_btn)
            self.tools_btn.style().polish(self.tools_btn)
        QTimer.singleShot(0, self._clamp_bubble_widths)

    def _toggle_activity_details(self):
        details = getattr(self, "activity_details", None)
        if details is None:
            return
        opening = not bool(getattr(self, "_activity_expanded", False))
        self._activity_expanded = opening
        self.activity_toggle.setText(
            ("▾  " if opening else "▸  ") + self.activity_toggle.text()[3:])
        duration = self._motion_duration(190)
        if opening:
            details.show()
        if duration <= 0:
            details.setMaximumHeight(156 if opening else 0)
            details.setVisible(opening)
            return
        animation = QPropertyAnimation(details, b"maximumHeight", self)
        animation.setDuration(duration)
        animation.setStartValue(details.maximumHeight() if details.isVisible() else 0)
        animation.setEndValue(156 if opening else 0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if not opening:
            animation.finished.connect(details.hide)
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._keep_animation(animation)

    def _activity_reset(self):
        self._activity_entries = []
        self._activity_count = 0
        self._activity_expanded = False
        if hasattr(self, "activity_details"):
            self.activity_details.clear()
            self.activity_details.setMaximumHeight(0)
            self.activity_details.hide()
        if hasattr(self, "activity_card"):
            self.activity_card.hide()

    def _activity_refresh_text(self):
        if hasattr(self, "activity_details"):
            self.activity_details.setPlainText("\n\n".join(self._activity_entries[-80:]))
            cursor = self.activity_details.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.activity_details.setTextCursor(cursor)

    def _activity_tool_call(self, name: str, args: dict):
        try:
            import redaction
            safe_args = redaction.scrub_obj(args or {})
        except Exception:
            safe_args = args or {}
        try:
            args_text = json.dumps(safe_args, ensure_ascii=False, default=str, indent=2)
        except Exception:
            args_text = str(safe_args)
        if len(args_text) > 1800:
            args_text = args_text[:1800] + "\n…"
        self._activity_count += 1
        self._activity_entries.append(f"{self._activity_count}. RUNNING  {name}\n{args_text}")
        self.activity_toggle.setText(f"▸  Working · {name}")
        self.activity_progress.setRange(0, 0)
        self.activity_card.show()
        self._activity_refresh_text()

    def _activity_tool_result(self, name: str, result: dict, summary: str):
        state = "DONE" if result.get("ok", True) else "FAILED"
        self._activity_entries.append(f"   {state}  {name}\n{summary[:1200]}")
        self.activity_toggle.setText(f"▸  {state.title()} · {name}")
        self._activity_refresh_text()

    def _activity_complete(self):
        if not getattr(self, "_activity_count", 0):
            return
        self.activity_progress.setRange(0, 1)
        self.activity_progress.setValue(1)
        self.activity_toggle.setText(
            f"{'▾' if self._activity_expanded else '▸'}  Completed · "
            f"{self._activity_count} action{'s' if self._activity_count != 1 else ''}")

    def _filter_history(self, query: str):
        """Filter locally without mutating saved task history or losing the active selection."""
        needle = (query or "").strip().casefold()
        for i in range(self.history_list.count()):
            item = self.history_list.item(i)
            item.setHidden(bool(needle and needle not in item.text().casefold()))

    def _on_main_mode_changed(self, _index: int):
        mode = self.main_mode_combo.currentData() or "auto"
        try:
            import agents
            result = agents.set_run_mode(mode)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Couldn't change agent mode"))
            agent = getattr(self, "agent", None)
            if agent is not None:
                agent.run_mode = mode
            labels = {
                "auto": "Autopilot can operate the computer end-to-end",
                "plan": "Plan first · Ember waits before acting",
                "chat": "Chat only · computer controls are paused",
                "read_only": "Read only · inspect without changing anything",
            }
            self._set_status(labels.get(mode, "Agent mode updated"))
        except Exception as exc:
            QMessageBox.warning(self, "Agent mode", str(exc))

    def _on_main_mouse_toggled(self, enabled: bool):
        self.settings["mouse_humanize"] = bool(enabled)
        self._apply_mouse_options()
        save_settings(self.settings)
        self._set_status("Human pointer motion on" if enabled else "Direct pointer motion on")

    def _on_main_ember_pointer_toggled(self, enabled: bool):
        self.settings["show_ember_pointer"] = bool(enabled)
        self._apply_mouse_options()
        save_settings(self.settings)
        self._set_status("Ember pointer on" if enabled else "Ember pointer hidden")

    def _on_main_mouse_speed_changed(self, value: int):
        speed = max(0.25, min(3.0, value / 100.0))
        self.settings["mouse_speed"] = speed
        self.main_mouse_speed_value.setText(f"{speed:.2f}×")
        self._apply_mouse_options()

    def _persist_main_mouse_speed(self):
        save_settings(self.settings)
        self._set_status(f"Pointer speed · {self.settings.get('mouse_speed', 1.0):.2f}×")

    def _switch_main_view(self, which: str):
        """Compatibility shim: cloud and local models now share the unified conversation."""
        if which == "chat":
            self._open_settings("Models")

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

    def _history_turns_for_seeding(self, max_turns: int = 40, max_chars: int = 6000) -> list:
        """Normalized prior conversation to seed a freshly-built agent with (so switching the
        model/provider mid-chat keeps context). Returns [{"role","text"}] that is: user/assistant
        only, consecutive same-role turns merged, starts with a user turn and ends with an
        assistant turn (valid for every provider), capped to the most recent turns."""
        raw = (self._active_chat().get("messages") or [])
        merged: list[dict] = []
        for m in raw:
            role = m.get("role")
            text = (m.get("text") or "").strip()
            if role not in ("user", "assistant") or not text:
                continue   # skip system/tool bubbles + empties
            if len(text) > max_chars:
                text = text[:max_chars] + " …[truncated]"
            if merged and merged[-1]["role"] == role:
                merged[-1]["text"] += "\n\n" + text   # coalesce a same-role run
            else:
                merged.append({"role": role, "text": text})
        while merged and merged[0]["role"] != "user":
            merged.pop(0)          # must start with a user turn
        while merged and merged[-1]["role"] != "assistant":
            merged.pop()           # end on assistant so the next user message alternates cleanly
        return merged[-max_turns:]

    def _refresh_history_sidebar(self):
        if not hasattr(self, "history_list"):
            return
        self._history_loading = True
        self.history_list.clear()
        sessions = sorted(self.chat_history.get("sessions") or [], key=lambda c: c.get("updated", 0), reverse=True)
        self.chat_history["sessions"] = sessions
        active_row = 0
        for i, chat in enumerate(sessions):
            count = len([m for m in (chat.get("messages") or [])
                         if m.get("role") in ("user", "assistant")])
            title = chat.get("title") or "New chat"
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, chat.get("id"))
            item.setToolTip(f"{title}\n{count} message{'s' if count != 1 else ''}")
            self.history_list.addItem(item)
            if chat.get("id") == self.active_chat_id:
                active_row = i
        if self.history_list.count():
            self.history_list.setCurrentRow(active_row)
        self._history_loading = False
        active = self._active_chat()
        if hasattr(self, "workspace_title"):
            self.workspace_title.setText((active.get("title") or "New task")[:44])
        if hasattr(self, "history_search"):
            self._filter_history(self.history_search.text())

    def _clear_chat_view(self):
        # A fresh/loaded chat should land at the bottom (latest message visible).
        self._chat_stick_bottom = True
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        # The typing indicator + streaming bubble were just deleted above — drop their stale
        # references too, or the next turn's "thinking" bubble never shows (the is-not-None
        # guard sees a dead widget) and streaming appends to a deleted label.
        self._typing_frame = None
        self._typing_dots = None
        self._typing_label = None
        self._streaming_bubble_label = None
        self._streaming_buffer = ""
        self.empty_hint = None

    def _load_active_chat_into_view(self):
        self._clear_chat_view()
        chat = self._active_chat()
        messages = chat.get("messages") or []
        if not messages:
            self._add_empty_state()
            return
        for msg in messages[-120:]:
            self._add_bubble(msg.get("role", "assistant"), msg.get("text", ""), meta=msg.get("meta"))

    def _add_empty_state(self):
        """A calm conversation-first welcome with useful prompts, not a dashboard card."""
        frame = QFrame()
        frame.setObjectName("emptyState")
        frame.setMaximumWidth(820)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content = QVBoxLayout(frame)
        content.setContentsMargins(28, 54, 28, 28)
        content.setSpacing(10)
        mark = DiamondMark(46)   # Ember's diamond brand logo (resizable vector), was the "✦" mark
        self._start_ambient_pulse(mark)
        title = QLabel("What can I help you get done?")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        body = QLabel(
            "Ask a question or describe an outcome. Ember can work with your apps, browser, "
            "files, and screen—and pauses before sensitive actions.")
        body.setObjectName("emptyBody")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        content.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        content.addWidget(title)
        content.addWidget(body)
        suggestions = QWidget()
        suggestion_grid = QGridLayout(suggestions)
        suggestion_grid.setContentsMargins(8, 8, 8, 8)
        suggestion_grid.setHorizontalSpacing(8)
        suggestion_grid.setVerticalSpacing(8)
        for index, (label, command) in enumerate((
                ("Help me use this app", "/apps"),
                ("Research and compare", "/research"),
                ("Organize my files", "/organize"),
                ("Create something new", "/create"))):
            button = QPushButton(label)
            button.setObjectName("promptCard")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(48)          # guarantee vertical room so labels never clip
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, c=command: self._run_slash(c))
            suggestion_grid.addWidget(button, index // 2, index % 2)
        content.addSpacing(3)
        content.addWidget(suggestions)
        hk = (self.settings.get("hotkey") or "ctrl+shift+space").upper()
        self.empty_hint = QLabel(f"{hk} summons Ember anywhere  ·  ⌘/Ctrl K finds tools")
        self.empty_hint.setObjectName("composerHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setVisible(getattr(self, "_size_mode", "normal") != "chatbot")
        content.addWidget(self.empty_hint)
        self.chat_layout.insertWidget(
            self.chat_layout.count() - 1, frame, 0, Qt.AlignmentFlag.AlignHCenter)
        QTimer.singleShot(0, self._clamp_bubble_widths)

    def _append_history(self, role: str, text: str, meta: str | None = None):
        if not text:
            return
        # Only real conversation is persisted and counted. Tool calls, errors, and system
        # notices are live UI only — they don't inflate the message count or the saved history.
        if role not in ("user", "assistant"):
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
        title_model = (self.settings.get("chat_title_model")
                       or model_catalog.DEFAULT_TITLE_MODEL)
        use_ollama = (title_model == "ollama")
        key = (self.settings.get("gemini_api_key") or self.settings.get("gemini_api_key_secondary")
               or self.settings.get("gemini_api_key_3") or self.settings.get("gemini_api_key_4") or "").strip()
        if not use_ollama and not key:
            return   # cloud title needs a Gemini key; local Ollama needs none
        self._title_jobs.add(chat_id)

        prompt = (
            "Create a concise chat title for this user request. "
            "Use 2 to 6 words. No quotes, no punctuation at the end.\n\n"
            f"Request: {first_text[:1200]}"
        )

        def _clean(raw: str) -> str:
            t = (raw or "").strip()
            # local models sometimes add a "Title:" preamble or wrap in quotes — strip both
            t = re.sub(r"^(?:title|chat title)\s*[:\-]\s*", "", t, flags=re.IGNORECASE)
            t = re.sub(r"^[\"'`]+|[\"'`.]+$", "", t)
            t = re.sub(r"\s+", " ", t).strip()
            return " ".join(t.split()[:7])[:56].strip(" -:,.")

        def _run():
            title = ""
            try:
                if use_ollama:
                    import ollama_agent
                    title = _clean(ollama_agent.quick_complete(
                        prompt, model=(self.settings.get("ollama_model") or "")))
                else:
                    from google import genai
                    client = genai.Client(api_key=key)
                    resp = client.models.generate_content(
                        model=model_catalog.resolve(title_model), contents=prompt)
                    title = _clean(getattr(resp, "text", "") or "")
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
        # Just learn durable facts from what the user said. We deliberately do NOT prepend a
        # "[Ember UI conversation context]" / memory block to the message anymore — weaker models
        # echoed that scaffolding straight back into their replies. The agent keeps its own chat
        # history (so follow-ups like "that"/"continue" still work), and learned facts live in the
        # system prompt, so the model keeps context WITHOUT the visible leak.
        try:
            import memory
            memory.learn_from_message(text)
        except Exception:
            pass
        return text

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
        # Keep running in the background (tray) so "Hey Ember" still wakes Ember after the
        # window is closed. Real quit goes through _do_quit (tray ▸ Quit).
        if (not getattr(self, "_really_quit", False)
                and self.settings.get("keep_running_in_background", True)
                and QSystemTrayIcon.isSystemTrayAvailable()):
            e.ignore()
            self.hide()
            if not getattr(self, "_bg_notified", False):
                self._bg_notified = True
                tray = getattr(self, "_tray", None)
                if tray is not None:
                    try:
                        tray.showMessage(
                            "Ember is still listening",
                            "Say “Hey Ember” to bring me back. Quit from the menu-bar icon.",
                            QSystemTrayIcon.MessageIcon.Information, 4000)
                    except Exception:
                        pass
            return
        super().closeEvent(e)
        try:
            QApplication.instance().quit()
        except Exception:
            pass

    def _do_quit(self):
        """Really quit Ember (tray ▸ Quit / explicit quit) — stops the background listeners."""
        self._really_quit = True
        # "Install on Quit": the user chose to update when they close Ember — do it now (the
        # updater installs the new version and relaunches into it).
        if getattr(self, "_install_on_quit", False) and getattr(self, "_pending_update", None):
            try:
                self._start_update(auto=True)
                return
            except Exception:
                pass
        lv = getattr(self, "_live_voice", None)
        if lv is not None:
            try:
                lv.stop()
            except Exception:
                pass
            self._live_voice = None
        for mod in ("wake_word",):
            try:
                __import__(mod).stop()
            except Exception:
                pass
        self.close()
        try:
            QApplication.instance().quit()
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        # The real chat-viewport width is only known once shown; re-clamp bubbles added
        # during init (welcome / history) so they don't render at a stale width and end
        # up visually off to the side.
        QTimer.singleShot(0, self._clamp_bubble_widths)
        duration = self._motion_duration(180)
        if duration <= 0:
            return
        try:
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(duration)
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
        self._attach_paths(paths, "Here are some files/folders I'm asking about:")
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

    def _resize_composer(self):
        """Grow the prompt naturally for multi-line input, while keeping one-line chat compact."""
        box = getattr(self, "input_box", None)
        if box is None:
            return
        try:
            document_height = int(box.document().size().height())
            box.setFixedHeight(max(46, min(96, document_height + 12)))
        except Exception:
            pass

    # --- chat bubbles ---
    def _add_bubble(self, kind: str, text: str, meta: str | None = None) -> QFrame:
        frame = QFrame()
        frame.setProperty("messageKind", kind)
        frame.setProperty("plainText", text or "")
        if kind == "user":
            frame.setObjectName("bubbleUser")
            # The user just spoke — they want to see it + the reply, so re-follow the bottom
            # even if they'd scrolled up earlier.
            self._chat_stick_bottom = True
        elif kind == "tool":
            frame.setObjectName("bubbleTool")
        elif kind == "error":
            frame.setObjectName("bubbleError")
        elif kind == "confirm":
            frame.setObjectName("bubbleConfirm")
        elif kind == "system":
            frame.setObjectName("bubbleSystem")
        else:
            frame.setObjectName("bubble")
        # Assistant replies read like a document; user/system messages hug their content.
        horizontal_policy = (QSizePolicy.Policy.Preferred if kind in ("user", "system")
                             else QSizePolicy.Policy.Expanding)
        frame.setSizePolicy(horizontal_policy, QSizePolicy.Policy.Preferred)
        # Hard cap so it never exceeds the visible chat area (scrollbar reserved), even mid-resize.
        try:
            frame_w, _ = self._chat_widths()
            if frame_w > 0:
                frame.setMaximumWidth(frame_w)
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
        body.setObjectName("bubbleBody")   # so the chat-text-size override can target it
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(_md_to_html(text))
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setOpenExternalLinks(True)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body.setMinimumWidth(50)
        v.addWidget(body)
        # ChatGPT-like rhythm: user messages sit right; assistant and notices share a centered
        # readable column instead of every message becoming a full-width dashboard card.
        alignment = (Qt.AlignmentFlag.AlignRight if kind == "user"
                     else Qt.AlignmentFlag.AlignHCenter)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame, 0, alignment)
        self._fade_in(frame, 180)
        QTimer.singleShot(35, self._scroll_to_bottom_smooth)
        # Re-clamp once layout settles so a bubble added before geometry is known
        # (e.g. during init) doesn't sit at the wrong width.
        QTimer.singleShot(0, self._clamp_bubble_widths)
        # Satisfying grow-in for real messages. NOT for the empty streaming bubble (it would
        # clip incoming text) and NOT the height-fragile tool bubbles. The animation ends with
        # maximumHeight unbounded, so even if the target height is off it can never stay collapsed.
        if (text and text.strip() and kind not in ("tool",)
                and self.settings.get("animations_enabled", True)
                and self.settings.get("bubble_animation", True)):
            frame.setMaximumHeight(0)
            QTimer.singleShot(0, lambda f=frame: self._animate_bubble_in(f))
        return frame

    def _add_thinking(self, text: str) -> None:
        """Show the model's reasoning as a collapsible, visually-quiet 'Thinking' note.
        Skipped entirely when the show_thinking setting is off, so it never competes with the
        actual reply."""
        text = (text or "").strip()
        if not text or not self.settings.get("show_thinking", True):
            return
        self._hide_typing_indicator()
        frame = QFrame()
        frame.setObjectName("bubbleThinking")
        frame.setProperty("messageKind", "thinking")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        try:
            frame_w, _ = self._chat_widths()
            if frame_w > 0:
                frame.setMaximumWidth(frame_w)
        except Exception:
            pass
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(5)
        toggle = QPushButton("▸  Thinking")
        toggle.setObjectName("thinkingToggle")
        toggle.setCheckable(True)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        body = QLabel(text)
        body.setObjectName("thinkingBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setVisible(False)

        def _toggle(checked, _t=toggle, _b=body):
            _b.setVisible(checked)
            _t.setText("▾  Thinking" if checked else "▸  Thinking")
            QTimer.singleShot(0, self._clamp_bubble_widths)

        toggle.toggled.connect(_toggle)
        v.addWidget(toggle)
        v.addWidget(body)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame, 0,
                                      Qt.AlignmentFlag.AlignLeft)
        try:
            self._fade_in(frame, 160)
        except Exception:
            pass
        QTimer.singleShot(0, self._clamp_bubble_widths)
        QTimer.singleShot(35, self._scroll_to_bottom_smooth)

    def _animate_bubble_in(self, frame):
        """Snappy grow-in (0 -> natural height) with an easing curve, then release the
        height cap. Layout-safe: no QGraphicsEffect (those regressed bubble width/word-wrap),
        and the cap always ends unbounded so a bubble can never be left collapsed.

        Crucially, the target height is measured AFTER the bubble's word-wrap width is locked
        in (clamp + layout activate). Measuring too early returned a single-line height, so the
        old animation grew to a stub and then SNAPPED to full height — that ugly jump was the
        'random fade' the bubbles seemed to do."""
        QWIDGET_MAX = 16777215
        try:
            # Lock THIS bubble's width (and its labels' wrap width) and force the layout to
            # compute the real wrapped height NOW, so we animate to the final height.
            self._clamp_bubble_widths()
            lay = frame.layout()
            if lay is not None:
                lay.activate()
            frame.adjustSize()
            h = max(1, frame.sizeHint().height())
            anim = QPropertyAnimation(frame, b"maximumHeight", self)
            anim.setDuration(self._motion_duration(230))
            anim.setStartValue(0)
            anim.setEndValue(h)
            anim.setEasingCurve(
                QEasingCurve.Type.OutBack if self._motion_mode() == "dynamic"
                else QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda f=frame: f.setMaximumHeight(QWIDGET_MAX))
            anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
            if not hasattr(self, "_anims"):
                self._anims = []
            self._anims.append(anim)
            self._anims = self._anims[-50:]
        except Exception:
            try:
                frame.setMaximumHeight(QWIDGET_MAX)
            except Exception:
                pass

    def _chat_widths(self) -> tuple[int, int]:
        """(frame_width, label_width) for chat bubbles. Reserves the vertical scrollbar's
        width ALWAYS — even while it's hidden — so a bubble sized before the bar appears (as
        the chat grows) doesn't end up clipped under it. That right-edge clipping was the bug."""
        try:
            W = self.chat_scroll.width()
            if W < 120:
                return 0, 0
            sb = self.chat_scroll.verticalScrollBar()
            sb_w = sb.sizeHint().width() or 14
            available = max(80, W - sb_w - 48)  # chat container margins + breathing room
            frame_w = min(860, available)
            lbl_w = max(60, frame_w - 28)
            return frame_w, lbl_w
        except Exception:
            return 0, 0

    def _clamp_bubble_widths(self):
        """Size every bubble to the chat width. Inner labels get a FIXED width so their
        word-wrapped height is computed correctly — otherwise the layout allocates a one-line
        height and bubbles overlap ('playing cards') or clip their text."""
        try:
            frame_w, lbl_w = self._chat_widths()
            if frame_w <= 0 or not hasattr(self, "chat_layout"):
                return
            from PyQt6.QtWidgets import QLabel
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if w is None:
                    continue
                name = w.objectName()
                if name == "bubbleUser":
                    body = next((label for label in w.findChildren(QLabel)
                                 if label.objectName() == "bubbleBody"), None)
                    plain = str(w.property("plainText") or "")
                    longest = max(plain.splitlines() or [plain], key=len)
                    natural = (body.fontMetrics().horizontalAdvance(longest) + 34
                               if body is not None else 240)
                    target = max(96, min(660, int(frame_w * 0.76), natural))
                    w.setMinimumWidth(target)
                    w.setMaximumWidth(target)
                elif name == "emptyState":
                    target = frame_w
                    w.setMinimumWidth(0)
                    w.setMaximumWidth(target)
                elif name in ("bubbleTool", "bubbleSystem"):
                    target = min(760, frame_w)
                    w.setMinimumWidth(0)
                else:
                    target = frame_w
                    w.setMinimumWidth(0)
                    w.setMaximumWidth(target)
                if name == "emptyState":
                    w.updateGeometry()
                    continue
                for lbl in w.findChildren(QLabel):
                    inner = max(60, target - 28)
                    if name == "bubbleUser":
                        lbl.setFixedWidth(inner)
                    else:
                        lbl.setFixedWidth(inner)
                w.updateGeometry()
        except Exception:
            pass

    def resizeEvent(self, e):
        """Reposition the grip immediately; debounce the (heavier) bubble re-clamp so it runs
        once after a drag-resize settles instead of on every intermediate event."""
        super().resizeEvent(e)
        self._position_size_grip()
        siri = getattr(self, "_siri", None)
        if siri is not None:
            try:
                siri.cover()
            except Exception:
                pass
        if not hasattr(self, "_clamp_timer"):
            self._clamp_timer = QTimer(self)
            self._clamp_timer.setSingleShot(True)
            self._clamp_timer.timeout.connect(self._clamp_bubble_widths)
        self._clamp_timer.start(60)

    def _position_size_grip(self):
        """A bottom-right grip makes the frameless window resizable (drag to resize)."""
        g = getattr(self, "_size_grip", None)
        if g is None:
            from PyQt6.QtWidgets import QSizeGrip
            g = self._size_grip = QSizeGrip(self)
            g.setFixedSize(18, 18)
        g.move(self.width() - g.width() - 6, self.height() - g.height() - 6)
        g.raise_()
        g.show()

    def _fade_in(self, widget: QWidget, duration: int = 220):
        """Bubbles appear instantly.

        This used to fade each bubble in with a QGraphicsOpacityEffect. But a widget that
        has a QGraphicsEffect attached is rendered through an offscreen pixmap at its
        UNCONSTRAINED natural size — which made chat bubbles ignore the column width
        (overflowing under the Command Center) and leave ghosted/empty-box artifacts.
        Correct rendering beats the fade, so this is now a no-op. The window-open fade
        (showEvent, window-level opacity) is unaffected."""
        return

    def _on_chat_scrolled(self, value: int):
        """Track whether the user is parked at the bottom. Programmatic scroll-to-max keeps
        us stuck; dragging up by more than a small tolerance un-sticks (so we stop yanking
        them back down while they read history); returning to the bottom re-sticks."""
        try:
            bar = self.chat_scroll.verticalScrollBar()
            self._chat_stick_bottom = (bar.maximum() - value) <= 48
        except Exception:
            pass

    def _chat_follow_bottom(self):
        """Snap to the bottom when content grows, but only if we're sticking (user at bottom)."""
        if not getattr(self, "_chat_stick_bottom", True):
            return
        try:
            bar = self.chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _scroll_to_bottom_smooth(self, duration: int = 190):
        try:
            bar = self.chat_scroll.verticalScrollBar()
            # Respect the user reading history — don't yank them down if they scrolled up.
            if not getattr(self, "_chat_stick_bottom", True):
                return
            end = bar.maximum()
            duration = self._motion_duration(duration)
            if duration <= 0:
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
        # Light the Siri glow while Ember is working (covers typed + voice turns).
        self._set_siri("thinking")
        existing = getattr(self, "_typing_frame", None)
        if existing is not None:
            try:
                existing.isVisible()   # touches the C++ object; raises if it was deleted
                return                 # a live indicator is already showing
            except RuntimeError:
                self._typing_frame = None   # stale (deleted) ref -> recreate below
        frame = QFrame()
        frame.setObjectName("typingIndicator")
        frame.setMaximumWidth(820)
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(10)
        dots = None
        try:
            from siri_glow import ThinkingDots
            dots = ThinkingDots(frame)
            dots.start()
            h.addWidget(dots)
        except Exception:
            # Fallback to a plain pulsing-text indicator if the widget can't load.
            dl = QLabel("●")
            dl.setObjectName("typingDots")
            h.addWidget(dl)
        label = QLabel("Ember is thinking…")
        label.setStyleSheet("color: #8f99ad; font-size: 11px;")
        h.addWidget(label)
        h.addStretch()
        self.chat_layout.insertWidget(
            self.chat_layout.count() - 1, frame, 0, Qt.AlignmentFlag.AlignHCenter)
        self._typing_frame = frame
        self._typing_dots = dots
        QTimer.singleShot(45, self._scroll_to_bottom_smooth)

    def _hide_typing_indicator(self):
        d = getattr(self, "_typing_dots", None)
        if d is not None:
            try:
                d.stop()
            except Exception:
                pass
        self._typing_dots = None
        f = getattr(self, "_typing_frame", None)
        if f is not None:
            try:                       # the frame may already be deleted (e.g. chat cleared)
                f.setParent(None)
                f.deleteLater()
            except RuntimeError:
                pass
        self._typing_frame = None
        self._typing_label = None

    def _set_status(self, text: str):
        self.status_label.setText(text)
        model = self.settings.get("model_id") or self.settings.get("gemini_model") or "Choose model"
        model_button = getattr(self, "model_btn", None)
        if model_button is not None:
            model_button.setText(str(model))
        metric = getattr(self, "model_metric", None)
        if metric is not None:
            metric.setText(f"{model} · {text}")

    def _submit_user_text(self, text: str, meta: str | None = None, status: str = "Thinking...") -> bool:
        if not self.agent:
            # A key IS configured -> the agent likely failed to init earlier (e.g. the
            # working-dir / FileNotFound issue). Try to rebuild it before nagging about a key.
            has_key = bool(self.settings.get("gemini_api_key") or self.settings.get("anthropic_api_key")
                           or self.settings.get("openai_api_key")
                           or (self.settings.get("provider") == "ollama"))
            if has_key:
                try:
                    self._build_agent()
                except Exception:
                    pass
            if not self.agent:
                if has_key:
                    QMessageBox.warning(self, "Ember isn't ready yet",
                        "The agent didn't start. Give it a moment and try again, or restart "
                        "Ember. (If you just added a key, reopen Settings and re-save.)")
                else:
                    QMessageBox.warning(self, "No API key",
                        "Open settings (gear) and add your Gemini API key first.")
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
        self._activity_reset()
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
        if target == "__manual__":
            self._open_manual_mode()
            return True
        # Any other feature-opener target (e.g. __browser_app__, __scan_folder__, __sandbox__,
        # __usage__, __plugins__) is handled by _run_slash — route it there instead of sending
        # the literal "__opener__" token to the agent as a chat message.
        if target.startswith("__"):
            self._run_slash(target)
            return True
        expanded = target + (" " + rest if rest else "")
        agent_text = self._agent_contextual_text(expanded)
        self._activity_reset()
        self._add_bubble("user", text + f"\n(expanded: {target[:80]}{'…' if len(target) > 80 else ''})")
        self._append_history("user", text)
        self._set_status("Thinking…")
        self.send_btn.setEnabled(False)
        self._show_typing_indicator()
        self.agent.send_user_message(agent_text)
        return True

    def _run_slash(self, cmd: str):
        # Map each Command-Center feature token to its opener METHOD NAME. Anything not here is
        # a "quick task" that gets typed into the box and sent to the agent.
        # NB: resolved via getattr so a single missing/renamed handler can't blow up the whole
        # dict and leave EVERY button dead (that's exactly how all the buttons can go silent).
        feature_methods = {
            "__features__": "_open_features",
            "__antivirus__": "_open_antivirus_app",
            "__adblock__": "_open_adblock",
            "__voice_chat__": "_toggle_voice_chat",
            "__manual__": "_open_manual_mode",
            "__remote__": "_start_remote_control",
            "__browser_app__": "_open_ember_browser",
            "__scan_folder__": "_scan_folder",
            "__sandbox__": "_run_in_sandbox_ui",
            "__update__": "_update_now",
            "__usage__": "_show_usage_dashboard",
            "__plugins__": "_open_plugins_manager",
            "__passwords__": "_open_passwords_manager",
            "__vpn__": "_open_vpn_manager",
            "__workflow__": "_open_workflow_recorder",
            "__screen_record__": "_open_screen_recorder",
            "__snippets__": "_open_snippets_manager",
            "__macros__": "_open_macros_manager",
            "__local_ai__": "_open_local_ai",
            "__setup_tour__": "_open_setup_tour",
            "__terminal__": "_open_terminal",
            "__agents__": "_open_agents",
            "__storage__": "_open_storage_inspector",
            "__network_inspector__": "_open_network_inspector",
            "__clipboard__": "_open_clipboard_history",
        }
        handler = None
        if cmd in feature_methods:
            handler = getattr(self, feature_methods[cmd], None)
        if handler is not None:
            # Surface any failure instead of letting the button silently do nothing
            # (a raised exception in a Qt slot is otherwise swallowed -> "dead button").
            try:
                handler()
            except Exception as e:
                traceback.print_exc()
                QMessageBox.warning(self, "Couldn't open that",
                                    f"{type(e).__name__}: {e}")
            return
        if cmd.startswith("__"):
            # An unknown feature token would otherwise be typed to the agent as gibberish.
            QMessageBox.information(self, "Not available",
                                   f"That feature isn't available in this build ({cmd}).")
            return
        self.input_box.setPlainText(cmd)
        self._on_send()

    def _open_terminal(self):
        """Open the built-in terminal + Python runner."""
        TerminalDialog(self).exec()

    def _ensure_task_manager(self):
        if getattr(self, "_task_manager", None) is None:
            import agent_tasks
            self._task_manager = agent_tasks.TaskManager(self._agent_task_runner)
        return self._task_manager

    def _open_agents(self):
        """Open the parallel agent-tasks dashboard."""
        AgentsDialog(self._ensure_task_manager(), self).exec()

    def _open_storage_inspector(self):
        StorageInspectorDialog(self).exec()

    def _open_network_inspector(self):
        NetworkInspectorDialog(self).exec()

    def _open_clipboard_history(self):
        ClipboardHistoryDialog(self).exec()

    def _spawn_agent(self):
        """Build a FRESH agent instance for a background parallel task (separate from self.agent).
        Mirrors _build_agent's provider selection; raises RuntimeError with a clear message if a
        required key is missing."""
        s = self.settings
        raw_model = s.get("model_id") or s.get("gemini_model") or "gemini-3.1-flash-lite"
        model_id = model_catalog.resolve(raw_model)
        provider = s.get("provider") or model_catalog.provider_for(raw_model)
        if provider == "ollama":
            from ollama_agent import OllamaAgent
            return OllamaAgent(model_name=s.get("ollama_model") or "",
                               auto_screenshot=bool(s.get("auto_screenshot", True)))
        if provider == "claude":
            key = s.get("anthropic_api_key") or ""
            if not key:
                raise RuntimeError("Add an Anthropic API key in Settings to run Claude tasks.")
            from claude_agent import ClaudeAgent
            return ClaudeAgent(api_key=key, model_name=model_id,
                               auto_screenshot=bool(s.get("auto_screenshot", True)))
        if provider == "openai":
            key = s.get("openai_api_key") or ""
            label = s.get("openai_provider_label") or "OpenAI"
            if not key:
                raise RuntimeError(f"Add a{'n' if label[:1] in 'AEIOU' else ''} {label} API key in Settings to run tasks.")
            from openai_agent import OpenAIAgent
            return OpenAIAgent(api_key=key, model_name=model_id,
                               base_url=s.get("openai_base_url") or None,
                               provider_label=label,
                               auto_screenshot=bool(s.get("auto_screenshot", True)))
        if not s.get("gemini_api_key"):
            raise RuntimeError("Add a Gemini API key in Settings to run tasks.")
        from agent import Agent
        return Agent(
            api_key=s["gemini_api_key"], secondary_api_key=s.get("gemini_api_key_secondary") or None,
            backup_api_keys=[s.get("gemini_api_key_3") or "", s.get("gemini_api_key_4") or ""],
            dual_api_failover=bool(s.get("dual_api_failover", True)), model_name=model_id,
            anthropic_key=s.get("anthropic_api_key") or None,
            anthropic_model=s.get("anthropic_model", "claude-opus-4-8"),
            auto_screenshot=bool(s.get("auto_screenshot", True)),
            request_timeout_seconds=int(s.get("request_timeout_seconds", 30)),
            lean_tools=bool(s.get("lean_tools", False)))

    def _agent_task_runner(self, prompt, emit, stop_event):
        """Drive a fresh agent to completion for one parallel task (called on a worker thread by
        the TaskManager). Streams output via emit(); denies confirmation-required actions since
        the task is unattended; honours stop_event."""
        import threading
        agent = self._spawn_agent()
        done = threading.Event()

        def on_ev(ev):
            k = getattr(ev, "kind", None)
            p = getattr(ev, "payload", None)
            if k == "stream_chunk" and p:
                emit(str(p))
            elif k == "message" and p:
                emit(str(p) + "\n")
            elif k == "tool_call":
                try:
                    emit(f"\n· {p.get('name')}\n")
                except Exception:
                    emit("\n· (tool)\n")
            elif k == "error":
                emit(f"\n[error] {p}\n")
                done.set()
            elif k == "done":
                done.set()
            elif k == "confirm":
                try:
                    p.response.put(False)   # unattended background task: deny risky actions
                except Exception:
                    pass
                emit("\n[skipped an action that needs confirmation — run it in the main chat to approve]\n")

        agent.subscribe(on_ev)
        agent.send_user_message(prompt)
        while not done.is_set():
            if stop_event.is_set():
                try:
                    agent.stop()
                except Exception:
                    pass
                break
            done.wait(0.25)
        return ""

    def _open_antivirus_app(self):
        """Open the standalone graphical Antivirus app."""
        try:
            AntivirusDialog(self).exec()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "Antivirus", f"{type(e).__name__}: {e}")

    def _open_adblock(self):
        """Open the standalone graphical Ad Blocker app (its own window, like the Antivirus)."""
        try:
            import network_adblock  # noqa: F401 — ensure the module is importable first
        except Exception as e:
            QMessageBox.warning(self, "Ad blocker", f"unavailable: {e}")
            return
        try:
            AdBlockerDialog(self).exec()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "Ad blocker", f"{type(e).__name__}: {e}")

    def _open_setup_tour(self):
        """Open the friendly first-run Setup Tour (also reachable any time via /setup)."""
        try:
            SetupTourDialog(self.settings, self._apply_tour_result, self).exec()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "Setup", f"{type(e).__name__}: {e}")
        # Mark the tour as seen (finish OR skip) so it doesn't auto-reopen every launch.
        if not self.settings.get("setup_complete"):
            self.settings["setup_complete"] = True
            try:
                save_settings(self.settings)
            except Exception:
                pass

    def _apply_tour_result(self, updates: dict):
        """Save the tour's choices, apply them live, and (re)build the agent with the chosen brain."""
        try:
            self.settings.update(updates or {})
            save_settings(self.settings)
        except Exception:
            pass
        for fn in (self._apply_offline_mode, self._apply_tts_config):
            try:
                fn()
            except Exception:
                pass
        self._init_agent()
        self._add_bubble("system", "✓ Setup complete — you can change anything in Settings (gear). "
                                   "Tip: type /setup to run this tour again.")

    def _open_features(self):
        """Show the browsable, searchable Features directory."""
        try:
            FeaturesDialog(self._features_action, self).exec()
        except Exception as e:
            traceback.print_exc()
            QMessageBox.warning(self, "Features", f"{type(e).__name__}: {e}")

    def _features_action(self, action):
        """Perform a feature chosen in the Features directory."""
        kind, val = action
        if kind == "open":
            self._run_slash(val)
        elif kind == "type":
            self.input_box.setPlainText(val)
            cur = self.input_box.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            self.input_box.setTextCursor(cur)
            self.input_box.setFocus()
        elif kind == "settings":
            # Open the feature as its OWN focused window (its settings section, tab bar hidden),
            # not the top of the full Settings dialog. val is the section name, "" = full Settings.
            self._open_settings(only_tab=(val or None))

    def _show_usage_dashboard(self):
        show_usage_dashboard(self)

    def _open_plugins_manager(self):
        """Show loaded plugins and let the user reload, scaffold a new one, or open the folder."""
        try:
            import plugin_system
            info = plugin_system.list_plugins()
        except Exception as e:
            QMessageBox.warning(self, "Plugins", f"Plugin system unavailable: {e}")
            return
        plugins = info.get("plugins") or []
        lines = ["<b>Plugins</b> — drop a .py file into the plugins/ folder to add tools.", ""]
        if plugins:
            for p in plugins:
                tools = ", ".join(p.get("tools") or []) or "(no tools)"
                lines.append(f"• {p.get('file')}: {tools}")
        else:
            lines.append("No plugins yet.")
        if info.get("errors"):
            lines.append("")
            lines.append("<b>Errors</b>")
            for er in info["errors"]:
                lines.append(f"• {er.get('plugin')}: {er.get('error')}")
        box = QMessageBox(self)
        box.setWindowTitle("Plugins")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        new_btn = box.addButton("New plugin…", QMessageBox.ButtonRole.ActionRole)
        reload_btn = box.addButton("Reload", QMessageBox.ButtonRole.ActionRole)
        folder_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        try:
            if clicked is new_btn:
                from PyQt6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(self, "New plugin", "Plugin name:")
                if ok and name.strip():
                    r = plugin_system.create_plugin_template(name.strip())
                    if r.get("ok"):
                        self._add_bubble("system", f"Created plugin template: {r.get('path')}\n"
                                                   "Edit it, then click Plugins ▸ Reload.")
                    else:
                        QMessageBox.warning(self, "Plugins", r.get("error", "Could not create."))
            elif clicked is reload_btn:
                r = plugin_system.reload_plugins()
                self._add_bubble("system",
                                 f"Reloaded plugins: {r.get('loaded', 0)} loaded "
                                 f"({', '.join(r.get('tools') or []) or 'none'}). "
                                 "Restart Ember to expose new plugin tools to the agent.")
            elif clicked is folder_btn:
                import plugin_system as _ps
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                _ps.PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(_ps.PLUGINS_DIR)))
        except Exception as e:
            QMessageBox.warning(self, "Plugins", f"{type(e).__name__}: {e}")

    def _open_passwords_manager(self):
        """Review/delete saved website logins (encrypted via the key vault). New logins are
        added from the 🔑 button inside Ember Browser."""
        try:
            import browser_passwords
            doms = browser_passwords.list_logins()
        except Exception as e:
            QMessageBox.warning(self, "Passwords", f"Password manager unavailable: {e}")
            return
        lines = ["<b>Saved logins</b> (encrypted)", ""]
        lines += ([f"• {d}" for d in doms] if doms else ["No saved logins yet."])
        lines += ["", "Add a login from the 🔑 button inside Ember Browser."]
        box = QMessageBox(self)
        box.setWindowTitle("Passwords")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        browser_btn = box.addButton("Open Ember Browser", QMessageBox.ButtonRole.ActionRole)
        del_btn = box.addButton("Delete…", QMessageBox.ButtonRole.ActionRole) if doms else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        if clicked is browser_btn:
            self._open_ember_browser()
        elif del_btn is not None and clicked is del_btn:
            from PyQt6.QtWidgets import QInputDialog
            dom, ok = QInputDialog.getItem(self, "Delete login", "Site:", doms, 0, False)
            if ok and dom:
                browser_passwords.delete_login(dom)
                self._add_bubble("system", f"Deleted saved login for {dom}.")

    def _open_vpn_manager(self):
        """Quick VPN panel: connect/disconnect a saved WireGuard location."""
        try:
            import vpn
            st = vpn.status(quick=True)
            vl = vpn.list_locations()
        except Exception as e:
            QMessageBox.warning(self, "VPN", f"VPN unavailable: {e}")
            return
        locs = [l.get("name", "?") for l in (vl.get("locations") or [])]
        lines = ["<b>VPN</b> (bring-your-own WireGuard)", "",
                 "Status: " + ("Connected ✓" if st.get("connected") else "Not connected"),
                 f"Saved locations: {len(locs)}"]
        if not vl.get("wireguard_installed", True):
            lines.append("Install the free WireGuard app to connect.")
        box = QMessageBox(self)
        box.setWindowTitle("VPN")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        conn_btn = box.addButton("Connect…", QMessageBox.ButtonRole.ActionRole) if locs else None
        disc_btn = box.addButton("Disconnect", QMessageBox.ButtonRole.ActionRole)
        settings_btn = box.addButton("VPN settings…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        try:
            if conn_btn is not None and clicked is conn_btn:
                from PyQt6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getItem(self, "Connect VPN", "Location:", locs, 0, False)
                if ok and name:
                    r = vpn.connect(name)
                    self._add_bubble("system" if r.get("ok") else "error",
                                     f"VPN connected: {name}" if r.get("ok")
                                     else r.get("error", "VPN connect failed"))
            elif clicked is disc_btn:
                r = vpn.disconnect()
                self._add_bubble("system" if r.get("ok") else "error",
                                 "VPN disconnected" if r.get("ok")
                                 else r.get("error", "VPN disconnect failed"))
            elif clicked is settings_btn:
                self._open_settings()
        except Exception as e:
            QMessageBox.warning(self, "VPN", f"{type(e).__name__}: {e}")

    def _open_workflow_recorder(self):
        """Record & replay real mouse/keyboard workflows."""
        try:
            import workflow_recorder as wfr
            flows = (wfr.list_workflows().get("workflows")) or []
        except Exception as e:
            QMessageBox.warning(self, "Workflows", f"Workflow recorder unavailable: {e}")
            return
        lines = ["<b>Workflow recorder</b> — record & replay mouse/keyboard", ""]
        lines += ([f"• {f.get('name')} ({f.get('event_count', 0)} events)" for f in flows]
                  if flows else ["No saved workflows yet."])
        box = QMessageBox(self)
        box.setWindowTitle("Workflows")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        rec_btn = box.addButton("Record new…", QMessageBox.ButtonRole.ActionRole)
        stop_btn = box.addButton("Stop recording", QMessageBox.ButtonRole.ActionRole)
        play_btn = box.addButton("Replay…", QMessageBox.ButtonRole.ActionRole) if flows else None
        del_btn = box.addButton("Delete…", QMessageBox.ButtonRole.ActionRole) if flows else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        from PyQt6.QtWidgets import QInputDialog
        try:
            if clicked is rec_btn:
                name, ok = QInputDialog.getText(self, "Record workflow", "Name:")
                if ok and name.strip():
                    r = wfr.record_workflow_start(name.strip())
                    self._add_bubble("system" if r.get("ok") else "error",
                        (f"Recording '{name.strip()}' — do your actions, then reopen Workflows "
                         "and click Stop recording." if r.get("ok")
                         else r.get("error", "Could not start recording")))
            elif clicked is stop_btn:
                r = wfr.record_workflow_stop()
                self._add_bubble("system" if r.get("ok") else "error",
                    (f"Saved '{r.get('name')}' ({r.get('event_count', 0)} events)."
                     if r.get("ok") else r.get("error", "Not recording")))
            elif play_btn is not None and clicked is play_btn:
                names = [f.get("name") for f in flows]
                name, ok = QInputDialog.getItem(self, "Replay workflow", "Workflow:", names, 0, False)
                if ok and name and QMessageBox.question(
                        self, "Replay",
                        f"Replay '{name}'? It will move your mouse and type for you."
                        ) == QMessageBox.StandardButton.Yes:
                    # Replay sleeps between events — run off the UI thread so it doesn't freeze.
                    self._add_bubble("system", f"Replaying workflow '{name}'…")
                    threading.Thread(target=wfr.replay_workflow, args=(name,), daemon=True).start()
            elif del_btn is not None and clicked is del_btn:
                names = [f.get("name") for f in flows]
                name, ok = QInputDialog.getItem(self, "Delete workflow", "Workflow:", names, 0, False)
                if ok and name:
                    wfr.delete_workflow(name)
                    self._add_bubble("system", f"Deleted workflow '{name}'.")
        except Exception as e:
            QMessageBox.warning(self, "Workflows", f"{type(e).__name__}: {e}")

    def _open_screen_recorder(self):
        """Start/stop screen recording to a video file."""
        try:
            import productivity_tools as pt
            st = pt.screen_record_status()
        except Exception as e:
            QMessageBox.warning(self, "Screen recorder", f"Unavailable: {e}")
            return
        recording = st.get("recording")
        lines = ["<b>Screen recorder</b>", "",
                 (f"● Recording… {st.get('frames', 0)} frames" if recording else "Not recording.")]
        box = QMessageBox(self)
        box.setWindowTitle("Screen recorder")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        start_btn = box.addButton("Start…", QMessageBox.ButtonRole.ActionRole) if not recording else None
        stop_btn = box.addButton("Stop", QMessageBox.ButtonRole.ActionRole) if recording else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        from PyQt6.QtWidgets import QInputDialog
        try:
            if start_btn is not None and clicked is start_btn:
                secs, ok = QInputDialog.getInt(self, "Screen recorder",
                                               "Record for how many seconds?", 10, 1, 120)
                if ok:
                    r = pt.screen_record_start(secs)
                    self._add_bubble("system" if r.get("ok") else "error",
                        (f"Recording the screen for {secs}s → {r.get('output')}"
                         if r.get("ok") else r.get("error", "Could not start recording")))
            elif stop_btn is not None and clicked is stop_btn:
                r = pt.screen_record_stop()
                self._add_bubble("system" if r.get("ok") else "error",
                    (f"Saved recording: {r.get('output')} ({r.get('frames', 0)} frames)"
                     if r.get("ok") else r.get("error", "Not recording")))
        except Exception as e:
            QMessageBox.warning(self, "Screen recorder", f"{type(e).__name__}: {e}")

    def _open_snippets_manager(self):
        """Save & manage reusable text snippets (expand with ;keyword in chat)."""
        try:
            import productivity_tools as pt
            snips = (pt.snippet_list().get("snippets")) or {}
        except Exception as e:
            QMessageBox.warning(self, "Snippets", f"Unavailable: {e}")
            return
        lines = ["<b>Snippets</b> — type ;keyword in chat to expand", ""]
        lines += ([f"• ;{k} → {v}" for k, v in snips.items()] if snips else ["No snippets yet."])
        box = QMessageBox(self)
        box.setWindowTitle("Snippets")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        add_btn = box.addButton("Add…", QMessageBox.ButtonRole.ActionRole)
        del_btn = box.addButton("Delete…", QMessageBox.ButtonRole.ActionRole) if snips else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        from PyQt6.QtWidgets import QInputDialog
        try:
            if clicked is add_btn:
                kw, ok = QInputDialog.getText(self, "Add snippet", "Keyword:")
                if ok and kw.strip():
                    txt, ok2 = QInputDialog.getMultiLineText(self, "Add snippet",
                                                             f"Text for ;{kw.strip()}:")
                    if ok2 and txt.strip():
                        pt.snippet_save(kw.strip(), txt)
                        self._add_bubble("system", f"Saved snippet ;{kw.strip()}.")
            elif del_btn is not None and clicked is del_btn:
                keys = list(snips.keys())
                kw, ok = QInputDialog.getItem(self, "Delete snippet", "Keyword:", keys, 0, False)
                if ok and kw:
                    pt.snippet_delete(kw)
                    self._add_bubble("system", f"Deleted snippet ;{kw}.")
        except Exception as e:
            QMessageBox.warning(self, "Snippets", f"{type(e).__name__}: {e}")

    def _open_macros_manager(self):
        """Save & run named task macros (Ember carries out the saved task)."""
        try:
            import macros
            items = (macros.list_macros().get("macros")) or []
        except Exception as e:
            QMessageBox.warning(self, "Macros", f"Unavailable: {e}")
            return
        lines = ["<b>Macros</b> — named tasks Ember runs on demand", ""]
        lines += ([f"• {m.get('name')}: {m.get('task', '')}" for m in items]
                  if items else ["No macros yet."])
        box = QMessageBox(self)
        box.setWindowTitle("Macros")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        add_btn = box.addButton("New…", QMessageBox.ButtonRole.ActionRole)
        run_btn = box.addButton("Run…", QMessageBox.ButtonRole.ActionRole) if items else None
        del_btn = box.addButton("Delete…", QMessageBox.ButtonRole.ActionRole) if items else None
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        from PyQt6.QtWidgets import QInputDialog
        names = [m.get("name") for m in items]
        try:
            if clicked is add_btn:
                name, ok = QInputDialog.getText(self, "New macro", "Name:")
                if ok and name.strip():
                    task, ok2 = QInputDialog.getMultiLineText(self, "New macro", "Task description:")
                    if ok2 and task.strip():
                        macros.save_macro(name.strip(), task.strip())
                        self._add_bubble("system", f"Saved macro '{name.strip()}'.")
            elif run_btn is not None and clicked is run_btn:
                name, ok = QInputDialog.getItem(self, "Run macro", "Macro:", names, 0, False)
                if ok and name:
                    gm = macros.get_macro(name)
                    if gm.get("ok") and gm.get("task"):
                        self.input_box.setPlainText(gm["task"])
                        self._on_send()
            elif del_btn is not None and clicked is del_btn:
                name, ok = QInputDialog.getItem(self, "Delete macro", "Macro:", names, 0, False)
                if ok and name:
                    macros.delete_macro(name)
                    self._add_bubble("system", f"Deleted macro '{name}'.")
        except Exception as e:
            QMessageBox.warning(self, "Macros", f"{type(e).__name__}: {e}")

    def _open_local_ai(self):
        """Show local Ollama status and offer to switch Ember's brain to it (offline, no key)."""
        try:
            import local_ai
            st = local_ai.local_ai_status()
        except Exception as e:
            QMessageBox.warning(self, "Local AI", f"Could not check Ollama: {e}")
            return
        cur = (self.settings.get("provider")
               or model_catalog.provider_for(self.settings.get("model_id", "")))
        lines = ["<b>Local AI (Ollama)</b> — offline · no API key · no rate limits", ""]
        if st.get("running"):
            models = st.get("models") or []
            lines.append("Ollama is running ✓")
            lines.append("Installed models: " + (", ".join(models) if models
                         else "(none — run: ollama pull llama3.2)"))
        else:
            lines.append(st.get("note") or "Ollama is not running. Install it from ollama.com, "
                         "then run: ollama pull llama3.2")
        lines += ["", ("Ember is currently using the local model." if cur == "ollama"
                       else "Click 'Use local AI' to switch Ember's brain to Ollama. "
                            "Local mode is chat only (no computer control).")]
        box = QMessageBox(self)
        box.setWindowTitle("Local AI")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br>".join(lines))
        use_btn = (box.addButton("Use local AI", QMessageBox.ButtonRole.AcceptRole)
                   if cur != "ollama" else None)
        settings_btn = box.addButton("Open model settings…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        clicked = box.clickedButton()
        if use_btn is not None and clicked is use_btn:
            self.settings["model_id"] = "ollama"
            self.settings["provider"] = "ollama"
            save_settings(self.settings)
            self._init_agent()
            self._add_bubble("system", "Switched to the local Ollama brain (offline, no key). "
                                       "Note: local mode is chat only — for computer control, "
                                       "switch back to Gemini or Claude in Settings.")
        elif clicked is settings_btn:
            self._open_settings()

    def _open_ember_browser(self):
        """Open the secure, AI-assisted Ember Browser window (Qt WebEngine)."""
        try:
            import ember_browser
        except Exception as e:
            self._add_bubble("error", f"Ember Browser unavailable: {e}")
            return
        if not ember_browser.WEBENGINE_OK:
            err = getattr(ember_browser, "WEBENGINE_ERROR", "") or "PyQt6-WebEngine not installed"
            if getattr(sys, "frozen", False):
                # A packaged-app user has no Python/pip at all - a pip command is unactionable.
                # This build is simply missing/broken WebEngine; the fix is a fresh download.
                self._add_bubble("error",
                    "Ember Browser can't load its web engine in this build.\n\n"
                    f"Reason: {err}\n\n"
                    "This copy of Ember is missing a required component. Please redownload the "
                    "latest version — Settings → gear → Check for updates, or from the website.")
            else:
                self._add_bubble("system",
                    "Ember Browser can't load the web engine (Qt WebEngine).\n\n"
                    f"Reason: {err}\n\n"
                    "Fix — install it into the SAME environment Ember runs from, matching your "
                    "PyQt6 version:\n"
                    "  uv pip install --reinstall PyQt6 PyQt6-WebEngine\n\n"
                    "Then fully quit and reopen Ember. (Compare versions with: "
                    "uv pip show PyQt6 PyQt6-WebEngine — they must match.)")
            return
        try:
            if getattr(self, "_browser_win", None) is None:
                self._browser_win = ember_browser.EmberBrowser(self.settings)
            self._browser_win.show()
            self._browser_win.raise_()
            self._browser_win.activateWindow()
        except Exception as e:
            self._add_bubble("error", f"Could not open Ember Browser: {e}")

    def _scan_folder(self):
        """Scan a folder for malware (Command Center -> Scan a folder). The Settings -> Security
        tab and the Antivirus app have their own copies of this same action for when they're
        already open; this one lets the Command Center token reach it directly."""
        import antivirus
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if not folder:
            return
        self._set_status("Scanning…")
        r = antivirus.scan_directory(folder, deep=False)
        self._set_status("Ready")
        if not r.get("ok"):
            QMessageBox.warning(self, "Scan", r.get("error", "scan failed"))
            return
        flagged = r.get("flagged", [])
        detail = "\n".join(f"{f['verdict']}: {f['path']}" for f in flagged[:20]) or "Nothing suspicious found."
        QMessageBox.information(
            self, "Scan complete",
            f"Scanned {r.get('scanned', 0)} files — flagged {r.get('flagged_count', 0)}.\n\n{detail}")

    def _run_in_sandbox_ui(self):
        """Run a chosen file/app inside the strongest available sandbox (Command Center ->
        Sandbox). Mirrors the copy of this action inside Settings -> Security for when that
        dialog is already open."""
        import antivirus
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file/app to run safely in the sandbox")
        if not path:
            return
        self._set_status("Running in sandbox…")
        r = antivirus.run_in_sandbox(path)
        self._set_status("Ready")
        if r.get("refused"):
            QMessageBox.warning(self, "Sandbox", r.get("message", "Refused: file is malicious."))
            return
        if not r.get("ok"):
            QMessageBox.warning(self, "Sandbox", r.get("error", "Could not run in the sandbox."))
            return
        parts = [f"Ran via: {r.get('method', 'sandbox')}",
                 f"Result: {r.get('verdict_hint', '?')}",
                 f"Exit code: {r.get('exit_code')}"]
        out = (r.get("stdout") or "").strip()
        err = (r.get("stderr") or "").strip()
        if out:
            parts.append("\nOutput:\n" + out[:800])
        if err:
            parts.append("\nErrors:\n" + err[:400])
        QMessageBox.information(self, "Sandbox result", "\n".join(parts))

    def _start_remote_control(self):
        """Start Ember Link (phone control) and open its panel (local URL/PIN + optional
        connect-from-anywhere tunnel)."""
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
        self._add_bubble("system", f"Ember Link is live at **{url}** (PIN **{pin}**).")
        RemoteLinkDialog(self).exec()

    def _autostart_download_protection(self):
        """Start real-time download protection (Downloads folder malware watcher) in the
        background. Best-effort and failure-silent so it never blocks the app."""
        try:
            import download_guard
            # Register the AI second-opinion judge app-wide so the download guard AND the
            # block-on-open gate can AI-scan a file even if the Antivirus window was never opened
            # (previously the judge was only wired inside AntivirusDialog).
            self._register_av_ai_judge()
            # Surface threats/cautionary downloads (the watcher used to only log them silently).
            download_guard.set_on_threat(lambda evt: self._bridge.download_alert.emit(evt))
            r = download_guard.start()
            if r.get("ok"):
                print(f"[Download protection on: watching {r.get('folder')}]")
            else:
                print(f"[Download protection failed: {r.get('error')}]")
        except Exception as e:
            print(f"[Download protection autostart failed: {e}]")

    def _register_av_ai_judge(self):
        """Register the AI malware second-opinion judge on the antivirus module, using the app's
        configured model/keys, so download-scan and block-on-open can AI-scan any file."""
        try:
            import antivirus, ai_detect
            antivirus.set_ai_judge(lambda items: ai_detect.judge_harmful(items, self.settings))
        except Exception:
            pass

    def _surface_window(self):
        """Bring the Ember window to the FRONT, over whatever the user is looking at — so a
        download alert is impossible to miss (it used to appear behind other apps)."""
        try:
            if self.isMinimized():
                self.showNormal()
            self.show()
            st = self.windowState()
            self.setWindowState((st & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
            self.raise_()
            self.activateWindow()
            wh = self.windowHandle()
            if wh is not None:
                wh.requestActivate()
        except Exception:
            pass

    def _on_download_alert(self, evt: dict):
        """A downloaded file was flagged (or is a cautionary executable). Bring Ember to the
        front and pop a blocking review dialog — Scan with AI / Quarantine / Delete / Keep — so
        it can't be silently ignored. Runs on the UI thread (marshalled via the bridge)."""
        try:
            name = evt.get("name", "a file")
            detail = evt.get("detail", "")
            level = evt.get("level", "caution")
            path = evt.get("path")
            tray = getattr(self, "_tray", None)
            if tray is not None:
                try:
                    tray.showMessage(
                        ("⚠ Threat in your download" if level == "threat" else "Download warning"),
                        f"{name}\n{detail}", QSystemTrayIcon.MessageIcon.Warning, 6000)
                except Exception:
                    pass
            self._add_bubble("system" if level == "caution" else "error",
                             f"🛡 Download protection: **{name}** — {detail}")
            if not (path and Path(path).exists()):
                return
            # Make sure the judge is registered (covers the case where download protection was
            # started from Settings before autostart ran), then surface Ember + show the dialog.
            self._register_av_ai_judge()
            self._surface_window()
            dlg = DownloadGuardDialog(self, name=name, detail=detail, level=level,
                                      path=path, settings=self.settings)
            dlg.exec()
        except Exception:
            traceback.print_exc()

    def _autostart_fileless_protection(self):
        """Start always-on fileless / behavioral malware protection (the background
        process monitor) at launch. Best-effort and failure-silent."""
        try:
            import fileless_guard
            r = fileless_guard.start()
            if r.get("ok"):
                n = r.get("initial_threats", 0)
                print(f"[Fileless protection on: active{' — ' + str(n) + ' threat(s) on initial sweep' if n else ''}]")
                if n:
                    self._add_bubble(
                        "system",
                        f"🛡️ Real-time fileless protection flagged **{n}** running "
                        "process(es) on startup. Open Settings → Security to review.")
            else:
                print(f"[Fileless protection failed: {r.get('error')}]")
        except Exception as e:
            print(f"[Fileless protection autostart failed: {e}]")

    def _autostart_security_center(self):
        """Bring up the unified always-on Security Center (continuous active scanning
        of processes, files, network and persistence). Best-effort, failure-silent."""
        try:
            import security_center
            r = security_center.start()
            if r.get("ok"):
                print("[Security Center active: continuous scanning of processes, "
                      "files, network & persistence]")
            else:
                print(f"[Security Center failed: {r.get('error')}]")
        except Exception as e:
            print(f"[Security Center autostart failed: {e}]")

    def _run_saved_agent(self, name):
        """Runner the scheduler calls to actually run a saved agent: launch it as a
        scoped sub-agent on the live agent, then push a summary to connected channels."""
        try:
            import agents, agent as agent_module
            ag = getattr(self, "agent", None)
            req = agents.build_run_request(name, all_tool_names=list(agent_module.TOOL_DISPATCH))
            if not req.get("ok"):
                return req
            if ag is None or not hasattr(ag, "_spawn"):
                return {"ok": False, "error": "no live agent to run with"}
            res = ag._spawn(req["instructions"], mode=req["run_mode"],
                            allowed_tools=req.get("allowed_tools"), label=name)
            try:
                import integrations
                if integrations.is_configured():
                    integrations.notify(f"🤖 Agent '{name}' ran: "
                                        f"{str(res.get('summary','done'))[:280]}")
            except Exception:
                pass
            return res
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _autostart_agent_scheduler(self):
        """Start the background agent scheduler and register the live runner."""
        try:
            import agent_scheduler
            agent_scheduler.set_runner(self._run_saved_agent)
            r = agent_scheduler.start()
            print("[Agent scheduler started]" if r.get("ok")
                  else f"[Agent scheduler failed: {r.get('error')}]")
        except Exception as e:
            print(f"[Agent scheduler autostart failed: {e}]")

    def _autostart_wake_word(self):
        """Start always-on 'Hey Ember' wake-word listening. Failure-visible: if the mic
        can't be opened, tell the user once (usually a macOS permission prompt) instead of
        leaving 'Hey Ember' silently dead."""
        try:
            import wake_word
            res = wake_word.start(on_wake=lambda: self._bridge.wake_detected.emit())
            if res.get("ok") and wake_word.is_running():
                print("[Wake word on: listening for 'Hey Ember']")
                return
            # start() now decides mic availability synchronously and hands back the real reason.
            detail = res.get("error") or wake_word.last_error() or "microphone unavailable"
            print(f"[Wake word: {detail}]")
            if not self.settings.get("_warned_mic_perm"):
                self.settings["_warned_mic_perm"] = True
                self._add_bubble("system", "🎙️ “Hey Ember” couldn’t start — " + detail)
        except Exception as e:
            print(f"[Wake word autostart failed: {e}]")

    def _ensure_orb(self):
        """The floating Siri orb (a top-level, focus-preserving overlay)."""
        if getattr(self, "_orb", None) is None:
            try:
                from siri_glow import SiriOrb
                self._orb = SiriOrb()
                try:
                    import audio_level
                    self._orb.set_level_provider(audio_level.get_level)  # pulse with the voice
                except Exception:
                    pass
            except Exception:
                self._orb = None
        return getattr(self, "_orb", None)

    def _apply_offline_mode(self):
        """Publish the Offline Mode flag so the agent + voice honour it."""
        try:
            import offline
            offline.set_offline(bool(self.settings.get("offline_mode", False)))
        except Exception:
            pass

    def _toggle_offline_mode(self, state):
        on = bool(state)
        self.settings["offline_mode"] = on
        self._apply_offline_mode()
        if on:
            note = ("🔌 Offline Mode ON — Ember won't use the internet. Local tools (files, "
                    "shell, screen, system info) work; web/search/email and cloud AI won't. "
                    "For a fully-offline brain, switch the model to Ollama (Local AI).")
            prov = self.settings.get("provider") or model_catalog.provider_for(
                self.settings.get("model_id") or self.settings.get("gemini_model") or "")
            if prov != "ollama":
                note += "\n⚠ Your current model is a cloud model — it can't reach the network in Offline Mode."
            self._add_bubble("system", note)
            self._set_status("Offline Mode on")
        else:
            self._add_bubble("system", "Offline Mode off — internet tools are available again.")

    def _apply_mouse_options(self):
        """Push the saved pointer appearance, speed, and motion options on launch."""
        try:
            import human_mouse
            human_mouse.set_options(
                enabled=bool(self.settings.get("mouse_humanize", True)),
                speed=max(0.25, min(3.0, float(self.settings.get("mouse_speed", 1.0)))),
                show_pointer=bool(self.settings.get("show_ember_pointer", True)))
            self._set_ember_pointer_enabled(bool(
                self.settings.get("show_ember_pointer", True)))
        except Exception:
            pass

    def _install_ember_pointer(self):
        """Attach the GUI overlay to the input driver without coupling either module."""
        try:
            import human_mouse
            from ember_pointer import EmberPointerOverlay
            self._ember_pointer = EmberPointerOverlay()
            self._ember_pointer.set_enabled(bool(
                self.settings.get("show_ember_pointer", True)))
            human_mouse.set_pointer_hook(self._ember_pointer.request)
        except Exception as e:
            self._ember_pointer = None
            print(f"[Ember pointer unavailable: {e}]")

    def _set_ember_pointer_enabled(self, enabled: bool):
        pointer = getattr(self, "_ember_pointer", None)
        if pointer is not None:
            pointer.set_enabled(bool(enabled))

    def _apply_tts_config(self):
        """Push the read-aloud engine settings to the voice module."""
        try:
            import voice
            voice.set_tts_config({
                "tts_engine": self.settings.get("tts_engine", "system"),
                "edge_tts_voice": self.settings.get("edge_tts_voice", "en-US-AriaNeural"),
                "gemini_api_key": self.settings.get("gemini_api_key", ""),
                "gemini_tts_voice": self.settings.get("gemini_tts_voice", "Kore"),
                "soundtools_api_key": self.settings.get("soundtools_api_key", ""),
                "soundtools_url": self.settings.get("soundtools_url", ""),
                "soundtools_voice": self.settings.get("soundtools_voice", ""),
            })
        except Exception:
            pass
        # If they picked the free Edge neural voice but the package isn't installed, it would
        # silently fall back to the device default — tell them how to enable it (once).
        if (self.settings.get("tts_engine") == "edge"
                and not getattr(self, "_edge_warned", False)):
            try:
                import edge_tts  # noqa: F401
            except Exception:
                self._edge_warned = True
                self._add_bubble("system",
                    "🔊 The free Edge neural voice needs a small package that isn't installed, so "
                    "Ember is using your device's default voice for now. To enable it, run:\n"
                    "    pip install edge-tts\nthen reopen Ember. (New installs include it "
                    "automatically.)")

    def _orb_speak(self, text: str):
        """Always speak (orb turns are voice interactions), regardless of the TTS setting."""
        try:
            import voice
            spoken = re.sub(r"\[[^\]]+\]", "", (text or "").replace("*", "").replace("`", "")).strip()
            if spoken:
                voice.speak(spoken[:700])
        except Exception:
            pass

    def _on_wake_word(self):
        """'Hey Ember' was heard. Show a small FLOATING ORB over whatever app you're using —
        DON'T switch apps or bring the whole Ember window forward — then listen, and caption +
        speak the reply on the orb (like the macOS Siri orb)."""
        # Barge-in: saying "Hey Ember" interrupts whatever it's currently saying.
        try:
            import voice
            voice.stop_speaking()
        except Exception:
            pass
        if not self.agent:
            self._voice_begin("Add your API key to start.")
            self._voice_state("speaking")
            self._orb_speak("Add your API key to start.")
            self._voice_end(delay_ms=3500)
            return
        if self._listening:
            return   # already mid-turn
        # Start a hands-free CONVERSATION: after this first turn you can keep chatting
        # without repeating "Hey Ember" until you go quiet or say "stop".
        self._orb_conversation = True
        self._orb_active = True
        self._voice_begin("Listening…")
        self._start_orb_turn()

    # ---- voice visual: the glow AROUND Ember (default) or the floating orb ----
    def _wake_visual(self) -> str:
        return (self.settings.get("wake_visual") or "glow").lower()

    def _voice_begin(self, caption: str = "Listening…"):
        if self._wake_visual() == "orb":
            orb = self._ensure_orb()
            if orb:
                orb.popup("listening")
                orb.set_caption(caption)
        else:
            self._set_siri("listening")
            self._set_status(caption)

    def _voice_state(self, state: str, caption: str | None = None):
        if self._wake_visual() == "orb":
            orb = self._ensure_orb()
            if orb:
                orb.set_state(state)
                if caption is not None:
                    orb.set_caption(caption)
        else:
            self._set_siri(state)
            if caption:
                self._set_status(caption)

    def _voice_end(self, caption: str = "", delay_ms: int = 1600):
        if self._wake_visual() == "orb":
            orb = self._ensure_orb()
            if orb:
                if caption:
                    orb.set_state("speaking")
                    orb.set_caption(caption)
                orb.dismiss_after(delay_ms)
        else:
            if caption:
                self._set_status(caption)
            self._set_siri(None)

    def _start_orb_turn(self, follow_up: bool = False):
        """Listen for one voice turn and send it to the agent. The reply shows on the chosen
        wake visual (glow around Ember, or the floating orb) — never opening/focusing the main
        window. In a conversation (follow_up=True) a shorter silence window ends it naturally."""
        try:
            import voice
            ok, detail = voice.mic_available()
        except Exception:
            ok, detail = True, ""
        if not ok:
            self._voice_end(detail or "Microphone unavailable", delay_ms=4000)
            self._orb_conversation = False
            self._orb_active = False
            self._listening = False
            self._resume_wake_word()
            return
        self._listening = True
        self._listening_mode = "orb"
        try:
            import wake_word
            wake_word.pause()   # we own the mic for the whole conversation
        except Exception:
            pass
        # First turn waits a bit longer; follow-ups end sooner if you don't say anything.
        listen_timeout = 7.0 if follow_up else 10.0

        def _cb(text, err):
            self._bridge.transcript.emit(text or "", err or "")
        try:
            import voice
            started = False
            try:
                import audio_level
                # Metered capture publishes the live mic level so the glow/orb pulses with you.
                started = audio_level.listen_metered(_cb, phrase_timeout=8.0,
                                                     listen_timeout=listen_timeout)
            except Exception:
                started = False
            if not started:
                voice.listen_once(_cb, phrase_timeout=8.0, listen_timeout=listen_timeout)
        except Exception:
            self._listening = False
            self._end_orb_conversation()

    def _handle_orb_transcript(self, text: str, err: str):
        """A voice turn came back. In conversation mode, keep going turn after turn (no repeated
        "Hey Ember") until silence or a stop phrase."""
        text = _fix_assistant_name((text or "").strip())   # "amber" -> "Ember"
        convo = getattr(self, "_orb_conversation", False)
        # Nothing heard -> in a conversation that means you're done; otherwise prompt again.
        if err or not text:
            if convo:
                self._end_orb_conversation()
            else:
                self._resume_wake_word()
                self._voice_end("Didn't catch that — say “Hey Ember” again.", delay_ms=3000)
                self._orb_active = False
            return
        if convo and _is_stop_phrase(text):
            self._end_orb_conversation(spoken="Okay, bye.")
            return
        if not self.agent:
            self._end_orb_conversation()
            return
        self._voice_state("thinking", "Thinking…")
        # Record the turn + send. The window stays where it is; reply comes back via events.
        self._append_history("user", text)
        try:
            self.agent.send_user_message(self._agent_contextual_text(text))
        except Exception as e:
            self._voice_end(f"Error: {e}", delay_ms=4000)
            self._end_orb_conversation()

    def _resume_wake_word(self):
        try:
            import wake_word
            wake_word.resume()
        except Exception:
            pass

    def _continue_orb_conversation(self):
        """Listen for the next turn once Ember has FINISHED speaking (so the mic doesn't catch
        its own voice). Called after each reply while a conversation is active."""
        if not getattr(self, "_orb_conversation", False):
            return
        try:
            import voice
            if voice.is_speaking():
                QTimer.singleShot(220, self._continue_orb_conversation)
                return
        except Exception:
            pass
        self._orb_active = True
        self._voice_state("listening", "Listening…")
        self._start_orb_turn(follow_up=True)

    def _end_orb_conversation(self, spoken: str = ""):
        """Wrap up the hands-free conversation: optionally say a closer, fade the visual, and
        hand the mic back to the always-on wake word."""
        self._orb_conversation = False
        self._orb_active = False
        self._listening = False
        if spoken:
            self._orb_speak(spoken)
        self._voice_end(spoken, delay_ms=2600 if spoken else 1600)
        self._resume_wake_word()

    # --- Siri-style glow ------------------------------------------------------
    def _ensure_siri(self):
        if getattr(self, "_siri", None) is None:
            try:
                from siri_glow import SiriGlow
                self._siri = SiriGlow(self._root)
                try:
                    import audio_level
                    self._siri.set_level_provider(audio_level.get_level)  # band moves with the voice
                except Exception:
                    pass
            except Exception:
                self._siri = None
        if getattr(self, "_siri", None) is not None:
            try:
                self._siri.cover()
            except Exception:
                pass
        return getattr(self, "_siri", None)

    def _set_siri(self, state):
        """Drive the main-window edge glow: state in {'listening','thinking','speaking'} or None.
        Suppressed during a voice turn ONLY when the user chose the floating ORB as the wake
        visual (then the orb is the visual and we must not touch the main window). In glow mode
        this is exactly the wake visual, so it runs."""
        if (state and self._wake_visual() == "orb"
                and (getattr(self, "_orb_active", False) or getattr(self, "_orb_conversation", False))):
            return
        try:
            if not self.settings.get("glow_animation", True):
                return
            glow = self._ensure_siri()
            if glow is None:
                return
            if state:
                glow.start(state)
            else:
                glow.stop()
        except Exception:
            pass

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
            "Open it on a phone on the same Wi-Fi to control this computer.",
        )

    def _start_mcp_bridge(self, announce: bool = True):
        """Start Ember's MCP bridge so external MCP clients (Claude Desktop, Cursor) can drive
        Ember's tools. Loopback-only + token-secured. Returns the status dict."""
        try:
            import ember_bridge
            r = ember_bridge.start(
                port=int(self.settings.get("mcp_bridge_port", 8770) or 8770),
                allow_high_risk=bool(self.settings.get("mcp_bridge_allow_high_risk", False)),
            )
        except Exception as e:
            if announce:
                QMessageBox.warning(self, "MCP bridge", f"Could not start: {e}")
            return {"ok": False, "error": str(e)}
        if not r.get("ok"):
            if announce:
                QMessageBox.warning(self, "MCP bridge", r.get("error", "Failed to start."))
            return r
        if announce and not r.get("already_running"):
            self._add_bubble(
                "system",
                f"🔌 Ember MCP bridge is live at **{r.get('url')}**. Point Claude Desktop / "
                "Cursor at `ember_mcp_server.py` (see docs/MCP.md) to control Ember over MCP.",
            )
        return r

    def _autostart_mcp_bridge(self):
        """Bring the MCP bridge up at launch (opt-in). Best-effort, failure-silent."""
        r = self._start_mcp_bridge(announce=False)
        if not r.get("ok"):
            print(f"[Ember MCP bridge autostart failed: {r.get('error')}]")

    # ------------------------------------------------------------------ updates
    def _report_update_result(self):
        try:
            import updater
            result = updater.consume_update_result()
        except Exception:
            return
        if not result:
            return
        if result.get("ok"):
            self._add_bubble("system", "✓ Ember updated successfully. You're on the latest build.")
            self._set_status("Update installed")
            updater.cleanup_backup()
        else:
            self._add_bubble("error", result.get("message") or
                             "The update failed, so Ember restored the previous version.")
            self._set_status("Update rolled back safely")

    def _on_update_progress(self, percent: int):
        ver = (self._pending_update or {}).get("version", "")
        suffix = f" {ver}" if ver else ""
        self._set_status(f"Downloading Ember{suffix}… {max(0, min(100, percent))}%")

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
        """If Ember runs from a git checkout (source/dev), fetch + fast-forward to the latest
        commit on EVERY launch so it always has the newest features without a manual 'git pull'.
        Background + silent; only fast-forwards (never clobbers local edits). When it actually
        advances, it offers a one-click restart so the new code goes live."""
        if getattr(sys, "frozen", False):
            return
        import shutil
        base = _base_dir()
        if not (base / ".git").exists() or not shutil.which("git"):
            return

        def _head():
            try:
                import subprocess
                r = subprocess.run(["git", "-C", str(base), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, timeout=20)
                return (r.stdout or "").strip()
            except Exception:
                return ""

        def _work():
            try:
                import subprocess
                before = _head()
                # Fetch first so the fast-forward sees the very latest remote commits.
                subprocess.run(["git", "-C", str(base), "fetch", "--quiet"],
                               capture_output=True, text=True, timeout=90)
                subprocess.run(["git", "-C", str(base), "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=120)
                after = _head()
                if before and after and before != after:
                    self._bridge.source_updated.emit(after[:8])
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def _on_source_updated(self, rev: str):
        """A source checkout fast-forwarded to newer code on launch. Offer to restart now so
        the new features take effect (Python source can't hot-reload). Asked at most once."""
        if getattr(self, "_source_update_prompted", False):
            return
        self._source_update_prompted = True
        box = QMessageBox(self)
        box.setWindowTitle("Ember updated")
        box.setText(f"Ember fetched the latest version ({rev}).")
        box.setInformativeText("Restart now to use the new features?")
        restart = box.addButton("Restart now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is restart:
            if not self._relaunch_source():
                QMessageBox.information(self, "Restart needed",
                                       "Please quit and reopen Ember to load the update.")

    def _relaunch_source(self) -> bool:
        """Relaunch a source/dev checkout safely: a detached helper waits for THIS process to
        exit (which frees the single-instance port) and then starts Ember again."""
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        base = _base_dir()
        pid = os.getpid()
        try:
            if sys.platform.startswith("win"):
                launcher = base / "run.bat"
                target = (f'"{launcher}"' if launcher.exists()
                          else f'"{sys.executable}" "{base / "main.py"}"')
                bat = ("@echo off\r\n:w\r\n"
                       f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL '
                       f'&& (timeout /t 1 /nobreak >NUL & goto w)\r\n'
                       "timeout /t 1 /nobreak >NUL\r\n"
                       f'cd /d "{base}"\r\n'
                       f'start "" {target}\r\n'
                       'del "%~f0"\r\n')
                hp = Path(tempfile.mkdtemp(prefix="ember_relaunch_")) / "relaunch.bat"
                hp.write_text(bat, encoding="utf-8")
                DETACHED = 0x00000008 | 0x00000200 | 0x08000000
                subprocess.Popen(["cmd", "/c", str(hp)], creationflags=DETACHED, close_fds=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import shlex
                launcher = base / ("Ember.command" if sys.platform == "darwin" else "run.sh")
                if launcher.exists():
                    run = f"exec {shlex.quote(str(launcher))}"
                else:
                    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(base / 'main.py'))}"
                sh = ("#!/bin/bash\n"
                      f"while /bin/kill -0 {pid} 2>/dev/null; do sleep 0.4; done\n"
                      "sleep 0.5\n"
                      f"cd {shlex.quote(str(base))}\n"
                      f"{run}\n")
                hp = Path(tempfile.mkdtemp(prefix="ember_relaunch_")) / "relaunch.sh"
                hp.write_text(sh)
                hp.chmod(0o755)
                subprocess.Popen(["/bin/bash", str(hp)], start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            from PyQt6.QtWidgets import QApplication
            QTimer.singleShot(200, QApplication.quit)
            return True
        except Exception:
            traceback.print_exc()
            return False

    def _on_update_available(self, manifest: dict):
        """A newer release is available — show the standard update prompt (Skip / Install on Quit /
        Install & Relaunch) with an 'auto-update in future' checkbox."""
        import version
        self._pending_update = manifest
        ver = manifest.get("version", "?")
        # Respect a previously-skipped version — don't nag about the same one again.
        if self.settings.get("skipped_update_version") == ver:
            self._set_status(f"Update {ver} available — type /update")
            return
        notes = (manifest.get("notes") or "").strip()
        try:
            dlg = UpdateDialog(ver, version.__version__, notes,
                               bool(self.settings.get("auto_update", True)), self)
            dlg.exec()
            choice = dlg.choice
            # Persist the auto-update preference from the checkbox.
            self.settings["auto_update"] = bool(dlg.auto_update)
            save_settings(self.settings)
        except Exception:
            choice = "relaunch" if self.settings.get("auto_update", True) else None
        if choice == "relaunch":
            self._set_status(f"Updating to {ver}…")
            QTimer.singleShot(150, lambda: self._start_update(auto=True))
        elif choice == "on_quit":
            self._install_on_quit = True
            self._add_bubble("system", f"✅ Ember {ver} will install when you quit Ember.")
            self._set_status(f"Ember {ver} will install on quit")
        elif choice == "skip":
            self.settings["skipped_update_version"] = ver
            save_settings(self.settings)
            self._set_status("Ready")
        else:
            self._set_status(f"Update {ver} available — type /update")

    def _update_now(self):
        """Manual 'check for updates' that is NEVER silent — it always tells the user what
        happened. Handles BOTH a git/source checkout (fetch + fast-forward) and an installed
        app (release manifest). This is the fix for 'auto-update doesn't work': the automatic
        check is best-effort/quiet, but this button gives a clear, actionable result."""
        import shutil
        base = _base_dir()
        frozen = getattr(sys, "frozen", False)
        if not frozen and (base / ".git").exists() and shutil.which("git"):
            self._add_bubble("system", "Checking GitHub for a newer version…")
            self._set_status("Checking for updates…")
            threading.Thread(target=self._git_update_verbose, args=(str(base),), daemon=True).start()
            return
        if frozen:
            try:
                import updater, version
                if not updater.can_self_update():
                    site = version.site_url()
                    dl = version.latest_download_url()
                    self._add_bubble("system",
                        "This installed build can't update itself — usually because macOS is "
                        "running it from a read-only/quarantined location (Gatekeeper), or no "
                        "release is published yet.\n\n"
                        f"• **Newest build:** {dl}\n"
                        f"• **Website:** {site}\n\n"
                        "Tip: for updates that ‘just work’, run Ember from source — double-click "
                        "**RUN_FROM_SOURCE.command** in the repo. It pulls the latest on every "
                        "launch, so you never have to download again.")
                    return
            except Exception as e:
                self._add_bubble("error", f"Updater unavailable: {e}")
                return
            self._add_bubble("system", "Checking for the latest Ember release…")
            self._set_status("Checking for updates…")

            def _work():
                import version
                try:
                    import updater
                    # raise_on_error so a blocked/failed fetch isn't misreported as "up to date".
                    manifest = updater.check_for_update(raise_on_error=True)
                except Exception as e:
                    try:
                        site = version.site_url()
                    except Exception:
                        site = "the Ember website"
                    self._bridge.notice.emit(
                        f"Couldn't reach the update server ({type(e).__name__}: {e}). "
                        f"You can download the latest build directly from {site}.")
                    return
                if manifest:
                    QTimer.singleShot(0, lambda: self._on_update_available(manifest))
                else:
                    self._bridge.notice.emit(f"✓ Ember is up to date (v{version.__version__}).")
            threading.Thread(target=_work, daemon=True).start()
            return
        # Plain download (no .git, not a frozen app) — can't self-update; point them to the site.
        try:
            import version
            site = version.site_url()
        except Exception:
            site = "the Ember website"
        self._add_bubble("system",
            "This copy of Ember isn't a git checkout or an installed app, so it can't update "
            f"itself. Get the latest build from {site} — or, for updates that ‘just work’, run "
            "from source: double-click **RUN_FROM_SOURCE.command** (it pulls the latest on every "
            "launch).")

    def _git_update_verbose(self, base: str):
        """Fetch + fast-forward a source checkout and report the exact outcome (up to date /
        updated / why it couldn't). Runs on a worker thread; posts results via the bridge."""
        import subprocess

        def run(*args, timeout=120):
            return subprocess.run(["git", "-C", base, *args],
                                  capture_output=True, text=True, timeout=timeout)
        try:
            before = run("rev-parse", "HEAD").stdout.strip()
            branch = (run("rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip() or "?"
            run("fetch", "--quiet", timeout=90)
            up = run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
            if up.returncode != 0:
                self._bridge.notice.emit(
                    f"Update: branch '{branch}' has no upstream to pull from. Set one with:\n"
                    f"git branch --set-upstream-to=origin/{branch}")
                return
            counts = (run("rev-list", "--left-right", "--count", "HEAD...@{u}").stdout or "").split()
            behind = counts[1] if len(counts) == 2 else "?"
            if behind == "0":
                self._bridge.notice.emit(f"✓ Ember is up to date ({branch} @ {before[:8]}).")
                return
            pull = run("pull", "--ff-only", timeout=120)
            after = run("rev-parse", "HEAD").stdout.strip()
            if before and after and before != after:
                self._bridge.source_updated.emit(after[:8])
            else:
                err = (pull.stderr or pull.stdout or "").strip()[:300]
                self._bridge.notice.emit(
                    f"Ember is {behind} commit(s) behind on '{branch}' but couldn't fast-forward"
                    + (f":\n{err}" if err else ".")
                    + "\nThis is usually local changes or a diverged branch — commit/stash, or "
                    "run: git pull --ff-only")
        except Exception as e:
            self._bridge.notice.emit(f"Update check failed: {type(e).__name__}: {e}")

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
                staged = updater.download_and_stage(
                    manifest, progress=lambda pct: self._bridge.update_progress.emit(pct))
                updater.apply_update_and_relaunch(staged)
                _post(lambda: self._add_bubble("system", "Update ready — restarting Ember…"))
                _post(lambda: QApplication.instance().quit())
            except Exception as e:
                try:
                    import version
                    manual = version.latest_download_url()
                except Exception:
                    manual = "the Ember website"
                message = (f"Update failed: {type(e).__name__}: {e}\n\n"
                           f"Your current version is untouched. Manual download: {manual}")
                _post(lambda m=message: self._add_bubble("error", m))
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
            # CANCEL any in-flight turn first. Without this, resetting mid-task (e.g. a long
            # `ollama pull`) leaves that turn running on the single-turn worker, so new messages
            # queue behind it and the assistant looks frozen / "100x slower". stop() also clears
            # the queued turns; reset() then rebuilds a clean chat.
            try:
                self.agent.stop()
            except Exception:
                pass
            self.agent.reset()
        # Reset the UI turn state so the next message shows a thinking bubble + reply normally.
        self._hide_typing_indicator()
        self._streaming_bubble_label = None
        self._streaming_buffer = ""
        self._orb_active = False
        self._orb_conversation = False
        self._listening = False
        try:
            self.send_btn.setEnabled(True)
        except Exception:
            pass
        self._set_siri(None)
        chat = self._active_chat()
        chat["messages"] = []
        chat["updated"] = int(time.time())
        save_chat_history(self.chat_history)
        self._refresh_history_sidebar()
        self._load_active_chat_into_view()
        self._add_bubble("system", "Conversation reset.")
        self._set_status(f"Ready ({self.settings.get('model_id') or self.settings.get('gemini_model')})")

    def _open_settings(self, only_tab: str = None):
        old_hotkey = (self.settings.get("hotkey") or "ctrl+shift+space").lower()
        # Snapshot the keys the agent / glass effect actually depend on, so we only rebuild
        # them when relevant — a font or appearance tweak shouldn't drop conversation state.
        agent_keys = ("model_id", "provider", "gemini_api_key", "gemini_api_key_secondary",
                      "gemini_api_key_3", "gemini_api_key_4",
                      "anthropic_api_key", "anthropic_model", "gemini_model",
                      "openai_api_key", "openai_model", "openai_base_url", "openai_provider_label",
                      "auto_screenshot", "request_timeout_seconds", "dual_api_failover")
        glass_keys = ("liquid_glass", "glass_opacity", "accent_color")
        old_agent = {k: self.settings.get(k) for k in agent_keys}
        old_glass = {k: self.settings.get(k) for k in glass_keys}
        old_ptt = {k: self.settings.get(k) for k in ("push_to_talk", "push_to_talk_key")}
        try:
            dlg = SettingsDialog(self.settings, self, automation_engine=self._automation,
                                 only_tab=only_tab)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                with open(_data_dir() / "ember-crash.log", "a", encoding="utf-8") as f:
                    f.write("\n--- Settings open failed ---\n" + traceback.format_exc())
            except Exception:
                pass
            QMessageBox.critical(self, "Settings",
                f"Couldn't open Settings: {type(e).__name__}: {e}\n\n"
                f"A crash log was saved to:\n{_data_dir() / 'ember-crash.log'}")
            return
        old_mcp = {k: self.settings.get(k) for k in
                   ("mcp_bridge_enabled", "mcp_bridge_port", "mcp_bridge_allow_high_risk")}
        if dlg.exec():
            self.settings = dlg.get_settings()
            save_settings(self.settings)
            if self.agent is None or any(self.settings.get(k) != old_agent[k] for k in agent_keys):
                self._init_agent()
            # Apply MCP bridge changes live. Keep an already-correct bridge alive so saving the
            # dialog does not rotate its token underneath a running ChatGPT MCP endpoint.
            if any(self.settings.get(k) != old_mcp[k] for k in old_mcp):
                try:
                    import ember_bridge
                    if self.settings.get("mcp_bridge_enabled"):
                        current = ember_bridge.status()
                        desired_port = int(self.settings.get("mcp_bridge_port", 8770) or 8770)
                        desired_high_risk = bool(
                            self.settings.get("mcp_bridge_allow_high_risk", False))
                        already_matches = (
                            current.get("running") and current.get("port") == desired_port and
                            current.get("allow_high_risk") == desired_high_risk)
                        if not already_matches:
                            ember_bridge.stop()
                            self._start_mcp_bridge(announce=True)
                    else:
                        ember_bridge.stop()
                except Exception as e:
                    print(f"[MCP bridge apply failed: {e}]")
            if hasattr(self, "_automation"):
                self._automation.enabled = bool(self.settings.get("automation_enabled", True))
                self._automation.auto_confirm_popups = bool(self.settings.get("auto_confirm_popups", False))
            # Re-apply appearance live (no restart needed). Always re-theme so the accent
            # colour AND chat text size take effect immediately (both used to "do nothing").
            self._apply_glow()
            self._apply_glass_effect()
            self._apply_tts_config()   # read-aloud engine may have changed
            self._apply_mouse_options()
            if hasattr(self, "main_mouse_toggle"):
                self.main_mouse_toggle.blockSignals(True)
                self.main_mouse_toggle.setChecked(bool(self.settings.get("mouse_humanize", True)))
                self.main_mouse_toggle.blockSignals(False)
                speed = max(0.25, min(3.0, float(self.settings.get("mouse_speed", 1.0))))
                self.main_mouse_speed.blockSignals(True)
                self.main_mouse_speed.setValue(int(round(speed * 100)))
                self.main_mouse_speed.blockSignals(False)
                self.main_mouse_speed_value.setText(f"{speed:.2f}×")
                self.main_ember_pointer_toggle.blockSignals(True)
                self.main_ember_pointer_toggle.setChecked(bool(
                    self.settings.get("show_ember_pointer", True)))
                self.main_ember_pointer_toggle.blockSignals(False)
            try:
                import agents as _ag
                run_mode = _ag.get_run_mode()
                for i in range(self.main_mode_combo.count()):
                    if self.main_mode_combo.itemData(i) == run_mode:
                        self.main_mode_combo.blockSignals(True)
                        self.main_mode_combo.setCurrentIndex(i)
                        self.main_mode_combo.blockSignals(False)
                        break
            except Exception:
                pass
            new_hotkey = (self.settings.get("hotkey") or "ctrl+shift+space").lower()
            if new_hotkey != old_hotkey:
                self._install_hotkey()
                self._add_bubble("system", f"Hotkey changed to **{new_hotkey}**. Status: {self._hotkey_status}")
            # Re-arm push-to-talk if its enable/key changed.
            if (self.settings.get("push_to_talk") != old_ptt.get("push_to_talk")
                    or self.settings.get("push_to_talk_key") != old_ptt.get("push_to_talk_key")):
                self._install_ptt()
                if self.settings.get("push_to_talk"):
                    self._add_bubble("system",
                        f"Push-to-talk: hold **{self.settings.get('push_to_talk_key', 'f9')}** to "
                        f"talk. Status: {self._ptt_status}")
            # Refresh the welcome line so the message matches the active combo
            QTimer.singleShot(50, self._refresh_welcome_line)

    def _first_run_settings(self):
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to Ember 👋")
        box.setText("Ember is an AI agent that can use your computer for you.")
        box.setInformativeText(
            "Quick setup:\n\n"
            "1.  Get a FREE Gemini API key (no credit card) — click 'Get free key'.\n"
            "2.  Paste it into Settings (opens next) and click Save.\n"
            "3.  When prompted, grant Screen Recording + Accessibility so Ember can see the "
            "screen and control the mouse/keyboard, then reopen Ember once.\n\n"
            "Tip: pick a Claude model in Settings for harder tasks, or run a local model "
            "(Ollama) to work offline with no rate limits."
        )
        get_btn = box.addButton("Get free key", QMessageBox.ButtonRole.ActionRole)
        perm_btn = (box.addButton("Open permissions", QMessageBox.ButtonRole.ActionRole)
                    if sys.platform == "darwin" else None)
        box.addButton("I have a key →", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is get_btn:
            try:
                import webbrowser
                webbrowser.open("https://aistudio.google.com/apikey")
            except Exception:
                pass
        elif perm_btn is not None and clicked is perm_btn:
            try:
                import mac_permissions
                mac_permissions.open_accessibility_settings()
            except Exception:
                pass
        self._open_settings()

    def _prime_permissions(self):
        """First-run macOS permission primer: request EVERY permission Ember needs — Accessibility,
        Screen Recording, Microphone and Input Monitoring — up front in one pass, then show a single
        clear guide listing exactly what's still missing and why. macOS never auto-prompts for
        Accessibility, and the others only "sometimes" prompt on unsigned/ad-hoc-signed builds (the
        request quietly never fires and capture calls just return black frames / silence), so Ember
        asks for all four explicitly instead of hoping an incidental call surfaces each one. No-ops
        off macOS."""
        if sys.platform != "darwin":
            return
        try:
            import mac_permissions as mp
        except Exception:
            return
        # (label, granted?-and-prompt fn, open-settings fn, why Ember needs it). Accessibility first:
        # it's the most-used capability and the one macOS never prompts for on its own.
        checks = (
            ("Accessibility", mp.request_accessibility, mp.open_accessibility_settings,
             "control the mouse & keyboard"),
            ("Screen Recording", mp.has_screen_recording, mp.open_screen_recording_settings,
             "see the screen — screenshots & the Ember Link mirror"),
            ("Microphone", mp.has_microphone, mp.open_microphone_settings,
             "hear you for voice chat & dictation"),
            ("Input Monitoring", mp.has_input_monitoring, mp.open_input_monitoring_settings,
             "the global hotkey & the “Hey Ember” wake word"),
        )
        missing = []
        first_pane = None
        for label, granted_fn, open_fn, why in checks:
            try:
                ok = bool(granted_fn(prompt=True))   # fires the OS prompt if still undetermined
            except Exception:
                ok = True   # can't check (missing pyobjc / older macOS) -> don't nag or block
            if not ok:
                missing.append((label, why))
                if first_pane is None:
                    first_pane = open_fn   # open just the first pane; the guide lists the rest
        if not missing:
            return
        if first_pane is not None:
            try:
                first_pane()
            except Exception:
                pass
        lines = "\n".join(f"•  **{label}** — to {why}" for label, why in missing)
        self._add_bubble(
            "system",
            "⚙️ **One-time setup — grant Ember these macOS permissions:**\n\n"
            f"{lines}\n\n"
            "Open **System Settings → Privacy & Security**, switch **Ember** on under each item "
            "above (I've opened the first one for you), then **quit and reopen Ember once** so "
            "they all take effect.",
        )

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
        import os
        # A deleted/invalid working directory makes os.getcwd() (called internally by the
        # model SDK's httpx/anyio) raise a bare FileNotFoundError — which used to show up here
        # as "Agent init failed". Repair it before constructing the client.
        try:
            os.getcwd()
        except Exception:
            for _d in (os.path.expanduser("~"), "/"):
                try:
                    os.chdir(_d); break
                except Exception:
                    continue
        from agent import Agent  # already warm from the background thread -> fast here
        raw_model = self.settings.get("model_id") or self.settings.get("gemini_model") or "gemini-3.1-flash-lite"
        # "Auto" -> resolve to the best free model; the rate-limit fail-over chain handles the rest.
        model_id = model_catalog.resolve(raw_model)
        provider = self.settings.get("provider") or model_catalog.provider_for(raw_model)
        try:
            if provider == "ollama":
                from ollama_agent import OllamaAgent
                self.agent = OllamaAgent(
                    model_name=self.settings.get("ollama_model") or "",
                    auto_screenshot=bool(self.settings.get("auto_screenshot", True)),
                )
            elif provider == "claude":
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
            elif provider == "openai":
                key = self.settings.get("openai_api_key") or ""
                label = self.settings.get("openai_provider_label") or "OpenAI"
                if not key:
                    self._set_status(f"Need {label} API key — open settings (gear)")
                    return
                from openai_agent import OpenAIAgent
                self.agent = OpenAIAgent(
                    api_key=key,
                    model_name=model_id,
                    base_url=self.settings.get("openai_base_url") or None,
                    provider_label=label,
                    auto_screenshot=bool(self.settings.get("auto_screenshot", True)),
                )
            else:
                if not self.settings.get("gemini_api_key"):
                    self._set_status("Need Gemini API key — open settings (gear)")
                    return
                self.agent = Agent(
                    api_key=self.settings["gemini_api_key"],
                    secondary_api_key=self.settings.get("gemini_api_key_secondary") or None,
                    backup_api_keys=[self.settings.get("gemini_api_key_3") or "",
                                     self.settings.get("gemini_api_key_4") or ""],
                    dual_api_failover=bool(self.settings.get("dual_api_failover", True)),
                    model_name=model_id,
                    anthropic_key=self.settings.get("anthropic_api_key") or None,
                    anthropic_model=self.settings.get("anthropic_model", "claude-opus-4-8"),
                    auto_screenshot=bool(self.settings.get("auto_screenshot", True)),
                    request_timeout_seconds=int(self.settings.get("request_timeout_seconds", 30)),
                    lean_tools=bool(self.settings.get("lean_tools", False)),
                )
            self.agent.subscribe(lambda ev: self._bridge.event.emit(ev))
            try:
                import ember_bridge
                ember_bridge.set_host_agent(self.agent)
            except Exception:
                pass
            # Carry the visible conversation into the fresh agent so switching model/provider
            # mid-chat doesn't wipe context. Best-effort: never let it block agent init.
            try:
                if hasattr(self.agent, "load_history"):
                    turns = self._history_turns_for_seeding()
                    if turns:
                        self.agent.load_history(turns)
            except Exception:
                pass
            self._set_status(f"Ready ({model_id})")
        except Exception as e:
            # Only blame the key/model for actual auth/model errors; other failures
            # (e.g. a transient FileNotFoundError) get a generic hint instead of a wrong one.
            es = str(e).lower()
            if isinstance(e, FileNotFoundError) or "errno 2" in es:
                hint = ("A file/path the app expected was missing (often a stale working "
                        "directory). Restarting Ember usually fixes it.")
            elif any(t in es for t in ("api key", "api_key", "unauthorized", "permission",
                                       "401", "403", "not found", "404", "model")):
                hint = "Check your API key and model name in Settings (gear)."
            else:
                hint = "Try again, or restart Ember. Open Settings (gear) to check your key/model."
            QMessageBox.critical(self, "Agent init failed", f"{type(e).__name__}: {e}\n\n{hint}")
            self._set_status("Agent init failed")

    def _on_timer_fired(self, info: dict):
        """Called from the timer's background thread when a countdown elapses. Marshal to the UI
        thread (the timers module already did the desktop notification + alert sound)."""
        try:
            msg = (info or {}).get("message") or "Timer — time's up!"
            self._bridge.timer_fired.emit(msg)
        except Exception:
            pass

    def _on_timer_fired_ui(self, message: str):
        """UI-thread handler: pop a chat bubble for the elapsed timer and speak it if enabled."""
        try:
            self._add_bubble("system", f"⏰ {message}")
        except Exception:
            pass
        try:
            if self._should_speak_reply():
                self._speak_reply(message)
        except Exception:
            pass

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
                self._set_siri("speaking")
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
                    # Follow the stream only if the user is parked at the bottom (don't yank
                    # them down mid-read). rangeChanged also fires on growth as a backstop.
                    QTimer.singleShot(0, self._chat_follow_bottom)
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
            if ev.kind == "thinking":
                # The model's reasoning summary — show it as a collapsible "Thinking" note.
                self._add_thinking(ev.payload or "")
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
                # Wake-word voice turn ("Hey Ember"): show 'speaking' on the chosen visual
                # (glow around Ember, or the orb) and ALWAYS speak it — never surfacing the window.
                if getattr(self, "_orb_active", False):
                    self._voice_state("speaking", text)
                    self._orb_speak(text)
                else:
                    self._speak_reply(text)
            elif ev.kind == "tool_call":
                name = ev.payload["name"]
                args_short = self._shorten_args(ev.payload["args"])
                self._activity_tool_call(name, ev.payload["args"])
                # No separate "calling…" bubble — the status line shows it live and the result
                # bubble records what ran, so the chat doesn't fill up with activity lines.
                if _remote:
                    _remote.push_chat("tool", f"Running {name}({args_short})")
                self._set_status(f"Running {name}…")
            elif ev.kind == "tool_result":
                name = ev.payload["name"]
                result = ev.payload["result"]
                ok = result.get("ok", True)
                summary = self._summarize_result(name, result)
                self._activity_tool_result(name, result, summary)
                if ok:
                    # Completed steps update the live status, NOT the chat — keep the
                    # conversation to real messages, not a wall of task activity.
                    self._set_status(f"✓ {name}")
                else:
                    # Failures still surface so the user isn't left guessing.
                    self._add_bubble("error", summary, meta=f"← {name}")
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
                self._activity_complete()
                self._add_bubble("error", str(ev.payload))
                self._append_history("error", str(ev.payload))
                if _remote:
                    _remote.push_chat("system", "Error: " + str(ev.payload))
            elif ev.kind == "done":
                self._activity_complete()
                self.send_btn.setEnabled(True)
                self._hide_typing_indicator()
                self._set_status(f"Ready ({self.settings.get('model_id') or self.settings.get('gemini_model')})")
                if getattr(self, "_orb_active", False):
                    self._orb_active = False
                    if getattr(self, "_orb_conversation", False):
                        # Conversational mode: keep chatting — listen again (after Ember finishes
                        # speaking) without needing another "Hey Ember". The delay gives TTS time
                        # to start so is_speaking() gates correctly (mic won't catch Ember).
                        QTimer.singleShot(600, self._continue_orb_conversation)
                    else:
                        self._voice_end(delay_ms=7000)
                        self._resume_wake_word()
                elif self._voice_chat_enabled:
                    self._voice_waiting_for_reply = False
                    self._update_voice_chat_ui("Listening again")
                    QTimer.singleShot(650, lambda: self._start_voice_listen(mode="voice_chat"))
                elif not self._listening:
                    # Fully idle: stop the glow and let the wake word listen again.
                    self._set_siri(None)
                    try:
                        import wake_word
                        wake_word.resume()
                    except Exception:
                        pass
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
        self._chat_stick_bottom = True   # an action prompt the user must see
        QTimer.singleShot(50, self._chat_follow_bottom)

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
        self._chat_stick_bottom = True   # an action prompt the user must see
        QTimer.singleShot(50, self._chat_follow_bottom)


_CRASH_LOG_FILE = None


def _install_crash_guards():
    """Stop Ember from silently dying with macOS's 'Ember quit unexpectedly'. Two layers:

      1. faulthandler dumps the Python stack to ember-crash.log on a FATAL native signal
         (SIGSEGV/SIGABRT) — so a hard native crash still tells us the exact line.
      2. A custom sys.excepthook: on PyQt6, an unhandled Python exception inside a slot or
         virtual method calls qFatal() and ABORTS the whole process. Installing our own hook
         turns that into a logged, shown, NON-fatal error — one bad signal handler or settings
         widget can no longer take the app down."""
    global _CRASH_LOG_FILE
    log_path = _data_dir() / "ember-crash.log"
    try:
        import faulthandler
        _CRASH_LOG_FILE = open(log_path, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_CRASH_LOG_FILE, all_threads=True)
    except Exception:
        pass

    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            sys.__stderr__.write(text)
        except Exception:
            pass
        try:
            from datetime import datetime
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} unhandled exception ---\n")
                f.write(text)
        except Exception:
            pass
        try:
            from PyQt6.QtWidgets import QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(None, "Ember hit an error",
                    f"Ember caught an error and kept running:\n\n{exc_type.__name__}: {exc}\n\n"
                    f"Details were saved to:\n{log_path}")
        except Exception:
            pass

    sys.excepthook = _hook


def _set_macos_app_name(name: str = "Ember") -> None:
    """Make macOS show `name` (not the interpreter, e.g. 'python3.12') in the menu bar and Dock.

    When Ember runs from source (Ember.command → .venv/bin/python main.py) it isn't inside a
    signed .app bundle, so macOS labels it with the executable's name. Overriding the main
    bundle's info dictionary BEFORE the GUI (NSApplication) starts is the standard fix — the
    frozen .app already carries CFBundleName=Ember, so this is a no-op there."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
        bundle = NSBundle.mainBundle()
        if bundle is not None:
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            if info is not None:
                info["CFBundleName"] = name
                info["CFBundleDisplayName"] = name
    except Exception:
        pass
    # CFBundleName fixes the menu-bar title, but the Dock / Force-Quit / Activity Monitor label
    # for an unbundled process comes from the PROCESS name — which defaults to the interpreter
    # ("python3.12"). Override it too so every place macOS shows Ember says "Ember".
    try:
        from Foundation import NSProcessInfo
        NSProcessInfo.processInfo().setProcessName_(name)
    except Exception:
        pass


def main(instance_listener=None):
    _install_crash_guards()
    if _SAFE_MODE:
        print("[Ember] EMBER_SAFE_MODE on — native blur, global hotkey, accessibility "
              "prompt, and phone-remote autostart are disabled.")
    # Qt WebEngine (Ember Browser) is imported lazily, AFTER the app starts. That's only
    # allowed if shared GL contexts are enabled before the QApplication is created — otherwise
    # importing it raises "AA_ShareOpenGLContexts must be set before a QCoreApplication".
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception:
        pass
    _set_macos_app_name("Ember")   # menu bar / Dock say "Ember", not "python3.12" (source runs)
    app = QApplication(sys.argv)
    app.setApplicationName("Ember")
    app.setApplicationDisplayName("Ember")
    try:
        app.setDesktopFileName("Ember")   # Linux: match the .desktop id so the launcher labels it
    except Exception:
        pass
    # Closing the window hides to tray (keeps the "Hey Ember" wake word + background
    # monitors alive); real quit is the tray ▸ Quit action.
    app.setQuitOnLastWindowClosed(False)

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
    window._tray = tray
    window.show()

    # If another instance of Ember is started, it sends SUMMON through the lock socket.
    if instance_listener is not None:
        try:
            from single_instance import listen_for_summon
            listen_for_summon(instance_listener, lambda: window._bridge.summon.emit())
        except Exception as e:
            print(f"[summon listener failed: {e}]")

    show_action.triggered.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
    quit_action.triggered.connect(window._do_quit)
    tray.activated.connect(lambda r: (window.showNormal(), window.raise_(), window.activateWindow())
                           if r == QSystemTrayIcon.ActivationReason.Trigger else None)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
