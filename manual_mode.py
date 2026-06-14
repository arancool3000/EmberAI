"""Manual mode: human-in-the-loop fallback when the API is exhausted/broken.

Workflow:
  1. Ember builds a detailed prompt summarizing the user's request, recent chat, and tools.
  2. User clicks 'Copy prompt', pastes it into Claude.ai / ChatGPT / any LLM.
  3. The external LLM returns code (Python or PowerShell).
  4. User pastes the code back into the manual-mode textarea and clicks 'Run'.
  5. Ember executes the code with a timeout and shows the output."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


MANUAL_PROMPT_TEMPLATE = """You are helping a Windows automation agent called Ember whose API quota
has been exhausted. The user needs you to write working {language} code that completes their task.

# User's request
{user_request}

# Recent chat / tool-call context
{recent_context}

# Current screen description (best-effort, may be stale)
{screen_summary}

# Ember's environment
- Windows 10/11
- Python 3.10+ with these libraries already installed and importable:
  pyautogui, mss, pyperclip, PIL, requests, psutil, uiautomation, send2trash, subprocess, pathlib
- The script will be run from Ember's working directory.
- pyautogui.FAILSAFE = True (moving mouse to (0,0) aborts).

# Your job
Write a complete, self-contained {language} script that solves the request.
- Print short progress messages as you go ("Opening Chrome...", "Found 12 files...").
- Handle errors gracefully (try/except around external calls).
- Do NOT prompt for input. Do NOT call exit() abruptly.
- Output ONLY the code - no explanation, no markdown fence, no chatter.
"""


def build_prompt(user_request: str, recent_chat: list[str],
                 screen_summary: str = "", language: str = "Python") -> str:
    """Compose a detailed prompt for an external LLM."""
    chat_blob = "\n".join(recent_chat[-12:]) if recent_chat else "(no recent chat)"
    chat_blob = textwrap.indent(chat_blob[:3000], "  ")
    return MANUAL_PROMPT_TEMPLATE.format(
        language=language,
        user_request=user_request or "(empty - ask the user what they want)",
        recent_context=chat_blob,
        screen_summary=screen_summary[:1500] or "(no screenshot available)",
    )


def run_python(code: str, timeout: int = 120) -> dict:
    """Run pasted Python in a subprocess. Captures stdout + stderr + exit code."""
    try:
        # Strip markdown fences if the user pasted with them
        code = _strip_code_fence(code)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=timeout,
        )
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[:12000],
            "stderr": (result.stderr or "")[:6000],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_powershell(code: str, timeout: int = 120) -> dict:
    """Run pasted PowerShell. Same contract as run_python."""
    try:
        code = _strip_code_fence(code)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", code],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[:12000],
            "stderr": (result.stderr or "")[:6000],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _strip_code_fence(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        first_newline = code.find("\n")
        if first_newline >= 0:
            code = code[first_newline + 1:]
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()
