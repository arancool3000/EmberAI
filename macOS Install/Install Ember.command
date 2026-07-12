#!/bin/bash
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

open "$HERE/Installation Guide.html" 2>/dev/null || true
echo ""
echo "The macOS installation guide is open in your browser."
echo "Read it first, then press Return to install Ember."
read -r _

cd "$ROOT"
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true
exec "$ROOT/Ember.command"
