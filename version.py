"""Single source of truth for Ember's version + update/distribution config.

Everything that needs to know "what version is this and where do updates come from"
reads from here: the app UI, the auto-updater (updater.py), the release script
(RELEASE.command), and the download website (docs/latest.json is generated from this).

To wire up distribution, set GITHUB_OWNER (and GITHUB_REPO if you rename the repo),
then run RELEASE.command. Until GITHUB_OWNER is set, the auto-updater stays dormant.
"""
from __future__ import annotations

import os
import sys

# Bump this for every release (RELEASE.command does it for you). Semantic versioning.
__version__ = "1.10.4"

# --- GitHub Pages + Releases distribution -----------------------------------
# Set this to your GitHub username/org. The placeholder keeps the updater dormant
# until you've actually created the repo (so a fresh checkout never errors).
# Defaults match this repo; override via env (e.g. in CI) without editing the file.
GITHUB_OWNER = os.environ.get("EMBER_GITHUB_OWNER", "arancool3000")
GITHUB_REPO = os.environ.get("EMBER_GITHUB_REPO", "EmberAI")

# Per-OS release asset names (the updater downloads these; the release scripts produce them).
# Linux ships a raw AppImage (not zipped) - that's the idiomatic AppImage UX for someone
# downloading it by hand (chmod +x and run, no unzip step); updater.py detects the .AppImage
# extension and skips the unzip step it uses for the macOS/Windows .zip assets.
ASSET_NAMES = {"macos": "Ember-macOS.zip", "windows": "Ember-Windows.zip",
               "linux": "Ember-Linux.AppImage"}
MANIFEST_NAME = "latest.json"

_PLACEHOLDER_OWNER = "YOUR_GITHUB_USERNAME"


def platform_key() -> str | None:
    """'macos' | 'windows' | 'linux' | None — which OS we're running on for update purposes."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def asset_name(plat: str | None = None) -> str:
    return ASSET_NAMES.get(plat or platform_key() or "macos", "Ember-macOS.zip")


def is_configured() -> bool:
    """True once a real GitHub owner has been set (updater stays off until then)."""
    return bool(GITHUB_OWNER) and GITHUB_OWNER != _PLACEHOLDER_OWNER


def manifest_url() -> str:
    """Where the auto-updater fetches the release manifest (latest.json).

    Uses the Releases 'latest' alias so it always points at the newest published
    release without the URL changing."""
    return (f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/releases/latest/download/{MANIFEST_NAME}")


def latest_download_url(plat: str | None = None) -> str:
    """Direct 'download newest build' link for a platform (website button fallback)."""
    return (f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
            f"/releases/latest/download/{asset_name(plat)}")


def site_url() -> str:
    return f"https://{GITHUB_OWNER}.github.io/{GITHUB_REPO}/"


def parse(v: str) -> tuple[int, ...]:
    """Parse a version string like '1.2.3' (or 'v1.2.3') into a comparable tuple.
    Non-numeric/pre-release suffixes are ignored — keep releases plain numeric."""
    import re
    nums = re.findall(r"\d+", (v or "").strip())
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    """True if `candidate` is a strictly newer version than `current`."""
    return parse(candidate) > parse(current)
