#!/bin/bash
# Run this ONCE in Terminal to let Ember's .command files open on macOS:
#
#     bash unblock-mac.sh
#
# (Tip: type "bash " then drag this file from Finder into Terminal and press Return.)
#
# Why this exists:
# macOS tags anything downloaded from the internet with a "quarantine" flag.
# Unsigned scripts that carry it are blocked by Gatekeeper with the
# "Apple could not verify ... is free of malware" dialog. On macOS 15 (Sequoia)
# that dialog no longer offers a right-click -> Open escape hatch (only
# "Done" / "Move to Bin" -- do NOT pick "Move to Bin", it deletes the file).
#
# Running THIS file with `bash` bypasses that block: Gatekeeper only inspects
# Finder double-clicks and `open`, not a file you hand directly to bash. The
# script then strips the quarantine flag from its own folder, so every
# .command here (Ember.command, BUILD_DESKTOP_APP.command, ...) opens with a
# normal double-click afterwards.
#
# Equivalent manual one-liner:  xattr -dr com.apple.quarantine /path/to/this/folder
cd "$(dirname "$0")"
echo "Unblocking Ember in: $(pwd)"
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true
echo "✓ Done. You can now double-click Ember.command (and the other .command files)."
