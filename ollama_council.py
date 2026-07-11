"""Ember Council — make several local (Ollama) models collaborate like a team to answer better
than any one small model alone. This is the "mixture-of-agents" pattern (a proven way to beat a
single model): every model drafts an answer, then reads the others' drafts and improves its own,
then one model synthesizes them into a single combined answer.

Everything is offline (local Ollama) and needs no API key. The orchestration is a PURE function
that takes an injected `complete(model, prompt) -> str`, so the whole collaboration flow is
unit-tested with fakes; the real Ollama calls are lazy (default_completer).
"""
from __future__ import annotations

from typing import Callable, Optional


def build_proposer_prompt(question: str, history: str = "") -> str:
    ctx = f"Conversation so far:\n{history}\n\n" if history else ""
    return (f"{ctx}Answer this as helpfully and correctly as you can. Think it through, then give "
            f"a clear, complete answer.\n\nQuestion: {question}")


def build_refine_prompt(question: str, my_draft: str, peers: list) -> str:
    """peers: list of (model_name, draft_text). The model sees other answers and improves its own."""
    others = "\n\n".join(f"— Draft from {name}:\n{text}" for name, text in peers if text)
    return (
        "You are collaborating with other AI models to answer a question. Below is your own draft "
        "and the other models' drafts. Take the best ideas from all of them, fix any mistakes you "
        "notice, and write an improved, self-contained answer. Don't mention the other drafts or "
        "that you collaborated — just give the best answer.\n\n"
        f"Question: {question}\n\nYour draft:\n{my_draft}\n\n"
        f"Other models' drafts:\n{others}\n\nYour improved answer:"
    )


def build_aggregator_prompt(question: str, drafts: list) -> str:
    """drafts: list of (model_name, text). One model fuses everything into the final answer."""
    joined = "\n\n".join(f"— Answer {i + 1} (from {name}):\n{text}"
                         for i, (name, text) in enumerate(drafts) if text)
    return (
        "Several AI models each answered the question below. Synthesize their answers into ONE "
        "final response that is more accurate and complete than any single one: keep what they "
        "agree on, resolve disagreements using your best judgment, drop anything wrong, and "
        "present it clearly. Write only the final answer, as if it were your own.\n\n"
        f"Question: {question}\n\nThe answers:\n{joined}\n\nFinal combined answer:"
    )


def run_council(question: str, models: list, complete: Callable[[str, str], str], *,
                rounds: int = 1, aggregator: Optional[str] = None,
                history: str = "", on_event: Optional[Callable] = None,
                stop: Optional[Callable[[], bool]] = None) -> dict:
    """Run the collaboration. `complete(model, prompt) -> str` is injected (lazy Ollama in prod).

    Flow: propose (each model drafts) → refine × `rounds` (each model improves after reading the
    others) → synthesize (aggregator fuses all into one). Returns
    {ok, final, contributions:[{model,round,text}], aggregator}.
    on_event(phase, model) is called for UI progress ("propose"/"refine"/"synthesize").
    """
    on = on_event or (lambda *a, **k: None)
    stopped = stop or (lambda: False)
    models = [m for m in (models or []) if m]
    if not models:
        return {"ok": False, "error": "no models selected for the council"}

    contributions = []
    drafts = {}
    for m in models:
        if stopped():
            return {"ok": False, "error": "stopped"}
        on("propose", m)
        text = (complete(m, build_proposer_prompt(question, history)) or "").strip()
        drafts[m] = text
        contributions.append({"model": m, "round": 0, "text": text})

    # A single model is just itself — no point refining/aggregating against no peers.
    if len(models) == 1:
        return {"ok": True, "final": drafts[models[0]], "contributions": contributions,
                "aggregator": models[0]}

    for r in range(1, max(0, rounds) + 1):
        updated = {}
        for m in models:
            if stopped():
                return {"ok": False, "error": "stopped"}
            on("refine", m)
            peers = [(mm, drafts[mm]) for mm in models if mm != m]
            text = (complete(m, build_refine_prompt(question, drafts[m], peers)) or "").strip()
            updated[m] = text or drafts[m]
            contributions.append({"model": m, "round": r, "text": updated[m]})
        drafts = updated

    agg = aggregator if aggregator in models else models[0]
    on("synthesize", agg)
    if stopped():
        return {"ok": False, "error": "stopped"}
    final = (complete(agg, build_aggregator_prompt(question, [(m, drafts[m]) for m in models])) or "").strip()
    if not final:  # aggregator produced nothing — fall back to the longest refined draft
        final = max(drafts.values(), key=lambda t: len(t or ""), default="")
    return {"ok": True, "final": final, "contributions": contributions, "aggregator": agg}


# --- lazy Ollama plumbing --------------------------------------------------------------

def default_completer(base_url: str = "http://localhost:11434", timeout: float = 120.0):
    """A `complete(model, prompt)` backed by local Ollama. Imports requests lazily."""
    def _complete(model: str, prompt: str) -> str:
        try:
            import ollama_agent
            return ollama_agent.quick_complete(prompt, model=model, base_url=base_url, timeout=timeout)
        except Exception:
            return ""
    return _complete


def available_models(base_url: str = "http://localhost:11434") -> list:
    """List installed Ollama model names (['llama3.1', 'qwen2.5', …]); [] if Ollama isn't running."""
    try:
        import requests
        r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        if r.status_code != 200:
            return []
        return [m.get("name", "").split(":")[0] or m.get("name", "")
                for m in (r.json().get("models") or []) if m.get("name")]
    except Exception:
        return []
