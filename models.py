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
    # Gemma open models — text-only here (no tool-use/vision wired) but generous free limits,
    # perfect for cheap background jobs like chat-title generation. Smallest first.
    ("gemma-3-1b-it",          "Gemma 3 1B",             30, 14400,       0, "free", "tiny + fastest - great for chat titles"),
    ("gemma-3-4b-it",          "Gemma 3 4B",             30, 14400,       0, "free", "small + quick - text-only, no tool use"),
    ("gemma-3-12b-it",         "Gemma 3 12B",            30, 14400,       0, "free", "mid Gemma - text-only, no tool use"),
    ("gemma-3-27b-it",         "Gemma 3 27B",            30, 14400,       0, "free", "largest Gemma - text-only, no tool use"),
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

OPENAI_MODELS = [
    # id, display name, notes  — OpenAI (ChatGPT) models. All paid, need an OpenAI API key.
    ("gpt-5.1",              "GPT-5.1",                      "newest flagship - strongest OpenAI reasoning"),
    ("gpt-5",                "GPT-5",                        "flagship - great for agents"),
    ("gpt-5-mini",           "GPT-5 mini",                   "faster + cheaper GPT-5"),
    ("gpt-4.1",              "GPT-4.1",                      "capable, lower cost"),
    ("gpt-4o",               "GPT-4o",                       "fast multimodal"),
    ("gpt-4o-mini",          "GPT-4o mini",                  "cheapest OpenAI - good default"),
]

# "Other API-key providers" — anything speaking the OpenAI Chat Completions protocol works
# through the same OpenAIAgent by pointing base_url at it (id, label, base_url, env-var hint).
# The user just pastes that provider's key and types a model id; leave base_url blank for
# OpenAI itself. This is what turns "ChatGPT support" into "ChatGPT + most of the ecosystem".
OPENAI_COMPAT_BASES = [
    ("openai",     "OpenAI (ChatGPT)",  "",                                       "OPENAI_API_KEY"),
    ("xai",        "xAI (Grok)",        "https://api.x.ai/v1",                    "XAI_API_KEY"),
    ("deepseek",   "DeepSeek",          "https://api.deepseek.com/v1",            "DEEPSEEK_API_KEY"),
    ("groq",       "Groq",              "https://api.groq.com/openai/v1",         "GROQ_API_KEY"),
    ("mistral",    "Mistral",           "https://api.mistral.ai/v1",              "MISTRAL_API_KEY"),
    ("together",   "Together AI",       "https://api.together.xyz/v1",            "TOGETHER_API_KEY"),
    ("openrouter", "OpenRouter",        "https://openrouter.ai/api/v1",           "OPENROUTER_API_KEY"),
    ("perplexity", "Perplexity",        "https://api.perplexity.ai",              "PERPLEXITY_API_KEY"),
    ("local",      "Local (LM Studio / vLLM)", "http://localhost:1234/v1",        ""),
]


# "Auto" resolves to the best free model and leans on the rate-limit fail-over chain.
RECOMMENDED_FREE = "gemini-3.1-flash-lite"


# Cheap models used only for the tiny background "name this chat" job. "ollama" runs the
# title locally (offline, free); the gemma/gemini ids run on the Gemini free tier. The UI
# exposes these in the chat-title dropdown.
TITLE_MODELS = [
    ("ollama",                "Local (Ollama) — offline, free, no key"),
    ("gemma-3-1b-it",         "Gemma 3 1B — fastest, free"),
    ("gemma-3-4b-it",         "Gemma 3 4B — free"),
    ("gemma-3-12b-it",        "Gemma 3 12B — free"),
    ("gemma-3-27b-it",        "Gemma 3 27B — free"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite — free"),
]
DEFAULT_TITLE_MODEL = "gemma-3-4b-it"


# Model ids that have been retired / 404 — remap saved settings to a working equivalent.
_DEAD_MODELS = {
    "gemini-3.1-flash": "gemini-3.1-flash-lite",
    "gemma-4-31b-it": "gemma-3-27b-it",   # was a placeholder id; map to a real Gemma
}


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
    for mid, name, notes in OPENAI_MODELS:
        out.append(("openai", mid, name, f"{notes} · needs OpenAI API key"))
    # Local Ollama brain — offline, no key, no rate limits. One generic entry; the actual
    # local model is resolved at runtime (or set via the "Ollama model" field in Settings).
    out.append(("ollama", "ollama", "Local (Ollama)",
                "offline · no key · no rate limits — runs local tools too; pick a tool-capable "
                "model like qwen2.5 / llama3.1"))
    return out


_OPENAI_MODEL_IDS = {mid for mid, _n, _notes in OPENAI_MODELS}


def provider_for(model_id: str) -> str:
    if model_id == "ollama" or model_id.startswith("ollama:"):
        return "ollama"
    if model_id.startswith("claude"):
        return "claude"
    # OpenAI + OpenAI-compatible ids: the known GPT ids, the openai:/oai: prefix escape hatch
    # (openai:<any-model> for custom/compat endpoints), and the reasoning-model families.
    if (model_id in _OPENAI_MODEL_IDS or model_id.startswith("openai:")
            or model_id.startswith("gpt-") or model_id.startswith("o1")
            or model_id.startswith("o3") or model_id.startswith("o4")
            or model_id.startswith("chatgpt")):
        return "openai"
    return "gemini"   # "auto" + all Gemini ids run on the Gemini provider


def openai_base_for(provider_key: str) -> str:
    """Base URL for a named OpenAI-compatible provider ('' = OpenAI default). Unknown → ''."""
    for key, _label, base, _env in OPENAI_COMPAT_BASES:
        if key == provider_key:
            return base
    return ""


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
    lines.append("Claude (Anthropic) and OpenAI (ChatGPT) are paid only - usage-based pricing.")
    lines.append("OpenAI-compatible providers (Grok, DeepSeek, Groq, OpenRouter, local…) set their own limits.")
    lines.append("Ember falls back automatically if your primary model is overloaded.")
    return "\n".join(lines)
