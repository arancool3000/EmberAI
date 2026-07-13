#!/bin/bash
# Package the built Ember.app into a drag-to-Applications disk image
# (dist/Ember.dmg) -- the same "open the .dmg, drag the icon into Applications"
# experience as a normal Mac app download (e.g. the Claude desktop app).
#
# Run AFTER BUILD_DESKTOP_APP.command has produced dist/Ember.app.
# macOS only (uses hdiutil). Usage:  bash make_dmg.sh
set -euo pipefail
cd "$(dirname "$0")"

APP="dist/Ember.app"
if [ ! -d "$APP" ]; then
  echo "No $APP yet — build it first:  ./BUILD_DESKTOP_APP.command"
  exit 1
fi

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/Ember.app"
ln -s /Applications "$STAGE/Applications"   # the drop target shown in the dmg window

rm -f dist/Ember.dmg
hdiutil create -volname "Ember" -srcfolder "$STAGE" -ov -format UDZO dist/Ember.dmg >/dev/null
rm -rf "$STAGE"

echo "✓ dist/Ember.dmg — open it, then drag Ember into Applications."
