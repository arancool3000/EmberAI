# Ember — autonomous AI agent for your computer

Ember sees your screen, drives the mouse/keyboard, controls a real browser, runs shell
commands, manages files, and can be controlled from your phone. macOS + Windows.

---

## ▶️ Run it (easiest)

**macOS:** double-click **`Ember.command`**
**Windows:** double-click **`Ember.bat`**

> **macOS first-launch note:** Ember is free and unsigned, so on the very first
> double-click macOS may say *"Apple cannot check it for malicious software."*
> That's expected for any app not paid-notarized through Apple. Just **right-click
> the file → Open → Open** once. The script then clears the quarantine flag from
> the whole folder, so the other `.command` files open normally afterward.
> Prefer one command? Run this in Terminal on the folder to clear them all at once:
> ```bash
> xattr -dr com.apple.quarantine /path/to/the/Ember/folder
> ```

The first launch installs everything automatically (a few minutes); after that it just opens.
You need **Python 3.10+** installed first:
- macOS: `brew install python@3.12`  (or grab it from python.org)
- Windows: install from [python.org](https://www.python.org/downloads/) and check **“Add Python to PATH.”**

On first run, paste a free **Gemini API key** (get one at https://aistudio.google.com/apikey)
into Settings (⚙). For Claude models, add an Anthropic API key too.

### Run from a terminal instead
```bash
cd EmberMac
./install.sh        # one-time (macOS).  Windows: pip install -r requirements.txt
./run.sh            # or:  python3 main.py
```

---

## 📦 Make it a real standalone app (free)

Turn Ember into a double-clickable app that needs **no Python and no terminal** to run:

- **macOS:** double-click **`BUILD_DESKTOP_APP.command`** → produces **`dist/Ember.app`**.
  Drag it to Applications. (Uses PyInstaller — completely free.)
- **Windows:** run `python -m PyInstaller --noconfirm Ember.spec` → produces `dist/Ember/Ember.exe`.

The build is unsigned, so the first launch needs **right-click → Open** (macOS) or
“More info → Run anyway” (Windows). That’s normal for free, self-built apps.

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
| `BUILD_DESKTOP_APP.command` | build standalone `Ember.app` |
| `main.py` | entry point |
| `ui.py` | the desktop UI |
| `agent.py` | the AI agent loop + tool declarations |
| `tools.py`, `more_tools.py`, `extra_tools.py` | the tool set |
| `screen_vision.py` | exact clicking + on-screen OCR |
| `remote_server.py` | Ember Link phone control |
| `voice.py` | speech input + text-to-speech for Voice Chat |
| `make_logo.py` | regenerate the app icon |
