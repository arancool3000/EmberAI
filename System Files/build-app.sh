#!/bin/bash
# Build Ember.app on macOS via PyInstaller.

set -e
cd "$(dirname "$0")"

echo "==========================================="
echo "  Building Ember.app (takes 2-5 min)"
echo "==========================================="

python3 -m pip install --quiet --upgrade pyinstaller

rm -rf build dist

python3 -m PyInstaller --noconfirm Ember.spec

echo ""
echo "==========================================="
echo "  Build complete!"
echo "  Bundle:  dist/Ember.app"
echo "  Drag it to Applications, then grant:"
echo "  - Screen Recording"
echo "  - Accessibility"
echo "  - Input Monitoring (for the global hotkey)"
echo "==========================================="
