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

## 🔭 Backlog (ideas, not yet built)
1. **Plugin system** — drop a `.py` in `plugins/` → it auto-registers as a tool. Extensible by anyone.
2. **Encrypted key vault** — store API keys in the OS keychain instead of plaintext `settings.json`.
3. **Real-time download protection** — background watcher auto-scans new files in Downloads.
4. **Usage dashboard** — live calls/tokens vs the 15/min + 500/day limits.
5. **Tab groups + autofill/password manager** in Ember Browser.
6. **Workflow recorder** — record real action sequences (beyond text macros) and replay.
7. **Email breach monitor**, **clipboard history**, **snippet expander**, **screen recorder**,
   **color picker**, **multi-monitor screen picker**, **themes/appearance presets**.
8. **Real GitHub Release** — build the macOS `.app` (BUILD_DESKTOP_APP.command), attach the zip +
   `latest.json`, tag `v1.0.0` → enables the in-app auto-updater download.
