#!/bin/bash
# Double-click this to run Ember from SOURCE on macOS with the microphone working.
#
# Why: a downloaded/pre-built Ember.app is a frozen snapshot — it can't pick up code
# fixes and it shipped without pyaudio + with a wrong-CPU flac (the mic errors you saw).
# Running from source uses your own Python (where pyaudio installs) and Homebrew's native
# flac, so voice actually works AND every code fix is live on restart.
#
# It installs what's needed the first time, then just launches Ember on later runs.
set -e
cd "$(dirname "$0")"

echo "=== Ember (run from source) ==="

# 1) Homebrew + the native audio libraries the mic needs.
if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew isn't installed. Install it from https://brew.sh first, then run this again."
  read -r -p "Press Enter to close…"; exit 1
fi
brew list flac      >/dev/null 2>&1 || { echo "Installing flac…";      brew install flac; }
brew list portaudio >/dev/null 2>&1 || { echo "Installing portaudio…"; brew install portaudio; }

# 2) A private virtual-env so we never touch the system Python.
if [ ! -d ".venv" ]; then
  echo "Creating Python virtual-env…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
echo "Installing Ember's dependencies (first run only, ~1-2 min)…"
python -m pip install --quiet -r requirements.txt
# Microphone input is already covered: requirements.txt installs sounddevice, whose wheels
# bundle PortAudio. PyAudio is NOT installed here on purpose — it has no macOS wheel, so it
# compiles against whatever PortAudio is on the machine and is by far the most common cause
# of "voice doesn't work". It stays available as an opt-in extra:
#   brew install portaudio && python -m pip install pyaudio
# Ember prefers sounddevice regardless, and falls back to PyAudio only if it is present.

# 3) Launch. The first time, macOS will ask Terminal for Microphone / Accessibility /
#    Screen Recording — say YES to all three (those grants attach to Terminal here).
echo "Launching Ember…"
exec python main.py
