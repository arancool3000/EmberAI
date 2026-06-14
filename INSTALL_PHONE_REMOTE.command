#!/bin/bash
# Install the ALWAYS-ON phone remote (EmberConnect).
# After this, your phone can control this Mac at login automatically — even if Ember isn't
# open and even if the keyboard/mouse drivers fail. Double-click to install.
cd "$(dirname "$0")"
DIR="$(pwd)"
PY="$(command -v python3)"
SUPPORT="$HOME/Library/Application Support/Ember"
PLIST="$HOME/Library/LaunchAgents/com.ember.phoneremote.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$SUPPORT"

echo "Installing always-on phone remote…"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ember.phoneremote</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string>
    <string>$DIR/remote_standalone.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$SUPPORT/remote.log</string>
  <key>StandardErrorPath</key><string>$SUPPORT/remote.err</string>
</dict></plist>
EOF

launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST" && echo "✓ Loaded — it will also auto-start at every login."
sleep 2
echo ""
if [ -f "$SUPPORT/remote_url.txt" ]; then
  echo "On your phone (same Wi-Fi), open:"
  cat "$SUPPORT/remote_url.txt"
else
  echo "Starting… check $SUPPORT/remote_url.txt in a moment for the URL + PIN."
fi
echo ""
echo "IMPORTANT — grant these to python3 once (System Settings → Privacy & Security):"
echo "  • Screen Recording   (to see the screen on your phone)"
echo "  • Accessibility      (to move the mouse / type)"
echo "Python is at: $PY"
echo ""
echo "To REMOVE later:  launchctl unload \"$PLIST\" && rm \"$PLIST\""
read -p "Press Enter to close."
