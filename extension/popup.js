const out = document.getElementById("out");
const setOut = (t) => { out.textContent = t; };

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function pageText() {
  const tab = await activeTab();
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => document.body.innerText.slice(0, 16000),
  });
  return result || "";
}

async function selectionText() {
  const tab = await activeTab();
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => window.getSelection().toString(),
  });
  return result || "";
}

async function gemini(prompt) {
  const { emberKey, emberModel } = await chrome.storage.sync.get(["emberKey", "emberModel"]);
  if (!emberKey) return "Set your Gemini API key first (link below).";
  const model = emberModel || "gemini-3.1-flash-lite";
  try {
    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${emberKey}`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }) });
    const j = await res.json();
    if (j.error) return "API error: " + (j.error.message || "unknown");
    return j.candidates?.[0]?.content?.parts?.[0]?.text || "(no response)";
  } catch (e) {
    return "Request failed: " + e.message;
  }
}

const clamp = (x) => Math.max(0, Math.min(1, x));

// Same heuristic as Ember's ai_detect.detect_text — runs offline, instantly.
function detectAI(text) {
  const words = text.match(/[A-Za-z']+/g) || [];
  const n = words.length;
  if (n < 25) return { ok: false, msg: "Select or open more text (need ~25+ words)." };
  const sents = text.split(/[.!?]+/).filter((s) => s.trim());
  const lens = sents.map((s) => (s.match(/[A-Za-z']+/g) || []).length);
  const mean = lens.reduce((a, b) => a + b, 0) / (lens.length || 1);
  const std = Math.sqrt(lens.reduce((a, b) => a + (b - mean) ** 2, 0) / (lens.length || 1));
  const cv = mean ? std / mean : 0;
  const burst = clamp((0.65 - cv) / 0.65);
  const phrases = ["it's important to note", "in conclusion", "delve", "moreover", "furthermore",
    "tapestry", "leverage", "seamless", "realm of", "robust", "underscores", "navigating the",
    "in today's fast-paced world", "a testament to", "first and foremost"];
  const low = " " + text.toLowerCase() + " ";
  let hits = 0; phrases.forEach((p) => { hits += low.split(p).length - 1; });
  const phraseAI = clamp((hits / Math.max(1, n / 100)) / 3);
  const contr = (text.toLowerCase().match(/\b\w+'(?:t|s|re|ve|ll|d|m)\b/g) || []).length;
  const contrAI = clamp((1.3 - contr / Math.max(1, n / 100)) / 1.3);
  const score = Math.round((0.42 * burst + 0.34 * phraseAI + 0.24 * contrAI) * 100);
  const verdict = score >= 65 ? "likely AI-generated" : score <= 35 ? "likely human-written" : "uncertain / mixed";
  return { ok: true, score, verdict };
}

document.getElementById("sum").onclick = async () => {
  setOut("Summarizing…");
  setOut(await gemini("Summarize this page in a few clear bullet points:\n\n" + (await pageText())));
};

document.getElementById("detect").onclick = async () => {
  setOut("Checking…");
  let text = await selectionText();
  let scope = "selection";
  if ((text || "").trim().length < 40) { text = await pageText(); scope = "page"; }
  const r = detectAI(text || "");
  setOut(r.ok ? `AI-content check (${scope}):\n${r.verdict} — ${r.score}% AI-likelihood.\n\n(Heuristic signal, not proof.)` : r.msg);
};

document.getElementById("ask").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const q = e.target.value.trim();
  if (!q) return;
  setOut("Thinking…");
  setOut(await gemini(`Answer this about the page. Be concise.\n\nQUESTION: ${q}\n\nPAGE:\n${await pageText()}`));
});

document.getElementById("opts").onclick = () => chrome.runtime.openOptionsPage();
