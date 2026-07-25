# Ember for Android

A native Android build of Ember — the Ember AI agent on your phone. This is a
real Android app (not the Phone-Link remote and not a web wrapper): a native,
framework-only Java app that talks directly to your chosen AI model.

## What's inside

- **Chat with any model** — Google **Gemini**, Anthropic **Claude**, or **OpenAI**
  and any OpenAI-compatible endpoint (Grok, DeepSeek, Groq, OpenRouter, a local
  server, …). Streaming replies, just like the desktop app.
- **Voice** — tap the mic to talk (on-device speech recognition) and have Ember
  read replies aloud (text-to-speech).
- **Web search grounding** — optional Google Search grounding on Gemini.
- **Memory** — tell Ember what to remember about you; it's injected into every chat.
- **Word filter** — a new-chat dialog lets you keep the default filter, **add your
  own blocked words**, or **turn filtering off entirely** (gated behind an 18+
  confirmation). Nothing is sent anywhere; it only changes how text is shown.
- **Ember Arcade** — five built-in games: Snake, 2048, Breakout, Memory Match, and
  Tic-Tac-Toe (against an unbeatable minimax AI).
- **Private by design** — your API keys and chats live only in the app's private
  storage on the device. Ember has no servers.

## Install the APK

Download `ember-debug.apk` (from a CI run's **Artifacts**, or built locally as
below), copy it to your Android phone, and open it. You'll need to allow
"install unknown apps" for your file manager/browser the first time. Requires
Android 7.0 (API 24) or newer.

Then open **Settings** (menu ▸ Settings), paste an API key for your provider, and
start chatting. A free Gemini key works well: https://aistudio.google.com/apikey

## Build it yourself

The app is deliberately **framework-only** — it uses just the Android platform
(no AndroidX, no Compose, no third-party libraries), so it builds with the AOSP
command-line tools without Android Studio or a Google SDK download.

### Linux (Debian/Ubuntu)

```bash
sudo apt-get install -y aapt apksigner zipalign dalvik-exchange   # + a JDK (17)
cd android
bash build-apk.sh
# → build/ember-debug.apk  (signed with a debug key; installable on any device)
```

The build script fetches the framework `android.jar` (API 34) once on first run.
Set `ANDROID_JAR=/path/to/android.jar` to use a local copy.

### CI

`.github/workflows/android.yml` runs exactly this build on every push that
touches `android/` and uploads the APK as the `ember-android-apk` artifact.

## Project layout

```
android/
├── build-apk.sh                 # one-command AOSP-tools build → build/ember-debug.apk
└── app/src/main/
    ├── AndroidManifest.xml
    ├── java/com/ember/ai/
    │   ├── MainActivity.java     # chat UI, streaming, voice, new-chat word filter
    │   ├── SettingsActivity.java # provider + keys + models + memory
    │   ├── ai/AiClient.java      # Gemini / Claude / OpenAI streaming
    │   ├── WordFilter.java       # the word ban + unblock / custom words
    │   ├── Voice.java            # SpeechRecognizer + TextToSpeech
    │   ├── ChatStore.java        # on-device conversation history
    │   ├── ArcadeActivity.java   # game launcher/host
    │   └── {Snake,Game2048,Breakout,Memory,TicTacToe}View.java
    └── res/                      # layouts, drawables, ember theme, launcher icons
```

## Notes

- Signed with a **debug** key (v2 + v3 APK signatures). For a store release,
  re-sign `build/app.aligned.apk` with your own release keystore via `apksigner`.
- Model names are editable in Settings; defaults are sensible starting points and
  can be changed to whatever your provider currently offers.
