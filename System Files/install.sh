#!/bin/bash
# Ember for macOS - one-time install.
# Uses uv (https://docs.astral.sh/uv/), which fetches its own Python 3.12 -
# no Homebrew or pre-installed Python required.

set -e
cd "$(dirname "$0")"

echo "==========================================="
echo "  Ember - macOS install"
echo "==========================================="

# --- Ensure uv is available -------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (Python toolchain, no admin needed)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# --- Environment + dependencies --------------------------------------------
echo "Creating Python 3.12 environment…"
[ -d ".venv" ] || uv venv --python 3.12

echo "Installing dependencies (PyQt6, Gemini SDK, Anthropic SDK, voice output...)"
uv pip install -r requirements.txt

# Microphone / voice INPUT (pyaudio) is optional and needs the portaudio C
# library, so it's not installed by default. To enable it:
#   brew install portaudio && uv pip install -r requirements-voice.txt

echo ""
echo "==========================================="
echo "  Install complete!"
echo ""
echo "  Permissions to grant on first run:"
echo "  - Screen Recording (System Settings > Privacy & Security)"
echo "  - Accessibility (for UI control + global hotkey)"
echo "  - Input Monitoring (for global hotkey)"
echo "  - Microphone (only if you want voice input)"
echo ""
echo "  Run:  ./run.sh   (or double-click Ember.command)"
echo "  Free Gemini key at:  https://aistudio.google.com/apikey"
echo "==========================================="
