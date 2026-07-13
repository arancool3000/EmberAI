#!/bin/bash
# One-step macOS setup: run this ONCE in Terminal and Ember launches itself.
#
#     bash unblock-mac.sh
#
# (Tip: type "bash " then drag this file from Finder into Terminal and press Return.)
#
# Why this is needed:
# macOS tags anything downloaded from the internet with a "quarantine" flag.
# Gatekeeper then blocks the unsigned .command files with the "Apple could not
# verify ... is free of malware" dialog -- and on macOS 15 (Sequoia) it removed
# the old right-click -> Open escape hatch (the dialog only offers Done / Move
# to Bin; do NOT pick Move to Bin, it deletes the file).
#
# Handing THIS file to `bash` bypasses that block, because Gatekeeper only
# inspects Finder double-clicks and `open`, not a file you run through bash.
# The script then strips the quarantine flag from the whole folder -- so every
# .command here double-clicks normally from now on -- and launches Ember.
set -e
cd "$(dirname "$0")"
echo "Unblocking Ember in: $(pwd)"
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true
echo "✓ Quarantine cleared — from now on you can just double-click Ember.command."
echo "Launching Ember…"
exec bash "./Ember.command"
