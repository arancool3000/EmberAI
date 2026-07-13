"""Octopus Mode — every AI you've connected reaches into the same question at once, like the
eight arms of an octopus, and then a central "head" pulls the threads together into one answer.

Where the local-only Ember Council (``ollama_council``) teams up small *Ollama* models, Octopus
Mode spans EVERY provider you've set up — Gemini, Claude, OpenAI (and OpenAI-compatible), and a
local Ollama model — so a hard question gets genuinely different minds on it instead of clones of
one. Each connected model is a *tentacle*:

  1. **Reach**  — every tentacle drafts its own answer, independently and in its own voice.
  2. **Discuss** — each tentacle reads what the others found and improves its answer (the arms
     touching). Repeat for `rounds` rounds.
  3. **Head**   — the strongest connected model synthesizes all the tentacles into ONE answer.

The orchestration (`run_octopus`) is a PURE function over an injected
``complete(tentacle_key, prompt) -> str``, so the whole flow is unit-tested with fakes and never
makes a network call. `default_completer` wires the real providers lazily; `discover_tentacles`
reads the saved settings to see which AIs are actually available (a key present / a local model),
so Octopus never pretends to use a model the user hasn't connected. A dead tentacle (bad key,
model offline) simply doesn't contribute — the octopus still answers with the arms it has.
"""
from __future__ import annotations

from typing import Callable, Optional

import models

# An octopus has eight arms; that's also plenty of diverse drafts before returns diminish.
TENTACLE_MAX = 8

# Which model should act as the HEAD (synthesizer). Prefer the strongest connected vendor, since
# the head has the hardest job — fusing several answers without introducing new mistakes.
_HEAD_RANK = {"claude": 0, "openai": 1, "gemini": 2, "ollama": 3}

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


# ---------------------------------------------------------------------------
# Prompts (pure — no I/O)
# ---------------------------------------------------------------------------

def build_reach_prompt(question: str, history: str = "") -> str:
    ctx = f"Conversation so far:\n{history}\n\n" if history else ""
    return (f"{ctx}You are one mind among several answering the same question in parallel. Give "
            f"your best, complete, INDEPENDENT answer — your own angle, your own reasoning, and any "
            f"specifics you're confident about. Don't hedge about being one of several.\n\n"
            f"Question: {question}")


def build_discuss_prompt(question: str, my_draft: str, peers: list) -> str:
    """peers: list of (tentacle_label, draft_text). The tentacle reacts to the others and improves."""
    others = "\n\n".join(f"— Another AI answered:\n{text}" for _label, text in peers if text)
    return (
        "You are collaborating with other AIs on the question below — like arms of one octopus "
        "comparing what each found. Here is your answer and theirs. Take their best points, correct "
        "anything you now realise you got wrong, resolve disagreements with your best judgment, and "
        "write a single improved, self-contained answer. Don't mention the other answers or that you "
        "collaborated — just give the best answer.\n\n"
        f"Question: {question}\n\nYour answer:\n{my_draft}\n\n"
        f"The other AIs' answers:\n{others}\n\nYour improved answer:"
    )


def build_head_prompt(question: str, drafts: list) -> str:
    """drafts: list of (tentacle_label, text). The head fuses everything into the final answer."""
    joined = "\n\n".join(f"— Tentacle {i + 1} ({label}):\n{text}"
                         for i, (label, text) in enumerate(drafts) if text)
    return (
        "You are the HEAD of an octopus: several tentacles — each a different AI — answered the "
        "question below. Synthesize them into ONE final answer that is more accurate and complete "
        "than any single one: keep what they agree on, resolve conflicts using your best judgment, "
        "drop anything wrong, and present it clearly. Write only the final answer, as if it were "
        "your own.\n\n"
        f"Question: {question}\n\nThe tentacles' answers:\n{joined}\n\nFinal synthesized answer:"
    )


# ---------------------------------------------------------------------------
# Orchestration (pure — inject `complete`)
# ---------------------------------------------------------------------------

def _pick_head(live: list, head: Optional[str]) -> dict:
    if head:
        for t in live:
            if t["key"] == head:
                return t
    return sorted(live, key=lambda t: _HEAD_RANK.get(t["provider"], 9))[0]


def run_octopus(question: str, tentacles: list, complete: Callable[[str, str], str], *,
                rounds: int = 1, head: Optional[str] = None, history: str = "",
                on_event: Optional[Callable] = None,
                stop: Optional[Callable[[], bool]] = None) -> dict:
    """Run Octopus Mode. `complete(tentacle_key, prompt) -> str` is injected (real providers in prod).

    `tentacles` is a list of dicts: {key, provider, model, label}. Returns
    {ok, question, rounds, head:{key,label,answer}, tentacles:[{key,label,provider,reach,final}],
     answer, formatted}. `on_event(phase, label)` fires for UI progress ("reach"/"discuss"/"head").
    """
    on = on_event or (lambda *a, **k: None)
    stopped = stop or (lambda: False)
    tentacles = [t for t in (tentacles or []) if t and t.get("key")]
    if not tentacles:
        return {"ok": False, "error": "No AIs are available for Octopus Mode."}

    # 1) Reach — every tentacle drafts independently.
    reach: dict[str, str] = {}
    for t in tentacles:
        if stopped():
            return {"ok": False, "error": "stopped"}
        on("reach", t["label"])
        reach[t["key"]] = (complete(t["key"], build_reach_prompt(question, history)) or "").strip()

    live = [t for t in tentacles if reach.get(t["key"])]
    if not live:
        return {"ok": False, "error": "Every tentacle came back empty — check your API keys / that "
                                      "your local model is running, then try again."}
    drafts = {t["key"]: reach[t["key"]] for t in live}

    # 2) Discuss — each live tentacle reacts to the others and improves (skipped for a lone arm).
    if len(live) > 1:
        for r in range(1, max(0, rounds) + 1):
            updated = {}
            for t in live:
                if stopped():
                    return {"ok": False, "error": "stopped"}
                on("discuss", t["label"])
                peers = [(o["label"], drafts[o["key"]]) for o in live if o["key"] != t["key"]]
                improved = (complete(t["key"], build_discuss_prompt(question, drafts[t["key"]], peers))
                            or "").strip()
                updated[t["key"]] = improved or drafts[t["key"]]
            drafts = updated

    # 3) Head — the strongest connected model synthesizes all tentacles into one answer.
    head_t = _pick_head(live, head)
    if stopped():
        return {"ok": False, "error": "stopped"}
    on("head", head_t["label"])
    final = ""
    if len(live) > 1:
        final = (complete(head_t["key"],
                          build_head_prompt(question, [(t["label"], drafts[t["key"]]) for t in live]))
                 or "").strip()
    if not final:  # lone arm, or the head produced nothing — fall back to the fullest draft.
        final = drafts.get(head_t["key"]) or max(drafts.values(), key=lambda x: len(x or ""), default="")

    result = {
        "ok": True, "question": question, "rounds": rounds,
        "head": {"key": head_t["key"], "label": head_t["label"], "answer": final},
        "tentacles": [{"key": t["key"], "label": t["label"], "provider": t["provider"],
                       "reach": reach[t["key"]], "final": drafts[t["key"]]} for t in live],
    }
    result["answer"] = final
    result["formatted"] = format_result(result)
    return result


def _short(text: str, n: int = 240) -> str:
    """Trim a tentacle's take for the overview — on a sentence/word boundary, never mid-word."""
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    cut = t[:n]
    for sep in (". ", "! ", "? "):
        i = cut.rfind(sep)
        if i >= n // 2:
            return cut[:i + 1].strip()
    i = cut.rfind(" ")
    return (cut[:i] if i >= n // 2 else cut).strip() + "…"


def format_result(result: dict) -> str:
    """Render the result as octopus tentacles: a central head, then each arm's take."""
    tents = result.get("tentacles") or []
    head = result.get("head") or {}
    n = len(tents)
    lines = [
        f"🐙 **Octopus Mode** — {n} tentacle{'' if n == 1 else 's'} reached into this question",
        "",
        f"🧠 **Head — {head.get('label', '')} pulled the threads together:**",
        "",
        (head.get("answer", "") or "").strip(),
        "",
        "**The tentacles:**",
    ]
    for i, t in enumerate(tents):
        mark = _CIRCLED[i] if i < len(_CIRCLED) else f"({i + 1})"
        take = _short(t.get("final") or t.get("reach") or "")
        lines.append(f"🐙{mark}  **{t['label']}** — {take}")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Which AIs are available? (pure over `settings`; injectable ollama list)
# ---------------------------------------------------------------------------

def _first_key(s: dict, *names: str) -> str:
    for n in names:
        v = (s.get(n) or "").strip()
        if v:
            return v
    return ""


def _label_for(provider: str, model: str) -> str:
    if provider == "gemini":
        for mid, name, *_ in models.GEMINI_MODELS:
            if mid == model:
                return name
        return model
    if provider == "claude":
        for mid, name, _notes in models.CLAUDE_MODELS:
            if mid == model:
                return name
        return model
    if provider == "openai":
        for mid, name, _notes in models.OPENAI_MODELS:
            if mid == model:
                return name
        return model
    if provider == "ollama":
        return f"Ollama · {model}"
    return model


def _openai_default(s: dict) -> str:
    """Use the user's chosen OpenAI model if they have one selected; else a cheap, capable default."""
    m = s.get("model_id") or s.get("gemini_model") or ""
    if models.provider_for(m) == "openai":
        return m
    return "gpt-4o-mini"


def discover_tentacles(settings: dict, *, ollama_models: Optional[list] = None,
                       max_arms: int = TENTACLE_MAX) -> list:
    """Return the tentacles Octopus can actually use, from the saved settings.

    One tentacle per connected PAID provider (Claude, OpenAI) — never multiplied, to keep cost
    predictable — plus the free Gemini/Gemma minds and any local Ollama models to fill the arms.
    Returns a list of {key, provider, model, label}; empty if nothing is connected.
    """
    s = settings or {}
    gem = _first_key(s, "gemini_api_key", "gemini_api_key_secondary",
                     "gemini_api_key_3", "gemini_api_key_4")
    claude_key = _first_key(s, "anthropic_api_key")
    oai = _first_key(s, "openai_api_key")
    if ollama_models is None:
        om = (s.get("ollama_model") or "").strip()
        ollama_models = [om] if om else []
    ollama_models = [m for m in ollama_models if m]

    out: list = []
    seen: set = set()

    def add(provider: str, model: str) -> None:
        if not model or len(out) >= max_arms:
            return
        key = f"{provider}:{model}"
        if key in seen:
            return
        seen.add(key)
        out.append({"key": key, "provider": provider, "model": model,
                    "label": _label_for(provider, model)})

    # Stage 1 — one tentacle per connected provider (maximally different minds).
    if claude_key:
        add("claude", s.get("anthropic_model") or "claude-opus-4-8")
    if oai:
        add("openai", _openai_default(s))
    if gem:
        add("gemini", "gemini-3.5-flash")
    if ollama_models:
        add("ollama", ollama_models[0])

    # Stage 2 — fill the remaining arms with FREE/LOCAL models only (never multiply paid calls):
    # more distinct free Gemini/Gemma minds, then any other local Ollama models.
    if gem:
        for mid, _name, _rpm, _rpd, _tpm, tier, _notes in models.GEMINI_MODELS:
            if tier == "free":
                add("gemini", mid)
    for lm in ollama_models[1:]:
        add("ollama", lm)

    return out[:max_arms]


# ---------------------------------------------------------------------------
# Real, cross-provider completer (lazy imports; a dead tentacle returns "")
# ---------------------------------------------------------------------------

def _gemini_complete(s: dict, model: str, prompt: str) -> str:
    from google import genai
    key = _first_key(s, "gemini_api_key", "gemini_api_key_secondary",
                     "gemini_api_key_3", "gemini_api_key_4")
    if not key:
        return ""
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(model=models.resolve(model), contents=prompt)
    return (getattr(resp, "text", "") or "").strip()


def _claude_complete(s: dict, model: str, prompt: str, max_tokens: int) -> str:
    import anthropic
    key = "".join((s.get("anthropic_api_key") or "").split())
    if not key:
        return ""
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(model=model, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": prompt}])
    parts = [getattr(b, "text", "") for b in (getattr(resp, "content", None) or [])]
    return "".join(p for p in parts if p).strip()


def _openai_complete(s: dict, model: str, prompt: str, max_tokens: int) -> str:
    import openai
    key = "".join((s.get("openai_api_key") or "").split())
    if not key:
        return ""
    kwargs = {"api_key": key}
    base = (s.get("openai_base_url") or "").strip()
    if base:
        kwargs["base_url"] = base
    client = openai.OpenAI(**kwargs)
    m = model.split(":", 1)[1] if model.startswith("openai:") else model
    resp = client.chat.completions.create(
        model=m, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)
    return (resp.choices[0].message.content or "").strip()


def default_completer(settings: dict, *, max_tokens: int = 1400) -> Callable[[str, str], str]:
    """A real `complete(tentacle_key, prompt)` that routes to the right provider by the key prefix
    ('gemini:'/'claude:'/'openai:'/'ollama:'). Any failure returns "" so one dead arm can't sink
    the whole octopus."""
    s = settings or {}

    def complete(key: str, prompt: str) -> str:
        provider, _, model = key.partition(":")
        try:
            if provider == "gemini":
                return _gemini_complete(s, model, prompt)
            if provider == "claude":
                return _claude_complete(s, model, prompt, max_tokens)
            if provider == "openai":
                return _openai_complete(s, model, prompt, max_tokens)
            if provider == "ollama":
                import ollama_agent
                return (ollama_agent.quick_complete(prompt, model=model) or "").strip()
        except Exception:
            return ""
        return ""

    return complete


def _live_ollama_models(s: dict) -> list:
    """Installed local Ollama model names (best-effort); falls back to the configured one."""
    try:
        import ollama_council
        base = s.get("ollama_base_url") or "http://localhost:11434"
        got = ollama_council.available_models(base)
        if got:
            return got
    except Exception:
        pass
    om = (s.get("ollama_model") or "").strip()
    return [om] if om else []


# ---------------------------------------------------------------------------
# Agent tools
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    try:
        import ui
        return ui.load_settings() or {}
    except Exception:
        return {}


def octopus_status() -> dict:
    """List which AIs are available as Octopus Mode tentacles right now."""
    s = _load_settings()
    tentacles = discover_tentacles(s, ollama_models=_live_ollama_models(s))
    if not tentacles:
        return {"ok": True, "count": 0, "tentacles": [],
                "note": "No AIs are connected yet. Add a Gemini, Claude or OpenAI API key — or set "
                        "a local Ollama model — in Settings, then Octopus Mode lights up."}
    return {"ok": True, "count": len(tentacles),
            "tentacles": [{"provider": t["provider"], "label": t["label"]} for t in tentacles],
            "note": (f"{len(tentacles)} tentacles ready. "
                     "Octopus Mode will have them draft, discuss, then synthesize one answer.")}


def octopus_discuss(question: str, rounds: int = 1) -> dict:
    """Run Octopus Mode: every connected AI drafts, they discuss, then the head synthesizes ONE
    answer — returned with the octopus-tentacle structure in `formatted`."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "Give Octopus Mode a question to work on."}
    try:
        rounds = int(rounds)
    except Exception:
        rounds = 1
    rounds = max(0, min(3, rounds))

    s = _load_settings()
    tentacles = discover_tentacles(s, ollama_models=_live_ollama_models(s))
    if not tentacles:
        return {"ok": False, "error": "No AIs are connected yet. Add a Gemini, Claude or OpenAI API "
                                      "key — or set a local Ollama model — in Settings, then try "
                                      "Octopus Mode again."}
    result = run_octopus(question, tentacles, default_completer(s), rounds=rounds)
    if result.get("ok") and len(tentacles) == 1:
        result["note"] = ("Only one AI is connected, so this is a single tentacle — connect another "
                          "provider (Claude / OpenAI / a local Ollama model) for a real octopus.")
    return result


TOOL_DECLARATIONS = [
    {"name": "octopus_discuss",
     "description": "Octopus Mode: have EVERY connected AI (Gemini, Claude, OpenAI, local Ollama) "
                    "independently answer the question, then read each other's answers and discuss, "
                    "then one 'head' model synthesizes a single combined answer — structured like "
                    "octopus tentacles converging on a head (see the 'formatted' field). Use this "
                    "whenever the user asks for 'octopus mode', a panel/council of AIs, multiple "
                    "models, or the most robust answer. `rounds` (0-3) is how many discussion rounds.",
     "parameters": {"type": "OBJECT", "properties": {
         "question": {"type": "STRING"},
         "rounds": {"type": "NUMBER"}}, "required": ["question"]}},
    {"name": "octopus_status",
     "description": "List which AIs are currently available as Octopus Mode tentacles (based on the "
                    "API keys and local Ollama model the user has connected).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "octopus_discuss": octopus_discuss,
    "octopus_status": octopus_status,
}

# Both tools only send the question to the user's OWN configured providers and read back text —
# they never touch the machine — so they're safe/read-only.
READONLY_TOOLS = {"octopus_discuss", "octopus_status"}
