# Ember — roadmap & idea memory

A running memory of what's shipped and what's next, so ideas aren't lost between sessions.

## ✅ Shipped
- **Plans / Pro** — all Pro features unlocked, free.
- **Security suite** — antivirus (file/dir scan, heuristics, quarantine, VirusTotal), run-in-sandbox
  (Docker / macOS `sandbox-exec`), web protection, secret redaction, tamper-evident audit log,
  read-only / capability modes, Security settings tab with buttons.
- **VPN** — bring-your-own WireGuard (no Homebrew needed; "Get free config" → ProtonVPN/Windscribe/WARP).
- **Ember Browser** — secure AI-first browser: tracker blocking, Ember Search (AI answer + web results +
  instant math + multi-engine), summarize/ask, AI-content check, reader mode, per-site dark mode,
  bookmarks, history, downloads, find-in-page, zoom; Gemini or Claude.
- **Chrome extension** — summarize / ask / AI-check via your own Gemini key.
- **AI detector** — text (heuristics) + images (metadata/provenance).
- **Local AI** — Ollama (offline, no key, no rate limit). Selectable as Ember's brain in the
  model picker ("Local (Ollama)") via `ollama_agent.py` + the "Local AI" Command Center app /
  `/localai`; chat-only (no computer control). Also available as the `local_ai_*` tools.
- **Creative AI** — image generation, vision Q&A, audio transcription.
- **Macros** — save / list / run / delete named task workflows.
- **Tools** — 176 total: multitools (cleanup, network, media, privacy), charts (matplotlib),
  documents (PDF/docx/xlsx), secret scan, secure-delete, unit convert, network connections,
  security checkup, + 28 text/data/math utilities.
- **Reliability** — rate-limit pacing + mid-turn wait-and-retry (no model-switch context loss),
  React/Vue-safe form fill, multi-occurrence click safety, faulthandler, EMBER_SAFE_MODE.
- **UX** — resizable window, 3-way size cycle (normal/full/compact-chat), opaque/readable theme,
  no focus-stealing, fixed chat-bubble layout, modern browser UI.
- **Perf** — lean-tools mode (ON by default), non-blocking VPN status, debounced resize.
- **Launch** — MIT license, public README, secrets gitignored, no-Homebrew uv installer,
  offline launch, auto-update on launch (git pull for source / auto-install for the app),
  Ember-site links fixed to EmberAI.

## 🆕 Shipped this session — launch-at-login + message grow-in animation
- **True always-on wake** (`autostart.py`) — optional login item so Ember starts at login
  and relaunches on crash (macOS LaunchAgent / Windows Run key / Linux .desktop), so the
  wake word works without manually opening the app. Pure plist/.desktop builders are
  unit-tested (`test_autostart.py`). Toggle in Settings → Performance ("Launch Ember at
  login"); default off (a login item shouldn't install without consent). A clean Quit stays
  quit (KeepAlive only on abnormal exit).
- **Message grow-in animation** — new chat bubbles animate height 0 → natural with an easing
  curve, then release the cap. Deliberately layout-SAFE: no QGraphicsEffect (those regressed
  bubble width/word-wrap before), the empty streaming bubble is skipped (so live text isn't
  clipped), and the cap always ends unbounded so a bubble can never be left collapsed.
  Toggle: `bubble_animation`.
- Opened EmberAI PR for the whole post-#42 range so all of this reaches `main`/the app.

## 🆕 Shipped this session — background wake, quieter chat, animated UI
- **"Hey Ember" works with the window closed** — `app.setQuitOnLastWindowClosed(False)`;
  closing the window now hides Ember to the tray (one-time "still listening" notice) and
  keeps the wake-word + monitors alive. Saying "Hey Ember" un-hides and focuses the window.
  Real quit is the tray ▸ Quit (`_do_quit`, stops the listeners). Toggle:
  `keep_running_in_background` (default on) in Settings → Performance. (True "process fully
  quit" wake still needs an OS LaunchAgent — not done; tray/background covers reopen.)
- **Completed tasks no longer clutter the chat** — successful tool results update the live
  status line ("✓ <tool>") instead of posting a bubble; only failures still surface as
  messages. The chat stays a real conversation.
- **Glowing rearranging "thinking" dots** (`siri_glow.ThinkingDots`) — replaces the text-dot
  indicator with a cluster of glowing dots that pulse and drift past each other (~60fps).
- **Liquid menus** — rounded, padded, translucent QMenu styling with soft accent hover added
  to both themes (menus previously used default Qt chrome).
- Note: a full iOS-27 restyle (per-bubble slide/grow/fade + message glow) is iterative and
  needs live tuning; per-bubble QGraphicsEffects are deliberately avoided here because they
  previously broke chat-bubble width/word-wrap. Window fade, smooth scroll, the Siri edge
  glow, and the thinking dots provide the motion now.

## 🆕 Shipped this session — the model decides when to look + smarter prompting
- **Screenshots on demand, not on keywords** — removed the `_SCREEN_HINTS` heuristic that
  auto-attached a screenshot whenever a message merely mentioned "screen"/"click"/"open"
  (it even fired on browser tasks that should use the DOM). The model now DECIDES per turn
  whether it needs to see pixels and calls `take_screenshot` / `read_screen_text` itself.
  Applied to both the Gemini and Claude backends (claude_agent shared the removed heuristic,
  which also fixes an import-time crash it would have hit).
- **`auto_screenshot` is now a privacy control** — ON (default): Ember decides when to view
  the screen; OFF: it never captures the screen (browser DOM / files / shell only), enforced
  via a per-turn directive. Settings checkbox relabeled + tooltip.
- **Smarter system prompt** — a new "Deciding when to look at the screen" section (see vs.
  browser-DOM vs. no-capture), batch take_screenshot + read_screen_text for a one-pass read,
  and a sharper reasoning directive (restate the real goal, plan multi-step work, self-check
  each result, escalate hard reasoning to ask_claude instead of guessing).

## 🆕 Shipped this session — "Hey Ember" wake word + Siri-style glow
- **Always-on wake word** (`wake_word.py`) — a background daemon keeps the mic open and
  fires when it hears "hey ember" (fuzzy match via rapidfuzz, so "hey amber"/"okay ember"
  also trigger). Runs forever (restarts on hiccups), pauses only while a command is being
  captured so it doesn't fight the command recogniser, and prefers offline PocketSphinx
  (Google STT fallback). Default ON; toggle in Settings → Voice. Detection + lifecycle are
  unit-tested with injected capture (`test_wake_word.py`, 7) — no audio needed.
- **Siri "Golden Gate" glow** (`siri_glow.py`) — a click-through overlay that sweeps a
  flowing, breathing band of light (warm-leaning rainbow conical gradient, multi-layer
  bloom, ~60fps) around the window edge while Ember is **listening / thinking / speaking**,
  each state tuned for speed + brightness. Wired into the voice + turn lifecycle (covers
  typed turns too) and resizes with the window. Toggle in Settings → Voice.
- Wiring: a `wake_detected` bridge signal marshals the wake to the UI thread →
  starts a voice turn; glow shows on listen/think/speak and dims when idle; wake word
  pauses/resumes around mic use so the two never collide.

## 🆕 Shipped this session — pixel-accurate mouse + rate-limit resilience
- **Accurate mouse** (`human_mouse.py`) — humanized travel stays, but the pointer now
  **snaps to the exact integer target** at the end of every move, and clicks/presses are
  issued at **explicit coordinates** (and drags press/release at exact start/end points),
  so realism never costs accuracy. New driver tests assert clicks land on the exact pixel.
- **Rate-limit resilience without losing history** (`agent.py`) — on a 429 Ember now, in
  order and **always preserving chat history**: (1) instantly retries the same model on the
  alternate API key, (2) waits out the per-minute limit and retries the same chat, (3)
  switches to a fallback model. `_init_chat(history=…)` + `_capture_history()` carry the full
  conversation (including a mid-turn tool call) into the new key/model, so switching no longer
  wipes context or forces a reset — mid-turn switches now work too.

## 🆕 Shipped this session — scheduler, integrations + cross-feature polish
- **Background agent scheduler** (`agent_scheduler.py`) — a daemon that ticks on a
  timer, asks `agents.due_agents()` what's due and runs each via a registered runner
  (the UI wires one that spawns the agent as a scoped sub-agent and posts a notify()).
  Completes the Base44-style "always-on agents on a schedule." Autostarts at launch.
  Tools: `scheduler_status/events/run_due/start/stop`. Tested (`test_agent_scheduler.py`, 7).
- **Integrations** (`integrations.py`) — push updates to Slack / Telegram / Discord /
  generic webhook with just a webhook URL or bot token (no OAuth). `notify()` fans out
  to all configured channels; secrets masked in listings. Tools: `notify`,
  `integration_set/list/remove`. UI: a Notifications section (connect a channel, send
  test). Tested offline via injected HTTP (`test_integrations.py`, 10).
- **Archive scanning** (`antivirus.py`) — the file scanner now looks INSIDE zip
  archives for malicious members (EICAR / signature byte-match -> malicious; disguised
  executables / IOC content -> suspicious), bounded against zip bombs. The signature DB
  also gained `bad_ips` for the network scanner.
- **Security → notifications** — when enabled (`sc_notify`), the Security Center pushes
  real threats (suspicious/malicious) to the connected channels. Off by default.
- Tests extended: archive cases in `test_antivirus.py` (now 20), notify-hook cases in
  `test_security_center.py` (now 12).

## 🆕 Shipped this session — agent UX: run modes, agents, human mouse, tool polish
- **Human-like mouse movement** (`human_mouse.py`) — replaces the old jerky
  `moveTo(duration=0.08)` teleport with a curved (cubic-Bézier) path, ease-in/out
  timing, distance-scaled speed, micro-jitter and overshoot-and-settle on long moves.
  Routed through `tools.click/move_mouse/drag` + `screen_vision` drag-select (graceful
  fallback if pyautogui is missing). Toggle in Settings → Security → Pointer.
  Pure path math is unit-tested (`test_human_mouse.py`, 10).
- **Run modes (like Claude)** + **named agents (like Base44 Superagents)** (`agents.py`) —
  run modes `auto` / `plan` / `chat` / `read_only` map to capability + a live
  system-prompt directive (framed into every turn). Named agent profiles: a goal,
  default run mode, **tool scope** (permission control via categories/allow/deny),
  optional model, and an optional **schedule** (`every_minutes` / `daily_at`) so they
  can run on a timer. CRUD + scheduling + scope resolution + `build_run_request` are
  pure and tested (`test_agents.py`, 16). Tools: `list_run_modes`, `set_run_mode`,
  `agent_create/list/get/delete`, `agent_run`. UI: a Run-mode selector + Agents list
  in Settings → Security.
- **Sub-agents (like Claude's Task)** — `spawn_agent` and `agent_run` launch a fresh,
  scoped sub-agent that runs its own bounded tool loop and reports a summary; it
  forwards events to the parent UI (so progress + confirmations are visible/answerable),
  carries an isolated tool whitelist + run mode, and is recursion-bounded.
- **Tool-use polish** — `tool_args.py` coerces every tool argument to its declared type
  before dispatch ("100"→100, "true"→True, 3.0→3), killing a big class of "bad args"
  failures. Applied in the executor; unit-tested (`test_tool_args.py`, 7).

## 🆕 Shipped this session — always-on antivirus + fileless detection
- **Fileless-malware detection** (`fileless_guard.py`) — an always-active background
  process monitor for in-memory / "living-off-the-land" attacks that file scanners
  miss. On launch it sweeps every running process, then watches for new ones, scoring
  each command line with the shared behavioral IOC engine (encoded PowerShell,
  download-and-execute, reverse shells, LOLBins, ransomware shadow-copy wipes,
  credential dumping, miners, AV-tampering, obfuscation) **plus process lineage**
  (e.g. Word → PowerShell). Alerts by default; optional auto-terminate. psutil with a
  `ps`/`wmic` fallback, fully injectable for tests. Tools: `scan_processes`,
  `scan_command`, `fileless_guard_start/stop/status/events`.
- **Much stronger antivirus** (`antivirus.py`) — added **Shannon-entropy** packer
  detection, a **behavioral IOC/signature engine** (`scan_text_iocs` /
  `scan_command_line`) reused by the fileless monitor, an **extensible on-disk
  signature DB** (`signatures.json`: hashes + byte patterns), and richer scan reasons.
  The "heuristics never auto-delete" safety rule is preserved — file heuristics still
  cap at *suspicious*; only definitive signals (EICAR / signature hit / known-bad hash
  / platform-AV / VirusTotal) quarantine.
- **Always active** — real-time download protection AND the fileless monitor now
  autostart at launch (default ON) and have toggles in **Settings → Security**
  ("Real-time protection (always active)" + "Scan running processes now"). New tests:
  `test_fileless_guard.py` (13) + entropy/IOC/signature cases in `test_antivirus.py`.
- **Security Center** (`security_center.py`) — a unified, always-on supervisor that
  turns the individual defenses into one continuous, self-healing layer. It actively
  and repeatedly scans **every** surface on its own schedule: processes (keeps the
  fileless monitor alive), files (keeps the download watcher alive + periodically
  sweeps Downloads/Desktop/Documents/Temp), **network** connections + listening
  ports (reverse-shell listeners, C2/mining, interpreters phoning home), and
  **persistence/autostart** (cron, launchd, systemd, shell rc files, registry Run
  keys, Startup folder — each command scanned by the IOC engine, with baseline-diff
  for new entries). A **watchdog** restarts any monitor that dies so scanning never
  stops. Findings funnel into one bounded, de-duplicated threat feed. Threat-intel is
  extensible via `signatures.json` (`bad_ips` added alongside hashes/patterns).
  Autostarts at launch; **Settings → Security** gets a "Security Center" section with
  Full scan / Scan network / Scan persistence / Activity buttons. Tools:
  `security_center_start/stop/status/events`, `run_full_scan`, `scan_network`,
  `scan_persistence`. New `test_security_center.py` (10).

## 🆕 Shipped previously (was the backlog)
1. **Plugin system** (`plugin_system.py` + `plugins/`) — drop a `.py` defining `EMBER_TOOLS` into
   `plugins/` and it auto-registers as tools at startup. Broken plugins are skipped, never crash
   the app. Tools: `list_plugins`, `reload_plugins`, `create_plugin_template`. Example + README ship
   in `plugins/`.
2. **Encrypted key vault** (`key_vault.py`) — API keys stored in the OS keychain (via `keyring`) or
   an encrypted Fernet file fallback. Settings UI toggle (Models tab); `load_settings`/`save_settings`
   hydrate/redact keys so `settings.json` holds no plaintext keys when on. Tools: `vault_*`.
3. **Real-time download protection** (`download_guard.py`) — background watcher scans new Downloads
   files via the antivirus engine (verdict-based), skips partial downloads. Performance-tab toggle +
   launch autostart. Tools: `download_guard_start/stop/status/events`.
4. **Usage dashboard** (`usage.py`) — tracks calls/tokens per day + rolling minute vs the 15/min &
   500/day free-tier limits; recorded on every model response in `agent._process_response`. Dialog
   button in the Performance tab. Tool: `usage_summary` (+ `usage_reset`).
5. **Tab groups + password manager** in Ember Browser (`browser_passwords.py` + `ember_browser.py`) —
   🔑 toolbar button to save/fill/manage per-site logins (stored in the encrypted vault, never exposed
   to the LLM; JSON-safe autofill JS), and a right-click tab-bar menu to colour/assign tabs to groups.
6. **Workflow recorder** (`workflow_recorder.py`) — record real mouse+keyboard input (pynput) and
   replay by name at adjustable speed. Tools: `record_workflow_start/stop`, `replay_workflow`
   (classified high-risk → confirm), `list_workflows`, `delete_workflow`.
7. **Productivity tools** (`productivity_tools.py`) — snippet expander (`snippet_*`, `;keyword`),
   email breach monitor (`email_breach_check`, free XposedOrNot), screen recorder
   (`screen_record_*`), screen color picker (`pick_screen_color`), multi-monitor screenshot
   (`screenshot_monitor`). Plus **theme/appearance presets** in the Appearance tab. (Clipboard
   history already existed.)
8. **Real GitHub Release** — `.github/workflows/release.yml` builds the macOS `.app` + Windows `.exe`
   on native runners on a `v*` tag (or manual dispatch), generates `latest.json` (sha256 + URLs), and
   publishes a Release with all assets → enables the in-app auto-updater. ⚠ Builds are **unsigned**
   (no Apple/Windows certs in CI) — mac users run `unblock-mac.sh` or right-click → Open. Adding
   notarization/signing (secrets + `notarize_mac.sh`) is the remaining polish.

Total: **30 new built-in tools** (288 total, 0 duplicate names) + dynamic plugin tools; **73 new
tests** pass. Lean-tools mode hides the productivity utilities; vault/usage/download-guard/plugins/
workflow stay core.

## 🔭 Backlog (next ideas)
- **Release signing/notarization** — Apple Developer ID + notarytool (and Windows Authenticode) in CI
  so the published builds open without the Gatekeeper warning.
- **Cross-session tab-group persistence** — groups are currently session-scoped (colour only).
- **Password autofill on submit-capture** — currently save is a manual prompt; capturing creds from a
  real form submit (via QWebChannel) would be more automatic.
