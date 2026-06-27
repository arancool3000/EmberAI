"""Catalog of supported AI models (Gemini + Claude), with free-tier rate limits where applicable."""
from __future__ import annotations

# rpm = requests/minute, rpd = requests/day, tpm = tokens/minute (free tier where applicable)
# tier: "free" (works on free Gemini tier), "paid" (Anthropic / paid Google tier)

GEMINI_MODELS = [
    # id, display name, rpm, rpd, tpm, tier, notes
    ("gemini-3.1-flash-lite",  "Gemini 3.1 Flash Lite",  15,  500,   7_070, "free", "BEST free for agents - 500 RPD"),
    ("gemini-3.5-flash",       "Gemini 3.5 Flash",        5,   20, 250_000, "free", "newest free flash, high TPM"),
    ("gemini-2.5-flash-lite",  "Gemini 2.5 Flash Lite",  10,   20, 250_000, "free", "high TPM, low RPD"),
    ("gemini-2.5-flash",       "Gemini 2.5 Flash",        5,   20,  10_120, "free", "older but stable"),
    ("gemma-3-27b-it",         "Gemma 3 27B",            15, 1500,       0, "free", "text-only - used for chat titles"),
    ("gemma-4-31b-it",         "Gemma 4 31B",            15, 1500,       0, "free", "1500 RPD - text-only, no tool use"),
    ("gemini-3.1-pro",         "Gemini 3.1 Pro",          0,    0,       0, "paid", "paid only - top reasoning"),
    ("gemini-2.5-pro",         "Gemini 2.5 Pro",          0,    0,       0, "paid", "paid only"),
]

CLAUDE_MODELS = [
    # id, display name, notes
    ("claude-opus-4-8",      "Claude Opus 4.8 (1M context)", "newest, strongest reasoning - paid"),
    ("claude-sonnet-4-6",    "Claude Sonnet 4.6",            "fast and very capable - paid"),
    ("claude-haiku-4-5",     "Claude Haiku 4.5",             "fastest Claude - paid"),
    ("claude-opus-4-7",      "Claude Opus 4.7 (1M context)", "prior flagship - paid"),
]


# "Auto" resolves to the best free model and leans on the rate-limit fail-over chain.
RECOMMENDED_FREE = "gemini-3.1-flash-lite"


# Model ids that have been retired / 404 — remap saved settings to a working equivalent.
_DEAD_MODELS = {"gemini-3.1-flash": "gemini-3.1-flash-lite"}


def resolve(model_id: str | None) -> str:
    """Map the 'auto' sentinel to a concrete model, retired ids to a live one; else pass through."""
    if not model_id or model_id == "auto":
        return RECOMMENDED_FREE
    return _DEAD_MODELS.get(model_id, model_id)


def all_choices() -> list[tuple[str, str, str, str]]:
    """Returns flat list of (provider, model_id, display_label, hint) for UI dropdowns."""
    out = [("gemini", "auto", "✨ Auto — best available",
            "picks the best free model and auto-fails-over on rate limits")]
    for mid, name, rpm, rpd, tpm, tier, notes in GEMINI_MODELS:
        if tier == "free":
            hint = f"{rpm} req/min, {rpd} req/day · free tier"
        else:
            hint = notes
        out.append(("gemini", mid, name, hint))
    for mid, name, notes in CLAUDE_MODELS:
        out.append(("claude", mid, name, f"{notes} · needs Anthropic API key"))
    # Local Ollama brain — offline, no key, no rate limits. One generic entry; the actual
    # local model is resolved at runtime (or set via the "Ollama model" field in Settings).
    out.append(("ollama", "ollama", "Local (Ollama)",
                "offline · no key · no rate limits — runs local tools too; pick a tool-capable "
                "model like qwen2.5 / llama3.1"))
    return out


def provider_for(model_id: str) -> str:
    if model_id == "ollama" or model_id.startswith("ollama:"):
        return "ollama"
    if model_id.startswith("claude"):
        return "claude"
    return "gemini"   # "auto" + all Gemini ids run on the Gemini provider


def supports_tool_use(model_id: str) -> bool:
    """Gemma + local Ollama don't drive Ember's tools. Pure Gemini and Claude do."""
    return not (model_id.startswith("gemma") or provider_for(model_id) == "ollama")


def supports_vision(model_id: str) -> bool:
    """Gemma + local Ollama are treated as text-only here. Gemini/Claude support images."""
    return not (model_id.startswith("gemma") or provider_for(model_id) == "ollama")


# Claude models that take adaptive thinking + the effort knob. Opus 4.6+ and Sonnet 4.6
# accept `thinking={"type": "adaptive"}` and `output_config={"effort": ...}`; Haiku 4.5
# (and older snapshots) reject `effort` with a 400, so we gate on an allow-list.
_CLAUDE_ADAPTIVE_THINKING = (
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
)
_CLAUDE_EFFORT = _CLAUDE_ADAPTIVE_THINKING  # same support set today


def supports_adaptive_thinking(model_id: str) -> bool:
    """True for Claude models that accept thinking={'type': 'adaptive'}."""
    return model_id in _CLAUDE_ADAPTIVE_THINKING


def supports_effort(model_id: str) -> bool:
    """True for Claude models that accept output_config={'effort': ...}."""
    return model_id in _CLAUDE_EFFORT


def rate_limit_summary() -> str:
    """Human-readable rate limit table for the settings dialog."""
    lines = ["Free-tier limits (Gemini AI Studio):", ""]
    for mid, name, rpm, rpd, tpm, tier, notes in GEMINI_MODELS:
        if tier != "free":
            continue
        lines.append(f"  {name:<26} {rpm:>3} RPM   {rpd:>4} RPD   {tpm:>7,} TPM")
    lines.append("")
    lines.append("Claude (Anthropic) is paid only - usage-based pricing.")
    lines.append("Ember falls back automatically if your primary model is overloaded.")
    return "\n".join(lines)
