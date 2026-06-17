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
- **Local AI** — Ollama (offline, no key, no rate limit).
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

## 🆕 Shipped this session (was the backlog)
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
