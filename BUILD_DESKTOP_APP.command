#!/bin/bash
# Double-click to build a REAL standalone Ember.app (no Terminal, no Python needed to run it).
# Free: uses PyInstaller. Output lands in dist/Ember.app — drag it to /Applications.
cd "$(dirname "$0")"

# macOS Gatekeeper: once this file runs, clear the "quarantine" flag from the
# whole folder so the other .command files open without the "Apple cannot
# verify" prompt. (The very first launch still needs right-click -> Open.)
xattr -dr com.apple.quarantine "$(pwd)" 2>/dev/null || true

# ---------------------------------------------------------------------------
# A tiny progress bar (ETA) + a new joke every 10s, so the 3-6 min build isn't
# a blank stare. The real command runs in the background with its output tucked
# into a log; on failure we show the tail of that log.
# ---------------------------------------------------------------------------
JOKES=(
  "Why do programmers prefer dark mode? Light attracts bugs."
  "There are 10 kinds of people: those who read binary and those who don't."
  "A SQL query walks up to two tables: 'Mind if I join you?'"
  "Why did the dev go broke? He used up all his cache."
  "I'd tell you a UDP joke, but you might not get it."
  "Changing a light bulb is a hardware problem, so: 0 programmers."
  "Why do Java devs wear glasses? They can't C#."
  "!false — it's funny because it's true."
  "A byte walks into a bar. Bartender: 'You OK?' 'Parity error.'"
  "Coding is 10% writing it and 90% wondering why it won't run."
  "There's no place like 127.0.0.1."
  "Why was the function sad? It never got called back."
  "Debugging: you're the detective AND the culprit."
  "To understand recursion, first understand recursion."
  "It's not a bug — it's an undocumented feature."
  "Real programmers count from 0."
  "A good programmer looks both ways before crossing a one-way street."
  "99 little bugs in the code… patch one… 127 little bugs in the code."
)

_secs() { printf "%02d:%02d" $(( ${1:-0} / 60 )) $(( ${1:-0} % 60 )); }
_repeat() { local n=${1:-0} c=${2:-#} out=""; while (( n-- > 0 )); do out+="$c"; done; printf "%s" "$out"; }

# run_with_progress <estimated_seconds> <label> <command...>
# Runs the command in the background; draws a live bar with ETA + rotating jokes.
# Sets LAST_LOG to the command's captured output. Returns the command's exit code.
run_with_progress() {
  local est=${1:-240} label=${2:-Working}; shift 2
  LAST_LOG="$(mktemp "${TMPDIR:-/tmp}/ember_build.XXXXXX")"
  "$@" >"$LAST_LOG" 2>&1 &
  local pid=$! start=$SECONDS width=26
  local cols; cols=$(tput cols 2>/dev/null || echo 80)
  local ji=$(( RANDOM % ${#JOKES[@]} )) lastj=0

  if [ ! -t 1 ]; then                     # not a real terminal (piped/CI): just wait quietly
    wait "$pid"; return $?
  fi

  printf '\n\n\033[2A'                     # reserve the joke line + bar line, park on the joke line
  while kill -0 "$pid" 2>/dev/null; do
    local el=$(( SECONDS - start ))
    (( el - lastj >= 10 )) && { lastj=$el; ji=$(( (ji + 1) % ${#JOKES[@]} )); }
    local pct=$(( est > 0 ? el * 100 / est : 99 )); (( pct > 99 )) && pct=99
    local fill=$(( pct * width / 100 )); local eta=$(( est - el )); (( eta < 0 )) && eta=0
    local bar; bar="$(_repeat "$fill" '#')$(_repeat $(( width - fill )) '-')"
    local etastr="ETA ~$(_secs "$eta")"; (( eta == 0 )) && etastr="almost there…"
    local joke="😄 ${JOKES[$ji]}"; joke="${joke:0:$(( cols - 2 ))}"   # truncate so it can't wrap
    printf '\r\033[K  %s\n\r\033[K  %s [%s] %3d%%  ⏱ %s  %s\033[1A\r' \
      "$joke" "$label" "$bar" "$pct" "$(_secs "$el")" "$etastr"
    sleep 1
  done
  wait "$pid"; local rc=$?
  local el=$(( SECONDS - start ))
  if [ "$rc" -eq 0 ]; then
    printf '\r\033[K  ✅ Nice — that part is done!\n\r\033[K  %s [%s] 100%%  ⏱ took %s\n' \
      "$label" "$(_repeat "$width" '#')" "$(_secs "$el")"
  else
    printf '\r\033[K  ❌ %s failed after %s\n\r\033[K\n' "$label" "$(_secs "$el")"
  fi
  return "$rc"
}

echo "==============================================="
echo "  Building Ember.app  (first build: 3-6 min)"
echo "==============================================="

# Ensure deps + PyInstaller are present.
if ! python3 -c "import PyQt6, google.genai" >/dev/null 2>&1; then
  run_with_progress 120 "Installing dependencies" python3 -m pip install -r requirements.txt \
    || { echo "Dependency install failed. Last lines:"; tail -20 "$LAST_LOG"; echo "Press Enter."; read _; exit 1; }
fi
python3 -m pip install --quiet --upgrade pyinstaller

rm -rf build dist
run_with_progress 240 "Building Ember.app" python3 -m PyInstaller --noconfirm --log-level=WARN Ember.spec \
  || { echo "Build failed. Last lines:"; tail -30 "$LAST_LOG"; echo "Press Enter."; read _; exit 1; }

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
