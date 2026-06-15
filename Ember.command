#!/bin/bash
# Double-click this file to run Ember on macOS.
# First run installs everything automatically; later runs just launch.
#
# No Homebrew and no pre-installed Python required: this uses uv
# (https://docs.astral.sh/uv/), which fetches its own Python 3.12. That also
# means pip pulls a prebuilt wheel for pyaudio instead of trying to compile it,
# so microphone/voice support works without portaudio.
cd "$(dirname "$0")"

# macOS Gatekeeper: once this file runs, clear the "quarantine" flag from the
# whole folder so the other .command files open without the "Apple cannot
# verify" prompt. (The very first launch still needs right-click -> Open.)
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true

die() { echo ""; echo "$1"; echo "Press Enter to close."; read -r _; exit 1; }

# --- 1. Make sure uv is available (install it if not) -----------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "First-time setup: installing uv (Python toolchain, no admin needed)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh \
        || die "Could not install uv. Install Python 3.12 from https://www.python.org/downloads/ and try again."
    export PATH="$HOME/.local/bin:$PATH"
fi

# --- 2. Create the environment (uv downloads Python 3.12 if needed) ---------
[ -d ".venv" ] || uv venv --python 3.12 || die "Could not create the Python environment."

# --- 3. Install dependencies on first run (fast/no-op afterwards) -----------
if ! uv run python -c "import PyQt6, google.genai" >/dev/null 2>&1; then
    echo "First-time setup: installing Ember dependencies (this takes a few minutes)…"
    if ! uv pip install -r requirements.txt; then
        # pyaudio (voice input) is the only package that may fail to build on
        # some setups. It's optional, so retry without it rather than dead-end.
        echo "Retrying without optional voice input (pyaudio)…"
        grep -v '^pyaudio' requirements.txt > .requirements.core.txt
        uv pip install -r .requirements.core.txt || { rm -f .requirements.core.txt; die "Dependency install failed."; }
        rm -f .requirements.core.txt
    fi
fi

echo "Starting Ember…"
exec uv run python main.py
