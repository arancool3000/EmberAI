# Ember Chrome extension

A self-contained companion: summarize any page, ask questions about it, and check
whether text looks AI-generated — using **your own** free Gemini API key (stored only
in your browser). It does not require the Ember desktop app to be running.

## Install (free, ~30 seconds)

1. Open `chrome://extensions` in Chrome (or Edge/Brave).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `extension/` folder.
4. Click the Ember icon → **Set Gemini API key ⚙**, paste a key from
   <https://aistudio.google.com/apikey>, Save.

## Use

- **Summarize** — bullet-point summary of the current page.
- **AI-check** — heuristic estimate of whether the selected text (or the page) is
  AI-generated. Runs offline, instantly. It's a signal, not proof.
- **Ask** — type a question about the page and press Enter.

## Notes

- The AI-check uses the same heuristic as the desktop app's `ai_detect` (sentence
  burstiness, AI "tell" phrases, contraction rate).
- Your key only goes to Google's Gemini endpoint, directly from your browser.
- To publish on the Chrome Web Store later, zip this folder and submit it (one-time
  $5 developer fee — optional; loading unpacked is free forever).
