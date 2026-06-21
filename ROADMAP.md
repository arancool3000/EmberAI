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
