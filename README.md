# Ember — autonomous AI agent for your computer

Ember sees your screen, drives the mouse/keyboard, controls a real browser, runs shell
commands, manages files, and can be controlled from your phone. macOS + Windows.

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
You need **Python 3.10+** installed first:
- macOS: `brew install python@3.12`  (or grab it from python.org)
- Windows: install from [python.org](https://www.python.org/downloads/) and check **“Add Python to PATH.”**

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

Ember scans what it downloads and what you ask it to open, isolates anything it
can't vouch for, and quarantines confirmed threats.

- **Scan on download / before open** — every downloaded file (and any file Ember
  is about to open) is scanned with local heuristics (executable-disguised-as-a-
  document, double extensions like `invoice.pdf.exe`, macro-laden Office files,
  known-bad hashes), the platform antivirus (**Windows Defender** / **ClamAV** if
  installed), and **VirusTotal** (hash lookup, plus uploading unknown files when a
  key is set). Suspicious or malicious files are **not opened until the scan finishes**.
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
| `screen_vision.py` | exact clicking + on-screen OCR |
| `remote_server.py` | Ember Link phone control |
| `antivirus.py` | malware scan, quarantine vault & sandbox |
| `web_policy.py` | website blocking + URL reputation |
| `redaction.py` | strip secrets/PII from logs, audit & screenshots |
| `audit.py` | tamper-evident action audit log |
| `voice.py` | speech input + text-to-speech for Voice Chat |
| `make_logo.py` | regenerate the app icon |
