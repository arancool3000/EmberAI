#!/bin/bash
# Double-click this file to run Ember on macOS.
# First run installs everything automatically; later runs just launch — OFFLINE.
#
# No Homebrew and no pre-installed Python required: first-time setup uses uv
# (https://docs.astral.sh/uv/), which fetches its own Python 3.12 and prebuilt
# wheels. After that, Ember launches straight from the local .venv with NO network
# and NO dependency re-download, so it opens fully offline.
cd "$(dirname "$0")"

# macOS Gatekeeper: once this file runs, clear the "quarantine" flag from the
# whole folder so the other .command files open without the "Apple cannot
# verify" prompt. (The very first launch still needs right-click -> Open.)
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true

die() { echo ""; echo "$1"; echo "Press Enter to close."; read -r _; exit 1; }

PYBIN=".venv/bin/python"

# --- Fast path: already set up -> launch straight from the venv, fully OFFLINE ---
# No uv, no network, no dependency check against any index. This is the normal launch.
if [ -x "$PYBIN" ] && "$PYBIN" -c "import PyQt6, google.genai" >/dev/null 2>&1; then
    echo "Starting Ember…"
    # `exec -a Ember` runs the venv Python but names the process "Ember", so the Dock /
    # Activity Monitor / Force-Quit say "Ember" instead of "python3.12". (macOS resolves the
    # real interpreter via its own path, not argv[0], so the venv still loads correctly.)
    exec -a Ember "$PYBIN" main.py
fi

# --- First-time setup (needs the network, once) ---------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "First-time setup: installing uv (Python toolchain, no admin needed)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh \
        || die "Could not install uv. Install Python 3.12 from https://www.python.org/downloads/ and try again."
    export PATH="$HOME/.local/bin:$PATH"
fi

[ -d ".venv" ] || uv venv --python 3.12 || die "Could not create the Python environment."

echo "First-time setup: installing Ember dependencies (this takes a few minutes)…"
# pyaudio (voice INPUT) is intentionally NOT in requirements.txt - it has no macOS
# wheel and needs portaudio. See requirements-voice.txt to add mic input.
uv pip install -r requirements.txt || die "Dependency install failed."

echo "Starting Ember…"
exec -a Ember "$PYBIN" main.py
