#!/bin/bash
# Ember for macOS - one-time install.

set -e
cd "$(dirname "$0")"

echo "==========================================="
echo "  Ember - macOS install"
echo "==========================================="

# Verify Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.10+ from https://www.python.org/downloads/macos/"
    echo "       or:  brew install python@3.12"
    exit 1
fi

# Verify portaudio (for pyaudio / voice)
if ! brew list portaudio >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "Installing portaudio (for microphone support)..."
        brew install portaudio || echo "  (skipping - install manually if mic doesn't work)"
    else
        echo "NOTE: Homebrew not found. For microphone support, install portaudio manually."
    fi
fi

echo "Upgrading pip..."
python3 -m pip install --upgrade pip

echo "Installing dependencies (PyQt6, Gemini SDK, Anthropic SDK, voice...)"
python3 -m pip install -r requirements.txt

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
echo "  Run:  ./run.sh"
echo "  Free Gemini key at:  https://aistudio.google.com/apikey"
echo "==========================================="
