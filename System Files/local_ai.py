"""Local AI via Ollama — offline, no API key, no rate limits.

If Ollama (https://ollama.com) is running locally, Ember can offload text tasks to it
instead of the cloud — handy when you're rate-limited or offline. Everything degrades
gracefully when Ollama isn't installed/running.
"""
from __future__ import annotations

_BASE = "http://localhost:11434"


def local_ai_status() -> dict:
    """Is a local Ollama server running, and which models are available?"""
    try:
        import requests
        r = requests.get(f"{_BASE}/api/tags", timeout=4)
        if r.status_code == 200:
            models = [m.get("name") for m in (r.json().get("models") or []) if m.get("name")]
            return {"ok": True, "running": True, "models": models,
                    "note": "" if models else "Running, but no models pulled. Run: ollama pull llama3.2"}
        return {"ok": True, "running": False, "status": r.status_code}
    except Exception:
        return {"ok": True, "running": False,
                "note": "Ollama not running. Install from https://ollama.com, then `ollama pull llama3.2`."}


def local_ai_ask(prompt: str, model: str = "") -> dict:
    """Answer a prompt with a LOCAL model (no cloud, no rate limit). Needs Ollama running."""
    if not prompt:
        return {"ok": False, "error": "prompt required"}
    try:
        import requests
        model = (model or "").strip()
        if not model:
            st = local_ai_status()
            models = st.get("models") or []
            if not models:
                return {"ok": False, "error": "no local models. Install Ollama (ollama.com) and "
                        "run: ollama pull llama3.2"}
            model = models[0]
        r = requests.post(f"{_BASE}/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False}, timeout=180)
        if r.status_code != 200:
            return {"ok": False, "error": f"ollama returned {r.status_code}"}
        return {"ok": True, "model": model, "response": (r.json().get("response") or "").strip()}
    except Exception as e:
        return {"ok": False, "error": f"local AI unavailable: {e} (install Ollama from ollama.com)"}
