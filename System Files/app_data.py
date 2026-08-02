"""Stable per-user storage for Ember settings, secrets, memory, and generated state.

Application code may be replaced by an installer, updater, git pull, or a new release folder.
User data must therefore never live beside ``ui.py`` or the executable.  This module provides
one OS-owned support directory and performs a conservative one-time migration of known legacy
files: existing destination data always wins and source files are copied, never deleted.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def data_dir() -> Path:
    """Return Ember's stable machine-level support directory, creating it if needed."""
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override).expanduser()
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


_LEGACY_NAMES = (
    "settings.json", "chat_history.json", "vault.enc", "vault.key",
    "memory.json", "automations.json", "usage.json", "api_health.json",
    "snippets.json", "macros.json", "remote_pin.txt", "remote_tokens.txt",
    "browser_profile", "workflows", "recordings", "screenshots", "plugins",
    "Scheduled Tasks", "scheduled_tasks",
)


def _legacy_dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [here]
    if sys.platform.startswith("win"):
        roaming = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        candidates.append(roaming / "Ember")
    elif not sys.platform.startswith("darwin"):
        candidates.append(Path.home() / ".ember")
    out = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved not in out:
            out.append(resolved)
    return out


def migrate_legacy_data(legacy_dirs=None) -> list[str]:
    """Copy known user-data items into :func:`data_dir` when the destination is absent."""
    dest = data_dir()
    copied: list[str] = []
    for old in (legacy_dirs or _legacy_dirs()):
        old = Path(old)
        try:
            if old.resolve() == dest.resolve() or not old.exists():
                continue
        except OSError:
            continue
        for name in _LEGACY_NAMES:
            src = old / name
            target = dest / name
            if not src.exists() or target.exists():
                continue
            try:
                if src.is_dir():
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
                    if name in ("settings.json", "vault.enc", "vault.key"):
                        try:
                            os.chmod(target, 0o600)
                        except OSError:
                            pass
                copied.append(name)
            except OSError:
                # A failed migration must never prevent Ember from launching.
                continue
    return copied



#: Keys that `ui.save_settings` moves into the encrypted vault, leaving settings.json blanked.
#: Kept here, beside the loader, so any module that reads settings from disk gets the real
#: values without having to import the (PyQt-heavy) ui module.
VAULT_KEYS = ("gemini_api_key", "gemini_api_key_secondary", "gemini_api_key_3",
              "gemini_api_key_4", "anthropic_api_key", "openai_api_key",
              "soundtools_api_key", "gmail_app_password", "email_smtp_password")


def read_settings() -> dict:
    """Settings as written to disk, with no vault hydration. Rarely what you want."""
    import json
    p = data_dir() / "settings.json"
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def settings_with_keys() -> dict:
    """Settings with API keys resolved — from the encrypted vault when it is enabled.

    With the key vault on (the default), ``settings.json`` holds *blanked* API keys and the
    real ones live in the vault. A module that reads settings.json directly therefore sees no
    Gemini key and reports "Add a Gemini API key in Ember Settings" — while the agent, whose
    settings dict was hydrated at load, is happily using that very key. That is exactly the
    state a user experiences as "I have and don't have a Gemini key".

    Falls back to the environment, so a headless or CI run can supply keys without a vault.
    """
    settings = read_settings()
    if settings.get("use_key_vault", True):
        try:
            import key_vault
            for k in VAULT_KEYS:
                if not settings.get(k):
                    v = key_vault.get_key(k)
                    if v:
                        settings[k] = v
        except Exception:
            pass
    return settings
