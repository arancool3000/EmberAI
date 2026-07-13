"""Bridge to consult Claude when Gemini is stuck. Manual (copy/paste) by default."""
from __future__ import annotations

import json
from pathlib import Path

import pyperclip

BRIDGE_INSTRUCTIONS = """When Gemini needs deeper reasoning, it produces a detailed handoff prompt for Claude.
The user copies the prompt into Claude.ai (free), then pastes Claude's reply back into Ember.
Optionally, an Anthropic API key can be set to automate this round-trip.
"""


def build_handoff_prompt(situation: str, gemini_summary: str, attempted_actions: list[str],
                         screen_observations: str, specific_question: str) -> str:
    """Compose a rigorous prompt for Claude that includes everything it needs to reason cold."""
    return f"""You are an expert Windows debugging consultant being asked for help by another AI agent (Gemini)
that is controlling a Windows PC for the user. Gemini has hit a wall and needs your reasoning.
Reply with concrete, ordered, step-by-step instructions Gemini can execute via its tools.

# Gemini's available tools
- take_screenshot, click(x,y), type_text, press_key, scroll
- run_powershell(command), run_cmd(command)
- read_file, write_file, list_directory, open_url, open_app
- get_event_logs, get_system_info, get_installed_drivers, get_running_processes

# Situation as Gemini understands it
{situation}

# Gemini's current summary of the problem
{gemini_summary}

# What Gemini has already tried
{chr(10).join('- ' + a for a in attempted_actions) if attempted_actions else '(nothing yet)'}

# What Gemini sees on screen
{screen_observations}

# Gemini's specific question
{specific_question}

# Respond with
1. Your diagnosis (2-4 sentences).
2. A numbered action plan. Each step should be one tool call with exact arguments,
   e.g. "1. run_powershell: Get-WinEvent -LogName System -MaxEvents 5"
3. What to check after each step, and what to do if it fails.
Keep total response under 500 words. Be concrete, not abstract.
"""


def copy_to_clipboard(text: str) -> bool:
    try:
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def try_anthropic_api(prompt: str, api_key: str, model: str = "claude-opus-4-8") -> str | None:
    """If user has set an Anthropic API key, call Claude directly."""
    try:
        import requests
        import models
        # This consult exists for "deeper reasoning", so turn on adaptive thinking +
        # high effort where the model supports it (Opus 4.6+/Sonnet 4.6). Give thinking
        # headroom; older snapshots that reject these params fall back to a plain call.
        body = {
            "model": model,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        }
        if models.supports_adaptive_thinking(model):
            body["thinking"] = {"type": "adaptive"}
            body["max_tokens"] = 6000
        if models.supports_effort(model):
            body["output_config"] = {"effort": "high"}
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=90,
        )
        if r.status_code == 200:
            data = r.json()
            parts = data.get("content", [])
            return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return f"[Anthropic API error {r.status_code}: {r.text[:300]}]"
    except Exception as e:
        return f"[Anthropic API exception: {e}]"
