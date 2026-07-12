# Ember — autonomous AI agent for your computer

## Install

Open the folder for your computer. Each contains a visual guide and one installer:

- **Windows:** [`Windows Install/`](Windows%20Install/) → double-click **Install Ember.bat**
- **macOS:** [`macOS Install/`](macOS%20Install/) → double-click **Install Ember.command**

Both installers keep dependencies in a private `.venv` inside the Ember folder. They do not
install Ember packages globally. The rest of this repository is source code and developer files;
new users do not need to open them.

Ember sees your screen, drives the mouse/keyboard, controls a real browser, runs shell
commands, manages files, organises your Gmail, talks with you hands-free, and can be controlled
from your phone — online or **fully offline**. macOS + Windows.
Free, MIT-licensed, and private — your API key stays on your machine; there are no Ember servers.

## ✨ What's inside

### 🤖 The agent
- **Autonomous agent** — 290+ tools: move the mouse/keyboard, read the screen with Vision OCR,
  drive a real browser via the DOM, run shell, manage files, and chain multi-step tasks.
  **Human-like mouse movement** with an **adjustable pointer speed** (curved, eased paths).
- **Run modes & agents** — pick how Ember works: **auto** (autonomous), **plan** (proposes a
  plan and waits), **chat** (talk only), or **read-only**. Define **named agents** (a goal +
  run mode + tool scope + optional schedule), run them on demand or **on a schedule**; Ember can
  also **spawn scoped sub-agents** for sub-tasks.
- **Any model** — **Gemini**, **Claude**, or **ChatGPT (OpenAI)**, plus any **OpenAI-compatible
  provider** (xAI Grok, DeepSeek, Groq, Mistral, OpenRouter, or a local LM Studio / vLLM server)
  by pasting that provider's key. An **Auto** option picks the best available, with automatic
  **key failover** (rotates through backup keys, then falls back across models).
- **Setup tour** — a friendly first-run wizard: pick your experience level, install the free
  offline AI in one click or paste a free key, optionally connect Gmail, and see what Ember can do.
- **Learns about you** — long-term memory of facts/preferences so it gets more useful over time.

### 🆕 Productivity
- **Standalone utilities** — Storage Inspector finds large and duplicate files with reversible
  Trash actions; Network Inspector shows Wi-Fi, active connections, listening ports, and nearby
  devices; Clipboard History temporarily captures copied text while its window is open.
- **Organise your Gmail** — search (Gmail's own query syntax), label/file, archive, star, mark
  read/unread, create labels, and trash (recoverable) — *"clean up my inbox."* Also sends email.
  One Google **App Password** powers both.
- **Timers & reminders** — *"set a 10-minute timer"* → desktop notification + sound + a chat note.
- **Voice** — hands-free **"Hey Ember"** wake word, **push-to-talk** (hold a key, talk, release —
  zero-latency, no wake word, transcribed locally with **Whisper** when installed), voice chat
  (listen → act → speak) with an **automatic turn length** (stops when you pause), **natural voice**
  via the Gemini **Live API** (real-time, the AI handles turn-taking), and free **Edge neural**
  read-aloud (no key).
- **Macros, scheduling & workflows** — save/replay task macros, schedule actions for later, and
  **record & replay** real mouse/keyboard workflows.
- **Built-in terminal + Python runner** — run shell commands and Python in-app (`/terminal`), with
  a Python session that persists between runs — no need to leave Ember.
- **Parallel agent tasks** — kick off several Ember jobs at once and track each one's live status
  and output in a dashboard (`/agents`); stop any of them.
- **Phone Link** — control your computer from your phone's browser on the same Wi-Fi, with an
  opt-in **connect from anywhere** mode: pair once on Wi-Fi, then reach your computer from any
  network via a Cloudflare Tunnel — the short PIN never leaves your LAN (only a long pairing
  token, minted after a Wi-Fi pairing, is accepted remotely).
- **Quit-proof global hotkey** — summon Ember from anywhere, even when it's fully closed.
- **Complete MCP server** — Ember can be controlled from **ChatGPT**, Claude Desktop, Cursor,
  or any MCP client. Its live tool registry becomes ChatGPT-compatible MCP tools over stdio or
  Streamable HTTP, with real schemas and impact annotations. Off by default; capability modes and high-risk
  actions blocked unless you opt in. See **[docs/MCP.md](docs/MCP.md)**.

### 🌐 Browser & web
- **Ember Browser** — a secure, AI-first browser: tracker/ad blocking, an AI-answer search page,
  summarize/ask about any page, **AI extension maker**, AI-content check, reader mode, per-site
  dark mode, saved passwords, bookmarks, download history/progress, privacy controls, session
  recovery, closed-tab restore, duplicate tabs, tab search, and standard browser shortcuts.
- **System-wide ad blocker** — its own menu; blocks ads/trackers across the whole machine (hosts).
- **Chrome extension** — summarize / ask / AI-check any page from your browser.

### 🔌 Offline & local
- **Offline Mode** — run with **no internet**: a local brain (Ollama) plus every local tool
  (files, shell, screen, system info). Cloud-only tools fail fast with a clear notice.
- **Local AI that drives the computer** — the free offline model (Ollama) can now run terminal
  commands, read/write files, see the screen (screenshot + OCR), move the mouse/type, and
  **analyse images** with a local vision model — not just chat. No API key, no rate limits.
  Text-only local models no longer crash on screen-reading requests (Ember detects vision
  support and falls back to OCR text), and tool calls a local model writes as plain text now
  run reliably instead of leaking raw JSON.
- **Pick who names your chats** — auto-title new chats with a tiny **local Ollama** model
  (fully offline, free) or a small free **Gemma** (1B/4B/12B/27B), chosen from a dropdown in
  Settings → Models.

### 🛡️ Security
- **Endpoint security console** — local file analysis, optional platform/cloud reputation,
  behavioral process monitoring, network and persistence inspection, containment, sandboxing,
  web protection, and an evidence-led activity report. Coverage and limitations are shown in
  the product instead of being hidden behind a marketing “safe” score. (Full detail below.)

### 🔔 Other
- **Notifications** — connect **Slack, Telegram, Discord, or a webhook** so agents and security
  alerts can push you updates; `notify` sends to every channel.
- **Image gen, vision Q&A, audio transcription, and AI text/image detection.**
- **Self-update** — built-in updater pulls new releases (verified download) and relaunches.
- **Encrypted key vault, custom AI-authored tools, and a plugin system.**

---

## ▶️ Run it (easiest)

For a first installation, use **`macOS Install/Install Ember.command`** or
**`Windows Install/Install Ember.bat`**. Each opens its installation guide first.

After setup: **macOS** double-click **`Ember.command`**; **Windows** double-click **`Ember.bat`**.

> **macOS first-launch note:** Ember is free and unsigned, so macOS Gatekeeper
> blocks the `.command` files on first use with *"Apple could not verify … is free
> of malware."* On **macOS 15 (Sequoia)** the old right-click → **Open** trick is
> gone — the dialog only offers *Done* / *Move to Bin*. **Don't pick "Move to Bin"**
> (it deletes the file); click **Done**, then clear the block once with any one of
> these:
>
> **Easiest — one Terminal command that unblocks *and* launches Ember:**
> ```bash
> bash /path/to/the/Ember/folder/unblock-mac.sh
> # tip: type "bash " then drag unblock-mac.sh from Finder into Terminal, press Return
> ```
> **Or strip the quarantine flag yourself:**
> ```bash
> xattr -dr com.apple.quarantine /path/to/the/Ember/folder
> ```
> **Or via System Settings:** Privacy & Security → scroll to the blocked file →
> **Open Anyway**.
>
> After that, the `.command` files double-click normally (each one also re-clears
> the folder's quarantine flag when it runs). This is expected for any app not
> paid-notarized through Apple — it's a macOS packaging gate, not a problem with
> Ember.

The first launch installs everything automatically (a few minutes); after that it just opens.
- **macOS:** nothing to install first — double-clicking `Ember.command` fetches `uv`
  (which brings its own Python 3.12), so you need **neither Homebrew nor a system Python**.
- **Windows:** install Python from [python.org](https://www.python.org/downloads/) and check **“Add Python to PATH.”**

> Voice/microphone **input** (`pyaudio`) is optional and installed separately — it has
> no macOS/Linux wheel and needs the `portaudio` C library. To enable it:
> `brew install portaudio && uv pip install -r requirements-voice.txt`. Everything
> else, including voice **output**, works out of the box.

On first run, paste a free **Gemini API key** (get one at https://aistudio.google.com/apikey)
into Settings (⚙). For Claude models, add an Anthropic API key too.

### Run from source in Terminal (no build, no Homebrew)

The quickest way to run Ember without building an app. It uses
**[uv](https://docs.astral.sh/uv/)**, which installs its *own* Python — so you do
**not** need Homebrew or any pre-installed Python, and it avoids the slow
dependency-resolution stalls you can hit with the old system Python.

**1. Install uv** (one line — no Homebrew):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

**2. Go into this folder.** In Terminal type `cd ` (with a trailing space), then
drag the Ember folder from Finder onto the window and press Return. Confirm you're
in the right place — this must list both files:
```bash
ls main.py requirements.txt
```

**3. Install dependencies and launch:**
```bash
uv venv --python 3.12
uv pip install -r requirements.txt
uv run python main.py
```

> ⚠️ When copying commands, **never paste lines that start with `#`** — your shell
> tries to run them and errors. Copy only the actual commands.

Prefer your own Python? `python3 -m pip install -r requirements.txt --prefer-binary && python3 main.py` (needs Python 3.10+).

---

## 📦 Make it a real standalone app (free)

Turn Ember into a double-clickable app that needs **no Python and no terminal** to run:

- **macOS:** double-click **`BUILD_DESKTOP_APP.command`** → produces **`dist/Ember.app`**
  *and* **`dist/Ember.dmg`**. (Uses PyInstaller — completely free.)
- **macOS drag-to-Applications:** open **`dist/Ember.dmg`** and drag Ember into the
  Applications folder — the same experience as a normal Mac app download. (You can
  rebuild just the dmg any time with `bash make_dmg.sh`.)
- **Windows:** run `python -m PyInstaller --noconfirm Ember.spec` → produces `dist/Ember/Ember.exe`.

The build is unsigned, so Gatekeeper gates the first launch. On **macOS** double-click
`Ember.app`, then open System Settings → Privacy & Security → **Open Anyway** (on macOS
versions before Sequoia, right-click → **Open** also works). On **Windows** choose
“More info → Run anyway.” That’s normal for free, self-built apps.

> **“Why isn’t this just drag-and-drop like the Claude app?”** It can be — that’s
> what `dist/Ember.dmg` gives you (open it, drag Ember into Applications). The one
> remaining difference: apps like Claude are **notarized** by Apple (a paid Developer
> ID), so macOS shows no warning at all. Ember is free and unsigned, so the very
> first launch still needs the one-time **Open Anyway** step above. Add Apple
> notarization and even that disappears.

---

## ✨ Recent additions

- **Organise your Gmail** — search, label, archive, star, mark read/unread, create labels, and
  trash email on request. Set it up in Settings → Models → Gmail (address + a Google App Password).
- **Timers** — *"set a 10-minute timer"*; fires a notification + sound + a chat message.
- **Offline Mode + a local brain that controls the computer** — Ollama can now run shell, manage
  files, see the screen (OCR), drive mouse/keyboard, and analyse images — fully offline.
- **First-run setup tour** — experience level → install the free offline AI or paste a key →
  optionally connect Gmail → a quick "what you can do" summary.
- **Automatic voice turn length** — Voice Chat ends a turn when you naturally pause (no fixed cap),
  plus **natural voice** (Gemini Live API) where the AI handles turn-taking.
- **AI safe-open antivirus** — unconfirmed executables/scripts (or anything flagged) are AI-scanned
  and **held until you confirm** they're safe; confirmed files are remembered.
- **Smarter network scan** — finds connections on macOS via `lsof` (no root needed) and reports a
  useful breakdown (active vs listening, top remote hosts) instead of an empty result.
- **Self-update that works** — the updater bundles CA roots so it reliably finds new releases, and
  reports a real error instead of silently saying "up to date."
- **Conversational "Hey Ember"** — say it once, then keep talking; just a glow, no window pop-up.
- **Auto model selection + key failover, free Edge neural TTS, adjustable mouse speed, custom icon
  set, audio-reactive glow/orb, and a quit-proof global hotkey.**

---

## 🔐 Permissions (grant on first run)

System Settings → Privacy & Security:
- **Screen Recording** — so Ember can see the screen
- **Accessibility** — mouse/keyboard control + reading UI elements
- **Input Monitoring** — the global hotkey
- **Microphone** — only if you use voice

---

## 📱 Control your PC from your phone (Ember Link)

Ask Ember to “start remote control,” or it runs the `start_remote_control` tool. You’ll get a
**URL + 4-digit PIN**. On your phone (same Wi-Fi) open the URL, enter the PIN, and you get
**Ember Link**: a faster mirrored screen with tap, click-and-drag, fullscreen mirror,
trackpad, keyboard, quality controls, **one-tap quick-actions** (Lock PC, Mute, Mic Off,
Sleep Screen, and a custom-command box), and a Chat tab for telling Ember what to do remotely.
It’s LAN-only and PIN-gated; stop it when done.

**iPad / tablet:** open the link in Safari, then **Share → Add to Home Screen** — Ember Link
installs with its own icon and launches full-screen like a native app (it’s a PWA), with an
iPad-optimised layout. A native iPad app can’t control a computer (iOS sandboxing), so this
client is the supported way to use an iPad with Ember.

**Connect from anywhere (opt-in):** in the Ember Link panel, check **“Connect from anywhere.”**
This starts an outbound [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(`brew install cloudflared` on macOS) and shows a public link. The flow is *pair on Wi-Fi, then
roam*: the first time a device connects on your Wi-Fi and enters the PIN, it silently exchanges
that for a long-lived pairing token — after that, the same device can reconnect from any network
using the public link, no PIN needed. The short PIN itself is **never** accepted over the public
tunnel (only from a real LAN address), so there’s nothing for the internet to brute-force. Revoke
every paired device any time from the same panel.

---

## 🛡️ Built-in malware defense

Ember scans what it downloads and what you ask it to open, can watch running
processes for suspicious command-line behaviour, holds unconfirmed risky files,
and contains confirmed malware. The console reports what is actually running and
calls out missing providers. A result of **no known indicators** is not proof that
a device or file is safe.

- **Security Center** — a best-effort supervisor monitors several high-value endpoint
  surfaces and restarts enabled monitors that stop:
  - **Processes** — the fileless-malware monitor (below).
  - **Files** — a real-time **download monitor** scans new files the moment they
    finish downloading, plus periodic sweeps of Downloads/Desktop/Documents/Temp.
  - **Network** — repeatedly inspects active connections + listening ports for
    reverse-shell listeners, C2 / mining traffic and interpreters phoning home.
  - **Persistence** — repeatedly inspects autostart locations (cron, launchd,
    systemd, shell rc files, registry Run keys, the Startup folder) and scans each
    entry's command line.

  Everything funnels into one threat feed (`security_center_status` /
  `security_center_events`); run an on-demand sweep with `run_full_scan`,
  `scan_network`, `scan_persistence`, or the buttons in **Settings → Security**.
- **Fileless-malware detection** — file scanners miss attacks that never touch the
  disk. Ember classifies every process's command line with a behavioral
  IOC/signature engine that catches **encoded PowerShell**, **download-and-execute**
  (`IEX (New-Object Net.WebClient).DownloadString …`), **reverse shells**
  (`bash -i >& /dev/tcp/…`, `nc -e`), **LOLBins** (certutil / mshta / regsvr32 /
  rundll32 / bitsadmin / wmic), **ransomware** shadow-copy wipes, **credential
  dumping** (LSASS / mimikatz), **crypto-miners**, AV-tampering and heavy
  obfuscation — plus suspicious **process lineage** (e.g. Word spawning PowerShell).
  Run it on demand with the `scan_processes` / `scan_command` tools, or **Scan
  running processes now** in Settings → Security. It alerts by default and can be
  set to auto-terminate confirmed-malicious processes.
- **Scan on download / before open** — every downloaded file (and any file Ember
  is about to open) is scanned with local heuristics (executable-disguised-as-a-
  document, double extensions like `invoice.pdf.exe`, macro-laden Office files,
  **Shannon-entropy packer detection**, **behavioral content signatures**, an
  **extensible signature DB**, known-bad hashes), the platform antivirus
  (**Windows Defender** / **ClamAV** if installed), and **VirusTotal** (hash
  lookup when a key is set). Unknown file contents are **not uploaded by default**;
  sample sharing requires a separate explicit opt-in. Suspicious or malicious
  files are **not opened until the scan finishes**.
- **Deep static analysis** — beyond hashes and entropy, Ember parses files structurally:
  **PE/ELF/Mach-O** headers for packer sections (UPX/Themida/VMProtect…), writable-and-
  executable (W^X) sections and missing import tables; **PDF** actions (`/JavaScript`,
  `/OpenAction`, `/Launch`, embedded files); **Office VBA macros** (auto-run entry points +
  dangerous APIs); and **scripts** — it de-obfuscates base64/hex/char-code payloads and runs a
  Python **AST** pass so `eval`/`exec`/`os.system` are caught through renaming. Works out of the
  box (pure-Python); installing `requirements-security.txt` upgrades it with **YARA**, real
  **olevba** macro extraction, **lief** parsing, **ssdeep** fuzzy hashing and RAR/7z unpacking.
- **AI-assisted triage** — anything **unconfirmed and risky** (an executable/script, anything
  the scanner flags, or a file it couldn't scan) can receive an AI advisory and is **held until
  you confirm** it. AI never clears, restores, or downgrades engine evidence. Confirmed files
  are remembered by content hash; definitive malware remains blocked and contained.
- **Evidence-preserving quarantine** — confirmed-malicious files are moved to a locked,
  non-executable vault. Findings cannot be directly deleted: containment comes first, then a
  human may restore or permanently delete from Quarantine. Legacy automatic-delete policy is
  migrated to retention. Manage it with the `list_quarantine`,
  `restore_quarantined`, and `delete_quarantined` tools.
- **Activity chain + report export** — scans, containment, restoration, deletion, and policy
  changes are written to a locally verifiable hash chain. The console exports a redacted JSON
  report containing protection state, coverage gaps, evidence inventory, and explicit limits.
- **Sandbox unknown programs** — `run_in_sandbox` runs a file in the strongest
  isolation available (Docker with no network → macOS `sandbox-exec` / Windows
  restricted token → otherwise refuse) so its behaviour can be observed without
  risking your machine. If no isolation is available, Ember refuses to run it
  rather than running it unprotected.

**Optional, stronger protection** (all auto-detected — none required):
- **VirusTotal:** set a `VIRUSTOTAL_API_KEY` (free at virustotal.com) for cloud
  reputation. Only SHA-256 hashes are sent unless sample sharing is explicitly enabled.
- **ClamAV** (`brew install clamav` / `apt install clamav`) for an on-device engine.
- **Docker** for the strongest sandbox.

Settings live in `~/Library/Application Support/Ember/security.json` (macOS) /
`%LOCALAPPDATA%\Ember\security.json` (Windows); the `security_status` tool reports
what protection is currently active.

---

## Free, with the complete local toolset

Every local feature and MCP tool is permanently free: antivirus, sandbox, deep scans, agents,
browser control, utilities, VPN management, reputation integrations, and the full UI. There is
no licence check or upgrade tier. `get_plan` remains only for older plugins and always reports
the single free tier. Third-party services may still require their own account or credentials.

### VPN (bring-your-own WireGuard)
Ember isn't a VPN provider — it manages WireGuard configs **you** add (Mullvad,
ProtonVPN, your own server) and connects via `wg-quick`. Add one `.conf` per location
with `add_vpn_location`, then `vpn_connect` / `vpn_disconnect` / `vpn_status` /
`list_vpn_locations`. Needs `wireguard-tools` installed and admin rights to bring a
tunnel up — and it never claims to be connected when it isn't.

---

## 🧱 More security layers

- **Web protection** — every navigation (`open_url`, `browser_open`,
  `browser_navigate`) is checked against block/allow lists, known malware/phishing
  domains, live reputation (URLhaus free; VirusTotal / Google Safe Browsing with a
  key), and look-alike / typosquat detection. Blocked sites don't load; look-alikes
  are flagged. Manage with `add_web_block` / `add_web_allow` / `list_web_policy`.
- **Secret redaction** — API keys, passwords, tokens, private keys and PII are
  stripped from the action log and audit trail (and from screenshots via
  `redaction.redact_image`) so they don't leak to disk or the cloud LLM.
- **Tamper-evident audit log** — every action Ember takes is appended to a
  hash-chained log; `verify_audit_log` proves nothing was altered and
  `get_audit_log` shows recent activity.
- **Capability modes** — cap Ember's blast radius with `set_agent_mode`: `full`,
  `restricted` (no high-risk actions), or `read_only` (safe read-only tools only).
  Ember can tighten its own mode but can't loosen it out of read-only without you.

---

## 🧠 What makes Ember accurate

- **`smart_click("Sign in")`** — finds the real on-screen target via the macOS/Windows
  accessibility tree, then Apple-Vision/Windows OCR, and clicks the exact center. No grid guessing.
- **`read_screen_text` / `select_screen_text`** — every visible word with exact coordinates.
- **Browser tools** act through the DOM, not pixels.

---

## 🛠 Troubleshooting

- **“Chat history corrupted / desynced”** — fixed; if you ever see it, just resend (Ember now
  auto-clears the bad turn).
- **Clicks do nothing** — grant Accessibility, then fully quit and reopen Ember.
- **Black screenshots** — grant Screen Recording, then reopen.
- **Voice/mic errors** — `brew install portaudio` (macOS), then reinstall requirements.

---

## File map

| File | Purpose |
|---|---|
| `Ember.command` / `Ember.bat` | one-click run |
| `unblock-mac.sh` | macOS: clear Gatekeeper quarantine + launch, in one `bash unblock-mac.sh` |
| `BUILD_DESKTOP_APP.command` | build standalone `Ember.app` (+ `Ember.dmg`) |
| `make_dmg.sh` | package `Ember.app` into a drag-to-Applications `.dmg` |
| `main.py` | entry point |
| `ui.py` | the desktop UI |
| `agent.py` | the AI agent loop + tool declarations |
| `tools.py`, `more_tools.py`, `extra_tools.py` | the tool set |
| `human_mouse.py` | human-like (curved, eased) mouse movement |
| `agents.py` | run modes + named agent profiles (scope, schedule) |
| `agent_scheduler.py` | background scheduler that runs due agents |
| `integrations.py` | Slack / Telegram / Discord / webhook notifications |
| `tool_args.py` | coerces tool arguments to their declared types |
| `screen_vision.py` | exact clicking + on-screen OCR |
| `remote_server.py` | Ember Link phone control (LAN + pairing-token auth for remote access) |
| `tunnel.py` | outbound public tunnel (Cloudflare Tunnel) for Ember Link's "connect from anywhere" |
| `antivirus.py` | malware scan (entropy + IOC signatures), quarantine vault & sandbox |
| `security_center.py` | unified always-on active scanning (processes/files/network/persistence) + watchdog |
| `fileless_guard.py` | always-on fileless / behavioral process monitor |
| `download_guard.py` | real-time download folder scanner |
| `web_policy.py` | website blocking + URL reputation |
| `redaction.py` | strip secrets/PII from logs, audit & screenshots |
| `audit.py` | tamper-evident action audit log |
| `plan.py` | compatibility access API; every capability is permanently free |
| `vpn.py` | VPN location manager (bring-your-own WireGuard) |
| `utilities.py` | multitool helpers: disk usage, open ports, password strength, health |
| `cleanup.py` | system cleanup: temp/cache reclaim, startup items |
| `nettools.py` | network toolkit: port scan, LAN devices, Wi-Fi info |
| `mediatools.py` | file info + media conversion (ffmpeg) |
| `privacy.py` | keychain secrets, file encryption, breached-password check |
| `ember_browser.py` | Ember Browser: secure, AI-first web browser + Ember Search (Qt WebEngine) |
| `ai_detect.py` | AI-content detector for text + images (heuristics + provenance) |
| `quick_tools.py` | password generator, QR maker, image metadata stripper |
| `power_tools.py` | read documents (PDF/docx/xlsx), secret scan, secure-delete, unit convert |
| `local_ai.py` | local AI via Ollama (offline, no API key / rate limit) |
| `macros.py` | save/replay named task workflows |
| `creative.py` | image generation, vision Q&A, audio transcription (Gemini) |
| `security_extras.py` | aggregate security checkup + score |
| `extension/` | Ember Chrome extension (summarize / ask / AI-check, uses your Gemini key) |
| `voice.py` | speech input + text-to-speech for Voice Chat (Edge neural / system / Gemini) |
| `audio_level.py` | live mic level + silence-detected (auto) voice turns for the glow/orb |
| `live_voice.py` | natural real-time voice via the Gemini Live API (AI turn-taking) |
| `wake_word.py` | always-on "Hey Ember" wake-word listener |
| `push_to_talk.py` | hold-a-key-to-talk coordinator (zero-latency, no wake word) |
| `stt.py` | speech-to-text: local Whisper → Gemini → Google, auto-selected |
| `terminal.py` | in-app terminal + persistent Python REPL (shell & code runner) |
| `agent_tasks.py` | parallel agent-task manager (run/track multiple jobs at once) |
| `mac_keys.py` | atomic macOS key-combo sender (fixes cmd+w split-press) |
| `gmail_tools.py` | organise Gmail over IMAP — search/label/archive/star/trash + read |
| `timers.py` | countdown timers (notification + sound + chat alert) |
| `offline.py` | Offline Mode — gate network tools, run fully local |
| `ollama_agent.py` | local Ollama brain that drives the computer (tools + vision), offline |
| `ollama_tools.py` | curated offline toolset the local model can call |
| `setup_tour.py` | first-run newcomer tour (level, brain install, Gmail, capabilities) |
| `network_adblock.py` | system-wide ad/tracker blocker (hosts file) with its own menu |
| `browser_extensions.py` | AI-built Ember Browser extensions |
| `browser_passwords.py` | saved logins for Ember Browser |
| `workflow_recorder.py` | record & replay real mouse/keyboard workflows |
| `hotkey_daemon.py` / `ember_hotkey_listener.py` | quit-proof global summon hotkey helper |
| `icons.py` | original built-in icon set (replaces emoji) |
| `siri_glow.py` | audio-reactive Siri-style glow + floating orb |
| `productivity_tools.py` | screen recorder, text snippets/expander |
| `key_vault.py` | encrypted local vault for API keys |
| `usage.py` | per-model call/token usage vs free-tier limits |
| `custom_tools.py` | AI-authored custom tools (recipes) |
| `plugin_system.py` | drop-in user plugins (`plugins/*.py`) |
| `models.py` | model catalog, Auto selection, rate-limit info |
| `memory.py` | long-term facts/preferences ("learns about you") |
| `safety.py` | risk classification + confirmation for every tool |
| `claude_agent.py` / `claude_bridge.py` | Claude backend + Gemini↔Claude handoff |
| `scheduled_tasks.py` | schedule shell commands for later (launchd / Task Scheduler) |
| `updater.py` / `version.py` | in-app self-update + version/release config |
| `make_logo.py` | regenerate the app icon |
