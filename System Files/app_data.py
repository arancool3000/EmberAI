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

