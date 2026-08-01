#!/bin/bash
# Double-click to install Ember as a real Mac app.
#
# This BUILDS dist/Ember.app and copies it into /Applications, so Ember afterwards opens like
# any other Mac app — from Launchpad or Spotlight, with its own icon and name, and with no
# Terminal window.
#
# It used to just `exec` System Files/Ember.command, which runs Ember from source inside the
# Terminal under the venv's interpreter. That works, but it is not an install: nothing lands in
# /Applications, the Dock shows a Python process, and closing the Terminal closes Ember.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SYS="$ROOT/System Files"

# Overridable so this can be exercised without touching a real /Applications, and so a user
# without admin rights can install into their own ~/Applications instead.
APPS_DIR="${EMBER_APPS_DIR:-/Applications}"

say()  { echo "$@"; }
pause() { [ -n "${EMBER_NONINTERACTIVE:-}" ] || { echo ""; echo "Press Return to close."; read -r _; }; }
die()  { echo ""; echo "$1"; pause; exit 1; }

if [ -z "${EMBER_NONINTERACTIVE:-}" ]; then
    open "$HERE/Installation Guide.html" 2>/dev/null || true
    say ""
    say "The macOS installation guide is open in your browser."
    say "Read it first, then press Return to install Ember."
    read -r _
fi

# Gatekeeper: clear the quarantine flag on the whole folder so the build scripts can run.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true

[ -d "$SYS" ] || die "Cannot find the 'System Files' folder next to this installer."

say ""
say "==============================================="
say "  Installing Ember  (first run: 3-6 minutes)"
say "==============================================="
say ""

# Build the bundle. BUILD_DESKTOP_APP.command bootstraps its own Python 3.12 via uv, so there
# is nothing to install by hand first.
EMBER_NONINTERACTIVE=1 "$SYS/BUILD_DESKTOP_APP.command" || die \
"The build did not finish, so Ember was not installed.

You can still run Ember from source in the meantime:
  open \"$SYS/Ember.command\""

APP="$SYS/dist/Ember.app"
[ -d "$APP" ] || die \
"The build reported success but produced no Ember.app, so there is nothing to install.

You can still run Ember from source:
  open \"$SYS/Ember.command\""

say ""
say "Installing to $APPS_DIR …"
mkdir -p "$APPS_DIR" 2>/dev/null || true

DEST="$APPS_DIR/Ember.app"
# Replacing a running app leaves a half-copied bundle, so close it first if it is open.
pkill -x Ember 2>/dev/null || true

if [ -e "$DEST" ]; then
    rm -rf "$DEST" 2>/dev/null || die \
"Could not replace the existing $DEST (permission denied).

Quit Ember if it is running, then try again — or install for yourself only:
  EMBER_APPS_DIR=\"\$HOME/Applications\" \"$HERE/Install Ember.command\""
fi

if ! cp -R "$APP" "$DEST" 2>/dev/null; then
    # No admin rights for /Applications is the common case; fall back rather than fail.
    FALLBACK="$HOME/Applications"
    say "  Could not write to $APPS_DIR — installing to $FALLBACK instead."
    mkdir -p "$FALLBACK"
    rm -rf "$FALLBACK/Ember.app"
    cp -R "$APP" "$FALLBACK/Ember.app" || die "Could not install Ember to $FALLBACK either."
    DEST="$FALLBACK/Ember.app"
fi

# Verify rather than assume: a truncated copy (full disk, interrupted) must not be reported
# as a successful install.
[ -x "$DEST/Contents/MacOS/Ember" ] || [ -d "$DEST/Contents/MacOS" ] || die \
"Ember.app was copied to $DEST but looks incomplete. Delete it and run this installer again."

xattr -cr "$DEST" 2>/dev/null || true

say ""
say "==============================================="
say "  Installed →  $DEST"
say "==============================================="
say ""
say "  Ember is now a normal Mac app:"
say "    • Open it from Launchpad or Spotlight (⌘-Space, type Ember)"
say "    • No Terminal window, and closing this one won't stop it"
say ""
say "  First launch: macOS will say the developer cannot be verified."
say "  Go to System Settings → Privacy & Security → Open Anyway."
say "  (On older macOS: right-click the app → Open.)"
say ""
say "  Then grant Screen Recording and Accessibility in"
say "  System Settings → Privacy & Security, so Ember can see and"
say "  control the screen."
say ""

if [ -z "${EMBER_NONINTERACTIVE:-}" ]; then
    open "$DEST" 2>/dev/null || open "$APPS_DIR" 2>/dev/null || true
fi
pause
