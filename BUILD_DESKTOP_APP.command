#!/bin/bash
# Double-click to build a REAL standalone Ember.app (no Terminal, no Python needed to run it).
# Free: uses PyInstaller. Output lands in dist/Ember.app — drag it to /Applications.
cd "$(dirname "$0")"

# macOS Gatekeeper: once this file runs, clear the "quarantine" flag from the
# whole folder so the other .command files open without the "Apple cannot
# verify" prompt. (The very first launch still needs right-click -> Open.)
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true

echo "==============================================="
echo "  Building Ember.app  (first build: 3-6 min)"
echo "==============================================="

# --- Get a Python 3.12 toolchain with ZERO prerequisites (uv fetches its own Python) --------
# The system python3 is often too old (< 3.10) to build/run Ember, so — like Ember.command —
# we bootstrap uv and build inside a private 3.12 venv. No manual Python install needed.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python toolchain, no admin needed)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh \
    || { echo "Could not install uv. Install Python 3.12 from https://www.python.org/downloads/ and retry."; read _; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -d ".venv" ] || uv venv --python 3.12 || { echo "Could not create the Python 3.12 environment."; read _; exit 1; }
PYBIN=".venv/bin/python"

echo "Installing Ember's dependencies…"
uv pip install -r requirements.txt || { echo "Dependency install failed. Press Enter."; read _; exit 1; }
uv pip install --upgrade pyinstaller

# Microphone INPUT: pyaudio is intentionally OUT of requirements.txt (it compiles against the
# portaudio C library), but a packaged .app MUST bundle it or "Hey Ember" and voice chat can't
# hear anything — the #1 "the mic doesn't work in the app" complaint. Install portaudio and point
# the compiler at it (Apple-Silicon Homebrew lives in /opt/homebrew, which pip doesn't search by
# default), so the spec's collect_all("pyaudio") actually bundles working mic support.
if command -v brew >/dev/null 2>&1; then
  brew list portaudio >/dev/null 2>&1 || { echo "Installing portaudio (microphone support)…"; brew install portaudio; }
  _pa="$(brew --prefix portaudio 2>/dev/null)"
  [ -n "$_pa" ] && export CPPFLAGS="-I$_pa/include ${CPPFLAGS}" && export LDFLAGS="-L$_pa/lib ${LDFLAGS}"
fi
uv pip install pyaudio \
  || echo "  WARNING: pyaudio didn't install — the built app won't have mic input. Install Homebrew (brew.sh) + 'brew install portaudio', then rebuild."

rm -rf build dist
"$PYBIN" -m PyInstaller --noconfirm Ember.spec || { echo "Build failed. Press Enter."; read _; exit 1; }

# Clean extended-attribute detritus and apply a VALID ad-hoc signature.
# PyInstaller's own ad-hoc signature is often malformed, which makes macOS
# re-verify the bundle on EVERY launch -> ~30s startup. A clean signature fixes it.
echo "Signing bundle (fixes slow first launch)…"
rm -f dist/Ember 2>/dev/null
xattr -cr dist/Ember.app 2>/dev/null
find dist/Ember.app -exec xattr -c {} \; 2>/dev/null
dot_clean -m dist/Ember.app 2>/dev/null
codesign --force --deep --sign - dist/Ember.app 2>/dev/null
codesign --verify --verbose=1 dist/Ember.app 2>/dev/null && echo "  ✓ signature valid" || echo "  (signature check skipped)"

# Package a drag-to-Applications .dmg too (the normal Mac app-download experience).
if command -v hdiutil >/dev/null 2>&1; then
  bash make_dmg.sh 2>/dev/null && echo "Packaged dist/Ember.dmg (drag-to-Applications)."
fi

echo ""
echo "==============================================="
echo "  Done →  dist/Ember.app   (+ dist/Ember.dmg)"
echo "  1. Open dist/Ember.dmg and drag Ember into Applications"
echo "     (or drag dist/Ember.app there directly)."
echo "  2. First launch: open it, then System Settings → Privacy"
echo "     & Security → Open Anyway  (older macOS: right-click → Open)."
echo "  3. Grant Screen Recording + Accessibility in"
echo "     System Settings → Privacy & Security."
echo "==============================================="
open dist 2>/dev/null
echo "Press Enter to close."
read _
