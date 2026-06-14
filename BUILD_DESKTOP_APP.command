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

# Ensure deps + PyInstaller are present.
python3 -c "import PyQt6, google.genai" >/dev/null 2>&1 || python3 -m pip install -r requirements.txt
python3 -m pip install --quiet --upgrade pyinstaller

rm -rf build dist
python3 -m PyInstaller --noconfirm Ember.spec || { echo "Build failed. Press Enter."; read _; exit 1; }

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

echo ""
echo "==============================================="
echo "  Done →  dist/Ember.app"
echo "  1. Drag dist/Ember.app to your Applications folder."
echo "  2. First launch: right-click → Open (unsigned app)."
echo "  3. Grant Screen Recording + Accessibility in"
echo "     System Settings → Privacy & Security."
echo "==============================================="
open dist 2>/dev/null
echo "Press Enter to close."
read _
