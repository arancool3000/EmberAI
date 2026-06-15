#!/bin/bash
# Developer-ID signing + Apple notarization for Ember's release artifacts.
#
# Turns the free/unsigned build into a fully NOTARIZED one that opens with no
# Gatekeeper warning at all — the same experience as the Claude desktop app.
#
# It is enabled only when an Apple Developer ID signing identity is configured;
# otherwise every subcommand is a graceful no-op, so the free/unsigned release
# path keeps working unchanged.
#
# One-time setup (needs a paid Apple Developer account — see PUBLISH_SETUP.md):
#   export EMBER_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   # then EITHER a stored notarytool keychain profile (recommended):
#   #   xcrun notarytool store-credentials ember-notary \
#   #       --apple-id you@example.com --team-id TEAMID --password APP-SPECIFIC-PW
#   export EMBER_NOTARY_PROFILE="ember-notary"
#   # OR the raw credentials instead of a profile:
#   export EMBER_APPLE_ID="you@example.com"
#   export EMBER_TEAM_ID="TEAMID"
#   export EMBER_APP_PASSWORD="app-specific-password"
#
# Usage:
#   bash notarize_mac.sh enabled                         # exit 0 iff signing is configured
#   bash notarize_mac.sh sign <App.app>                  # Developer-ID sign (hardened runtime)
#   bash notarize_mac.sh notarize <file> [staple-extra]  # submit + wait + staple
set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-}"
IDENTITY="${EMBER_SIGN_IDENTITY:-}"

case "$CMD" in
  enabled)
    [ -n "$IDENTITY" ]   # exit status: 0 if configured, 1 if not
    ;;

  sign)
    TARGET="${2:?usage: notarize_mac.sh sign <App.app>}"
    if [ -z "$IDENTITY" ]; then
      echo "  (no EMBER_SIGN_IDENTITY — skipping Developer ID signing)"; exit 0
    fi
    echo "  Signing $TARGET with: $IDENTITY"
    # Sign nested code first, then the bundle, with the hardened runtime + a secure timestamp.
    find "$TARGET" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 2>/dev/null \
      | xargs -0 -I{} codesign --force --timestamp --options runtime --sign "$IDENTITY" {} 2>/dev/null || true
    codesign --force --deep --timestamp --options runtime --sign "$IDENTITY" "$TARGET"
    codesign --verify --strict --verbose=1 "$TARGET" \
      && echo "  ✓ Developer ID signature valid" || echo "  (signature verify warning — continuing)"
    ;;

  notarize)
    TARGET="${2:?usage: notarize_mac.sh notarize <file> [staple-extra]}"
    EXTRA="${3:-}"
    if [ -z "$IDENTITY" ]; then
      echo "  (no EMBER_SIGN_IDENTITY — skipping notarization)"; exit 0
    fi
    echo "  Submitting $TARGET to Apple notary (can take a few minutes)…"
    if [ -n "${EMBER_NOTARY_PROFILE:-}" ]; then
      xcrun notarytool submit "$TARGET" --keychain-profile "$EMBER_NOTARY_PROFILE" --wait
    elif [ -n "${EMBER_APPLE_ID:-}" ] && [ -n "${EMBER_TEAM_ID:-}" ] && [ -n "${EMBER_APP_PASSWORD:-}" ]; then
      xcrun notarytool submit "$TARGET" --apple-id "$EMBER_APPLE_ID" \
        --team-id "$EMBER_TEAM_ID" --password "$EMBER_APP_PASSWORD" --wait
    else
      echo "  ✗ EMBER_SIGN_IDENTITY is set but no notary credentials were provided"
      echo "    (set EMBER_NOTARY_PROFILE, or EMBER_APPLE_ID + EMBER_TEAM_ID + EMBER_APP_PASSWORD)."
      echo "    See PUBLISH_SETUP.md."
      exit 1
    fi
    # Staple the ticket so it verifies offline. A .zip can't be stapled — staple the
    # .app (passed as the extra target) or the .dmg instead.
    case "$TARGET" in
      *.zip) [ -n "$EXTRA" ] && xcrun stapler staple "$EXTRA" || true ;;
      *)     xcrun stapler staple "$TARGET" ;;
    esac
    echo "  ✓ notarized + stapled"
    ;;

  *)
    echo "usage: notarize_mac.sh {enabled | sign <app> | notarize <file> [staple-extra]}"
    exit 2
    ;;
esac
