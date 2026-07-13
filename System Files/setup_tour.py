"""First-run setup tour for people new to AI.

The tour asks how comfortable the user is, then sets up the essentials in PLAIN language:
it renames jargon (e.g. "Ollama" → "Free offline AI"), offers a one-click install of the
free offline brain, helps add a free online key, and turns on sensible defaults.

This module is the pure, testable core — experience levels, friendly labels, the per-OS
install plan, and the recommended config per level. The wizard UI (SetupTourDialog) lives in
ui.py and is built on top of these.
"""
from __future__ import annotations

import shutil
import sys

LEVELS = ("beginner", "some", "expert")

LEVEL_LABELS = {
    "beginner": "New to this — keep it simple",
    "some": "I know my way around",
    "expert": "Expert — show me everything",
}


def friendly_model_label(model_id: str, display: str, level: str) -> str:
    """Plain-language model names for newcomers; the technical name for experts."""
    if level == "expert":
        return display
    if model_id == "ollama":
        return "Free offline AI (runs on your computer — no internet, no account)"
    if model_id == "auto":
        return "Recommended — picks the best free AI for you"
    if model_id.startswith("gemini"):
        return "Free online AI (Google) — needs a free key"
    if model_id.startswith("claude"):
        return "Advanced AI (Claude) — needs a paid key"
    return display


def ollama_installed() -> bool:
    return bool(shutil.which("ollama"))


def ollama_install_plan(platform: str | None = None) -> dict:
    """How to install the free offline AI (Ollama) on this OS.
    Returns {method, label} plus either 'command' (list) or 'url' (str)."""
    plat = platform or sys.platform
    if plat == "darwin":
        if shutil.which("brew"):
            return {"method": "brew", "command": ["brew", "install", "--cask", "ollama"],
                    "label": "Install the free offline AI (via Homebrew)"}
        return {"method": "download", "url": "https://ollama.com/download",
                "label": "Open the free offline AI download page"}
    if plat.startswith("win"):
        return {"method": "download", "url": "https://ollama.com/download/windows",
                "label": "Open the free offline AI download page"}
    return {"method": "script",
            "command": ["/bin/sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            "label": "Install the free offline AI"}


def recommended_model_pull(level: str) -> str:
    """Which local model to suggest pulling: small+fast for beginners, a tool-capable one else."""
    return "llama3.2" if level == "beginner" else "qwen2.5"


def recommended_settings(level: str) -> dict:
    """Good defaults to apply when the tour finishes, tuned to the chosen experience level."""
    cfg = {"experience_level": level, "setup_complete": True}
    if level == "beginner":
        cfg.update({"lean_tools": True, "wake_word": True, "glow_animation": True,
                    "voice_output": False, "wake_visual": "glow"})
    return cfg


APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"


def gmail_setup_hint() -> str:
    """Plain-language explanation of the optional Gmail connect step in the tour."""
    return ("Optional — connect Gmail so Ember can organise your inbox (search, label, archive, "
            "star, tidy up) and send email for you. Use a Google App Password (not your normal "
            "password): turn on 2-Step Verification, then create one and paste it below. Skip if "
            "you'd rather do it later in Settings.")


def feature_highlights() -> list:
    """A short, friendly list of what Ember can do, shown at the end of the tour so newcomers
    know what to try. Pure data so it can be unit-tested and reused."""
    return [
        "🖥️  Control your computer — open apps, manage files, drive the browser, read the screen.",
        "🗂️  Organise your Gmail — search, label, archive, star, and tidy your inbox.",
        "⏰  Set timers & reminders — “set a 10 minute timer”.",
        "🛡️  Antivirus — holds risky downloads and AI-scans them before they can open.",
        "🔌  Works offline — the free local AI and all local tools run with no internet.",
        "🎙️  Talk to it — say “Hey Ember”, or just type. Voice in and out.",
    ]


def gmail_settings_from(address: str, app_password: str) -> dict:
    """Build the settings to enable Gmail (organise + send) from the tour. Mirrors the address +
    App Password into the SMTP/IMAP keys so one password powers everything. Returns {} if blank."""
    address = (address or "").strip()
    app_password = "".join((app_password or "").split())
    if not (address and app_password):
        return {}
    cfg = {
        "gmail_address": address,
        "gmail_app_password": app_password,
        "email_smtp_user": address,
        "email_smtp_password": app_password,
    }
    if address.lower().endswith(("@gmail.com", "@googlemail.com")):
        cfg["email_smtp_host"] = "smtp.gmail.com"
        cfg["gmail_imap_host"] = "imap.gmail.com"
    return cfg


def should_show(settings: dict) -> bool:
    """Show the tour on first run: not completed yet, and nothing configured."""
    if settings.get("setup_complete"):
        return False
    has_brain = bool(settings.get("gemini_api_key") or settings.get("anthropic_api_key")
                     or settings.get("provider") == "ollama"
                     or (settings.get("model_id") == "ollama"))
    return not has_brain
