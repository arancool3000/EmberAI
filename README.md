# Ember — autonomous AI agent for your computer

Ember sees your screen, drives the mouse/keyboard, controls a real browser, runs shell
commands, manages files, and can be controlled from your phone. macOS + Windows.
Free, MIT-licensed, and private — your API key stays on your machine; there are no Ember servers.

## ✨ What's inside

- **Autonomous agent** — 170+ tools: move the mouse/keyboard, read the screen with Vision OCR,
  drive a real browser via the DOM, run shell, manage files, and chain multi-step tasks.
  **Human-like mouse movement** (curved, eased paths — not robotic teleports).
- **Run modes & agents** — pick how Ember works: **auto** (autonomous), **plan** (proposes a
  plan and waits), **chat** (talk only), or **read-only**. Define **named agents** (a goal +
  run mode + tool scope + optional schedule, à la Base44 Superagents) and run them on demand or
  on a timer; Ember can also **spawn scoped sub-agents** for sub-tasks (like Claude's Task tool).
- **Ember Browser** — a secure, AI-first browser: tracker/ad blocking, an AI-answer search page,
  summarize/ask about any page, AI-content check, reader mode, per-site dark mode, bookmarks,
  history, downloads. Works with Gemini or Claude.
- **Security suite** — always-on antivirus (file scanning **+ real-time fileless /
  behavioral process protection**) + a real run-in-sandbox, malicious-site blocking,
  secret redaction, a tamper-evident audit log, read-only / capability modes, and bring-your-own VPN.
- **AI everywhere** — Gemini *or* Claude; an optional **local model via Ollama** (offline, no API
  key, no rate limit); image generation, vision Q&A, audio transcription, and AI text/image detection.
- **Phone Link** — control your computer from your phone's browser on the same Wi-Fi.
- **Voice, macros & scheduling** — hands-free voice chat, saved task macros, scheduled automations.
- **Chrome extension** — summarize / ask / AI-check any page from your browser.

---

## ▶️ Run it (easiest)

**macOS:** double-click **`Ember.command`**
**Windows:** double-click **`Ember.bat`**

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

## ✨ New in this build

- **Faster startup:** the desktop UI opens first while the AI engine warms in the background, and the
  macOS app bundle avoids slow one-file extraction on launch.
- **Codex-style workspace:** sidebar chat history, New Chat, longer message rendering, and recent
  conversation context sent automatically so follow-ups make sense.
- **Hands-free voice chat:** a continuous listen → act → speak loop, with a Voice settings tab for
  spoken replies, auto-send, silence retry, and voice turn length.
- **Command Center UI:** a wider desktop layout with one-click actions for Autopilot, app control,
  research, creation, screen reading, browser work, files, diagnostics, automations, phone link, and manual mode.
- **Cleaner chat input:** optional local autocorrect for ordinary prompts, without touching URLs,
  commands, paths, or code blocks.
- **Smarter web use:** Ember prefers DOM browser controls, re-checks pages after navigation, handles
  cookie banners, and pauses for the user on CAPTCHA/2FA instead of pretending to bypass them.
- **Better file finding:** `search_files` checks overlooked places such as Desktop, Downloads,
  iCloud Drive, shared folders, and Trash when a file looks missing.
- **Messier demo:** the Demo chip can create a deliberately cluttered folder for testing organize,
  dedupe, and cleanup workflows.

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
trackpad, keyboard, quality controls, and a Chat tab for telling Ember what to do remotely.
It’s LAN-only and PIN-gated; stop it when done.

---

## 🛡️ Built-in malware defense

Ember scans what it downloads and what you ask it to open, **watches running
processes in real time for fileless attacks**, isolates anything it can't vouch
for, and quarantines confirmed threats. Real-time protection is **always active**
by default.

- **Always-on Security Center** — a unified supervisor scans **every surface
  malware uses, continuously**, and keeps the individual monitors alive (a watchdog
  restarts anything that dies, so scanning never silently stops):
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
  lookup, plus uploading unknown files when a key is set). Suspicious or malicious
  files are **not opened until the scan finishes**.
- **Quarantine + auto-delete** — confirmed-malicious files are moved to a locked,
  non-executable vault and automatically deleted after 7 days. Nothing is deleted
  on a mere hunch — only a definitive detection quarantines a file, so a false
  positive can't destroy your data. Manage it with the `list_quarantine`,
  `restore_quarantined`, and `delete_quarantined` tools.
- **Sandbox unknown programs** — `run_in_sandbox` runs a file in the strongest
  isolation available (Docker with no network → macOS `sandbox-exec` / Windows
  restricted token → otherwise refuse) so its behaviour can be observed without
  risking your machine. If no isolation is available, Ember refuses to run it
  rather than running it unprotected.

**Optional, stronger protection** (all auto-detected — none required):
- **VirusTotal:** set a `VIRUSTOTAL_API_KEY` (free at virustotal.com) for cloud
  multi-engine scanning. Only file hashes are sent unless upload is enabled.
- **ClamAV** (`brew install clamav` / `apt install clamav`) for an on-device engine.
- **Docker** for the strongest sandbox.

Settings live in `~/Library/Application Support/Ember/security.json` (macOS) /
`%LOCALAPPDATA%\Ember\security.json` (Windows); the `security_status` tool reports
what protection is currently active.

---

## 💎 Plans — Free & Pro (everyone gets Pro right now)

Ember has two tiers, but **every user currently gets the full Pro feature set for
free** — no paywall, no license, no payment, no Apple Developer account needed.
`plan.py` keeps the structure so Pro *could* be sold later by flipping one default.

**Pro features (all unlocked today):**
- Advanced antivirus — `scan_directory` deep folder scans + quarantine
- Sandbox for running unknown programs safely
- **VPN** — connect through your own WireGuard locations (below)
- Live URL reputation, capability modes, tamper-evident audit log
- Multitool utilities — disk usage, open-port check, password strength, system health
- Priority models + the full Pro UI

`get_plan` shows what's unlocked; `set_plan free|pro` toggles locally (default **pro**).

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
| `tool_args.py` | coerces tool arguments to their declared types |
| `screen_vision.py` | exact clicking + on-screen OCR |
| `remote_server.py` | Ember Link phone control |
| `antivirus.py` | malware scan (entropy + IOC signatures), quarantine vault & sandbox |
| `security_center.py` | unified always-on active scanning (processes/files/network/persistence) + watchdog |
| `fileless_guard.py` | always-on fileless / behavioral process monitor |
| `download_guard.py` | real-time download folder scanner |
| `web_policy.py` | website blocking + URL reputation |
| `redaction.py` | strip secrets/PII from logs, audit & screenshots |
| `audit.py` | tamper-evident action audit log |
| `plan.py` | Free/Pro plans (everyone is Pro right now) |
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
| `voice.py` | speech input + text-to-speech for Voice Chat |
| `make_logo.py` | regenerate the app icon |
