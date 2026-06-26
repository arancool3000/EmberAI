"""Gemini-powered agent loop with tool execution (google-genai SDK)."""
from __future__ import annotations

import base64
import queue
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from google import genai
from google.genai import types

import tools
import memory
import safety
import antivirus
import web_policy
import audit
import plan
import vpn
import utilities
import cleanup
import nettools
import mediatools
import privacy
import ai_detect
import quick_tools
import power_tools
import chart_tools
import local_ai
import macros
import creative
import security_extras
import text_tools
import file_ops
import more_tools
import extra_tools
import screen_vision
import remote_server
import scheduled_tasks
# --- roadmap backlog feature modules ---
import usage as usage_tracker           # imported aliased: _send_streaming has a local var named `usage`
import key_vault
import download_guard
import fileless_guard
import security_center
import agents as agent_profiles
import agent_scheduler
import integrations
import tool_args
import workflow_recorder
import productivity_tools
import plugin_system
import custom_tools
from claude_bridge import build_handoff_prompt, copy_to_clipboard, try_anthropic_api


_OS = "macOS" if sys.platform == "darwin" else ("Windows" if sys.platform.startswith("win") else "Linux")
_SHELL = "zsh" if _OS == "macOS" else ("PowerShell" if _OS == "Windows" else "bash")
_MODKEY = "cmd" if _OS == "macOS" else "ctrl"
_LAUNCHER = "cmd+space (Spotlight)" if _OS == "macOS" else "win (Start menu)"


BASE_SYSTEM_PROMPT = f"""You are Ember, an autonomous agent running on the user's {_OS} machine. You see the
screen, drive mouse and keyboard, control a real browser via the DOM, run shell commands, and manage files.
You are capable and self-directed: figure things out and DO them. Describe only what you actually did.

# Operating principle: be independent
Default to solving the whole task yourself, end to end, without checking in. The user gave you a goal, not a
script - decompose it, act, observe, adapt. Ask for help ONLY when something is genuinely impossible for you
(a secret you don't have, a CAPTCHA/2FA, a payment signature, a biometric). Never stop just because the first
attempt failed - you have many tools; rotate through them. Treat "I'll try a different approach" as the rule,
"pause_for_human" as the rare exception. Bias toward action over questions.

# How to click and read the screen (this OS is {_OS})
- smart_click("Sign in") is your default for ANY labeled target (button, link, menu item, field, labeled
  icon). It locates the real on-screen geometry - accessibility tree first, then Vision OCR - and clicks the
  exact center. It refuses low-confidence matches instead of misclicking.
- read_screen_text() returns every visible word with exact coordinates - use it to understand a screen or to
  get a precise point when smart_click reports no confident match.
- select_screen_text("...") drags to select on-screen text; wait_for_text("...") blocks until UI appears (use
  it instead of a fixed wait after navigation/launch); assert_text_visible("...") confirms a result.
- Raw click(x,y) is a last resort for UNLABELED targets only (blank canvas, color swatch). Get the coordinate
  from read_screen_text or zoom_screenshot first - never eyeball it off the grid.

# Deciding when to look at the screen (YOUR judgment, every turn)
You do NOT get a screenshot automatically — capturing the screen is a deliberate choice you make. Before acting,
ask "do I actually need to see pixels for THIS?" and pick:
- SEE the desktop -> call take_screenshot (whole screen) or capture_window("App") first, THEN act. Do this for
  clicking/reading on-screen UI, automating a desktop app, "what's on my screen?", or visually verifying a result.
  To see AND read text in one step, issue take_screenshot + read_screen_text together (they run in parallel).
- BROWSER work -> read the live DOM with browser_get_page instead of a screenshot — it's faster and exact. Only
  screenshot a web page for a genuinely visual question the DOM can't answer.
- NO screen needed -> general knowledge, math, writing, files, shell, scheduling, memory, API/data work: just do
  it. Never screenshot "just in case" — a needless capture burns a model call and tells you nothing.
Don't be triggered by the wording of the request (a message that merely says "screen" or "open" may not need a
capture); be triggered by whether the NEXT action genuinely depends on current screen contents. After capturing,
reason fully in one pass and only re-capture once the screen has actually changed.

# Method hierarchy (pick the highest that fits)
1. Browser task -> browser_* (DOM, not pixels): browser_open -> browser_dismiss_cookies(mode="reject") ->
   browser_get_page -> browser_click_text / browser_fill. CAPTCHA/anti-bot: browser_check_captcha then
   pause_for_human. Never attempt to bypass bot protection.
2. Desktop app -> smart_click / read_screen_text / find_ui_elements.
3. Keyboard -> press_key. On {_OS}: {_MODKEY}+c/v/t/w/l, app launch via {_LAUNCHER}. Many panels are
   keyboard-navigable (tab, arrows, enter) - often faster than clicking.
4. Pixel click -> only for unlabeled targets, coordinate sourced from a tool.

# Website agent mode
For random websites, behave like a patient browser operator, not a search-only chatbot. Open the site, read the
structured page with browser_get_page, choose elements by text/role/selector, fill forms, scroll, use tabs, and
verify after each meaningful navigation. Prefer browser_click_text/browser_fill/browser_evaluate over screenshot
clicks. If a page changes under you, call browser_get_page again instead of guessing. For cookie banners, reject
non-essential cookies when possible, then continue. For CAPTCHA/2FA/bot checks, detect with browser_check_captcha
and pause for the user; you may continue after the user solves or explicitly authorizes a manual step, but never
claim you bypassed protection.

# Double-clicking and fiddly UI
If the user asks to double-click, open a file/app, select a desktop icon, or interact with a random small button,
use the built-in double-click controls: smart_click(..., double=true), click(..., double=true), or
browser_click_text(..., mode="double"). If the first click does not activate the target, try double-click or
keyboard Enter before giving up. Use zoom_screenshot/read_screen_text for small or ambiguous controls.

# Speed — every step costs ONE model API call, so minimize steps (THIS IS A HARD RULE)
- The free tier allows only ~15 calls PER MINUTE. Each round-trip of tool calls is one call. Many small
  one-tool-at-a-time rounds is the single biggest waste. Treat 1-2 tool steps as the target for a normal turn,
  4-5 as a lot, and anything beyond that as a sign you are over-investigating.
- BATCH every independent read into ONE step. If you need to look at 3 files, list a folder, and search text,
  request read_file + read_file + read_file + list_directory + grep_files TOGETHER in a single response - they
  run concurrently as one call. NEVER fetch them one per round. Before you emit a step, ask: "what is every
  piece of info I could possibly need next?" and request it all now.
- Use the DEDICATED read tools, never shell for reading. read_file (not run_shell cat/head/tail),
  grep_files (not run_shell grep/rg), list_directory/folder_tree (not run_shell ls/find), count_lines, json_query.
  These dedicated tools batch concurrently; run_shell does NOT batch and burns a whole call each time, so a
  "cat then grep then ls" sequence is 3 wasted calls when one batched read step would do it. Reserve run_shell
  for things with no dedicated tool (e.g. running a build), and even then combine commands with && in ONE call.
- STOP as soon as you can act or answer. Do not gather "just in case" context. If a tool result already answers
  the user, reply immediately - do not take another look. Verification is ONE final check, not a habit between
  every action.
- Batch deterministic UI steps with do_sequence (one API request). Include waits and a final assertion inside
  the same do_sequence whenever the next action is obvious. Don't screenshot between every action -
  screenshot/assert_text_visible once at the END to verify.
- Understand the screen in ONE pass: when you DO capture, batch take_screenshot with read_screen_text so you
  get the image plus an exact word+coordinate map together - read it and reason fully BEFORE acting. Don't take
  a second screenshot just to re-read the same screen; only re-capture after the screen actually changes.
- paste_text beats type_text for anything over a few words.
- Never repeat an identical failing call. Change tool, args, or tactic.
- Prefer high-signal tools over broad ones: browser_get_page for webpages, read_screen_text(query=...) for
  visible labels, list_directory with a narrow path/pattern for files.

# Reasoning pattern (think harder on anything non-trivial)
Before acting, think quietly: restate the user's REAL goal (not just the literal words), the minimum evidence you
need, the safest tool path, and exactly how you will verify success. For anything ambiguous or multi-step, form an
ordered plan first and pursue it; when two readings are plausible, choose the most useful and proceed rather than
asking. After each step, compare the result to what you expected and adapt — if something is surprising, find out
why before continuing instead of plowing ahead. Prefer reversible actions, preserve state, and remember durable
preferences/paths only when they will help future work. For genuinely hard reasoning or a stubborn blocker,
escalate to ask_claude rather than guessing. Do not narrate this plan unless the user asks.

# Self-recovery ladder (exhaust before pausing)
smart_click -> read_screen_text to see exact labels -> find_ui_elements (try a partial match, scroll, or
scope="desktop") -> keyboard navigation -> right_click_element_by_text then read the menu -> last, click(x,y)
from a tool-sourced coordinate. Only after these: ask_claude (hard reasoning) or pause_for_human (truly blocked).

# Building your own tools (you can extend yourself)
When the user asks you to remember a repeatable multi-step procedure ("every morning, tidy Downloads and
summarize my unread tabs"), or you notice you keep running the same sequence, BUILD A TOOL for it with
create_custom_tool: give it a snake_case name, a description, optional parameters, and steps that each call a
tool you already have (use {{placeholders}} in step args for inputs). It persists across restarts. Run it later
with run_custom_tool(name=..., args={...}); see what you've built with list_custom_tools; share one with
export_custom_tool / import_custom_tool. Each recipe step is still gated by the normal safety rules, so a custom
tool can't do anything you couldn't already do. Prefer a custom tool over re-deriving the same steps each time.

# Who you are (answer this directly if asked "what is Ember / what can you do")
You are Ember, a local AI agent that lives on the user's {_OS} computer. Unlike a chatbot, you can ACT on
this machine: see the screen, move the mouse and type, open and control apps, drive a real browser, read and
organize files, diagnose problems, and run shell commands. You can also be controlled from the user's phone
(the "Connect phone" button starts a live remote). When asked what you are, say this plainly and offer to do
something concrete.

# Organizing files smartly
1. First UNDERSTAND: list_directory + get_folder_size to see what's there. Never reorganize blind.
2. Propose a scheme before moving: by type (Images/Documents/Audio/Video/Archives/Code), by date, or by
   project - pick what fits what you actually see. Tell the user the plan in one line.
3. ALWAYS organize_folder(dry_run=true) first, show what WOULD move, then run for real once it looks right.
4. Use find_duplicate_files before deleting; trash_file (recoverable) never permanent delete; bulk_rename for
   messy names. Keep originals safe - moving/sorting beats deleting.
5. If a requested file is "missing", do not stop at Documents/Downloads. Search likely overlooked locations:
   Desktop, Downloads, Documents, iCloud Drive, OneDrive, /Users/Shared, recent folders, and Trash/Recycle Bin.
   Use search_files first; it checks common overlooked places including Trash on macOS.

# Conversation memory inside this session
Treat every user message as part of one continuous conversation. Use the previous messages and the Ember UI
context block by default, especially for pronouns ("it", "that", "same thing"), continuations, corrections, and
"as I said" follow-ups. Do not make the user restate context unless it is genuinely ambiguous.

# Voice chat mode
Some messages may be live microphone transcripts from Ember Voice Chat. Treat them as natural spoken commands:
infer minor transcription glitches from context, answer more concisely than you would in a long written report,
and keep the interaction moving unless a safety boundary, login, payment, CAPTCHA/2FA, or genuinely irreversible
choice requires the human.

# Scheduling
When the user asks for a future task, reminder-like command, or background timed action, use
schedule_shell_command/list_scheduled_tasks/cancel_scheduled_task instead of saying you cannot schedule. On macOS
this creates a user LaunchAgent; on Windows it creates a Task Scheduler entry. Keep commands simple, quote paths,
and tell the user exactly what was scheduled and when. Ask before scheduling destructive commands.

# Diagnosing computer issues (macOS)
Gather evidence before concluding: get_system_info + get_performance (CPU/RAM/disk pressure),
get_running_processes (find the hog), get_battery, and run_shell for specifics (e.g. `df -h` for disk,
`vm_stat` for memory, `pmset -g thermlog` for thermal, `log show --last 1h --predicate 'eventMessage
contains "error"'` for recent errors). Correlate timestamps, name the most likely cause, propose ONE concrete
fix, and ask before anything risky. Save findings with remember().

# Memory
remember(key,value) anything durable (paths, preferences, recurring issues) - facts auto-inject next session.

# Honesty (non-negotiable - this is what earns trust)
- Claim success ONLY with evidence: tool returned ok=True AND, for visible changes, assert_text_visible or a
  screenshot confirms it. Verify before you say "done".
- Never invent durations, paths, contents, counts, or outputs. Measure time with now() at start and end.
- In any summary, a failed step is reported as "FAILED: <error>", never papered over or fabricated.
- Quote literal returned values (paths from write_file, counts from get_folder_size, etc.).

# Output
Talk like a normal, capable assistant - natural and concise. Do NOT prefix replies with "Done:" or
narrate your instructions. Just answer questions directly and conversationally. ONLY after completing a
real multi-step task, you may end with a brief honest status line (what worked / what failed) - but for
simple questions or chat, skip it entirely. Never fake a duration; quote real now() numbers if asked.
"""


def system_extras() -> str:
    """The volatile part of the system prompt (memory facts + recent actions) that changes
    every turn. Kept separate from BASE_SYSTEM_PROMPT so the Claude backend can prompt-cache
    the large static prefix (tools + base prompt) and only re-send this small tail."""
    facts = memory.get_facts_summary()
    recent = memory.get_recent_actions(n=6)
    extras = []
    if facts:
        extras.append(f"\n# Known facts about this user's system (from past sessions)\n{facts}")
    if recent:
        action_lines = []
        for a in recent:
            args_short = ", ".join(f"{k}={v}" for k, v in (a.get("args") or {}).items())
            action_lines.append(f"- {a['name']}({args_short[:80]}) -> {a['result'][:80]}")
        extras.append(f"\n# Recent actions (last session/turn)\n" + "\n".join(action_lines))
    return ("\n" + "\n".join(extras)) if extras else ""


def build_system_prompt() -> str:
    return BASE_SYSTEM_PROMPT + system_extras()


TOOL_DECLARATIONS = [
    # ---- Vision / sensing ----
    {
        "name": "take_screenshot",
        "description": (
            "Capture the screen. Coordinate grid is overlaid in ORIGINAL screen pixels: cyan every 100px, "
            "yellow every 500px, green labels at every 200x200 intersection. A red ring shows the current "
            "mouse cursor position. Use the labels' numbers when calling click()."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "grid": {"type": "BOOLEAN", "description": "overlay grid (default true)"},
                "show_cursor": {"type": "BOOLEAN", "description": "show cursor ring (default true)"},
            },
            "required": [],
        },
    },
    {
        "name": "capture_window",
        "description": "Screenshot a single window by partial title match. Avoids OS chrome/distractions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"title_contains": {"type": "STRING"}, "grid": {"type": "BOOLEAN"}},
            "required": ["title_contains"],
        },
    },
    {
        "name": "get_screen_size",
        "description": "Get the actual screen resolution.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "zoom_screenshot",
        "description": (
            "Capture a high-detail 2x-magnified crop around a target point. Call this BEFORE click() "
            "when you need to verify exactly what's at (x, y) - especially on small icons or dense UIs. "
            "Returns an image with a red crosshair on the requested point so you can adjust if needed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER"},
                "y": {"type": "INTEGER"},
                "radius": {"type": "INTEGER", "description": "half-width of capture (default 150)"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "mouse_position",
        "description": "Get current mouse cursor coordinates.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    # ---- Precise targeting (no grid guessing) ----
    {
        "name": "smart_click",
        "description": (
            "PREFERRED way to click anything. Locates the target by its REAL on-screen geometry - "
            "first via the accessibility tree (buttons/fields/menus by name), then via Vision OCR "
            "(any visible text becomes an exact click point) - then clicks the precise center. "
            "Never guesses off the grid. If no candidate is confident it REFUSES to click and returns "
            "ranked alternatives instead of misclicking. Use this before take_screenshot+click(x,y)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "visible label/text of what to click, e.g. 'Sign in'"},
                "double": {"type": "BOOLEAN", "description": "double-click (default false)"},
                "button": {"type": "STRING", "description": "left or right (default left)"},
                "prefer": {"type": "STRING", "description": "auto | ax | ocr (default auto)"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "read_screen_text",
        "description": (
            "Read EVERY piece of text currently visible on screen via OCR, each with its exact "
            "clickable center coordinates. Use this to 'see' labels precisely instead of estimating "
            "pixel positions, or to find where a specific word is. Pass 'query' to rank by a phrase."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "optional phrase to rank results by"},
                "region": {"type": "OBJECT", "description": "optional {x,y,width,height} in screen points"},
            },
            "required": [],
        },
    },
    {
        "name": "locate_text",
        "description": (
            "Return the exact screen coordinates of a piece of on-screen text WITHOUT clicking. "
            "Useful to plan a click, verify something is present, or read a value's position. "
            "occurrence selects the Nth match when the text appears more than once."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING"},
                "occurrence": {"type": "INTEGER", "description": "1-based Nth match (default 1)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "select_screen_text",
        "description": (
            "Select on-screen text by dragging across it like a human (the 'select text on screen' tool). "
            "Locates the start phrase by OCR and drags to its end - or to 'to_text' for a multi-line range. "
            "Set copy=true to also copy the selection to the clipboard. No blind guessing of positions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "phrase to start the selection at"},
                "to_text": {"type": "STRING", "description": "optional phrase to end the selection at"},
                "occurrence": {"type": "INTEGER", "description": "1-based Nth match of 'text' (default 1)"},
                "copy": {"type": "BOOLEAN", "description": "copy selection to clipboard (default false)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "wait_for_text",
        "description": (
            "Block until a piece of text appears on screen, then return its location. Use this "
            "after an action that loads new UI (page nav, app launch, dialog) instead of a fixed wait() "
            "- it returns as soon as the text is there, so it's both faster and more reliable."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING"},
                "timeout": {"type": "NUMBER", "description": "max seconds to wait (default 10)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "assert_text_visible",
        "description": (
            "Verify that specific text is currently visible on screen. Returns ok=true with its "
            "location, or ok=false. Use this to CONFIRM a step worked before reporting success - "
            "satisfies the honesty rule that visible changes need evidence."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}},
            "required": ["text"],
        },
    },
    {
        "name": "start_remote_control",
        "description": (
            "Start Ember Link: a phone remote for THIS PC. Returns a LAN URL + PIN; the user opens "
            "the URL on a phone (same Wi-Fi), enters the PIN, and gets a live screen view they can tap "
            "to click, plus a trackpad and keyboard. Use when the user wants to control the PC from their "
            "phone (e.g. broken mouse/keyboard). Tell the user the returned url and pin."
        ),
        "parameters": {"type": "OBJECT", "properties": {"port": {"type": "INTEGER"}}, "required": []},
    },
    {
        "name": "stop_remote_control",
        "description": "Stop the Ember Link phone-remote server.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "remote_control_status",
        "description": "Check whether Ember Link phone-remote is running, and its url/pin.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    # ---- UI Automation (text-based clicking) ----
    {
        "name": "find_ui_elements",
        "description": (
            "List UI elements (buttons, links, text fields) on screen via Windows UI Automation, "
            "filtered by fuzzy match on 'filter_text'. Returns center coords and metadata. "
            "Prefer this over coordinate clicking when possible. scope='foreground' is fast, 'desktop' is slow."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filter_text": {"type": "STRING"},
                "scope": {"type": "STRING", "description": "foreground or desktop"},
                "max_results": {"type": "INTEGER"},
            },
            "required": [],
        },
    },
    {
        "name": "click_element_by_text",
        "description": "Click the UI element whose accessible name/auto_id/help-text best matches the given text. Use this FIRST when clicking labeled controls. Retries 3x with brief waits to handle transient UI.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING"},
                "scope": {"type": "STRING"},
                "double": {"type": "BOOLEAN"},
                "button": {"type": "STRING", "description": "left/right/middle"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "right_click_element_by_text",
        "description": (
            "Right-click a UI element by text and wait briefly so the context menu is fully painted. "
            "After this, call find_ui_elements (popups are now searched automatically) and "
            "click_element_by_text on the menu item you want."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}, "scope": {"type": "STRING"}},
            "required": ["text"],
        },
    },
    {
        "name": "list_windows",
        "description": "Enumerate top-level visible windows with titles and bounding boxes.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "focus_window",
        "description": "Bring a window to the foreground by partial title match.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"title_contains": {"type": "STRING"}},
            "required": ["title_contains"],
        },
    },
    # ---- Mouse / keyboard ----
    {
        "name": "click",
        "description": "Click at screen coordinates in ORIGINAL (unscaled) resolution.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
                "button": {"type": "STRING"}, "double": {"type": "BOOLEAN"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "move_mouse",
        "description": "Move the cursor without clicking.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}, "duration": {"type": "NUMBER"}},
            "required": ["x", "y"],
        },
    },
    {
        "name": "drag",
        "description": "Press and drag the mouse from one point to another. Use for dragging files, sliders, selecting text.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "from_x": {"type": "INTEGER"}, "from_y": {"type": "INTEGER"},
                "to_x": {"type": "INTEGER"}, "to_y": {"type": "INTEGER"},
                "button": {"type": "STRING"}, "duration": {"type": "NUMBER"},
            },
            "required": ["from_x", "from_y", "to_x", "to_y"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text via simulated keystrokes (slower, but works in any focused control).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}, "interval": {"type": "NUMBER"}},
            "required": ["text"],
        },
    },
    {
        "name": "paste_text",
        "description": "Set clipboard then Ctrl+V. Much faster than type_text for long strings or special chars.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}},
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a key or combo. Examples: 'enter', 'tab', 'esc', 'ctrl+c', 'win+r', 'alt+f4', 'win+e'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"keys": {"type": "STRING"}},
            "required": ["keys"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the window under the mouse (or move to x,y first).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {"type": "STRING"}, "amount": {"type": "INTEGER"},
                "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Sleep for N seconds (max 30).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"seconds": {"type": "NUMBER"}},
            "required": ["seconds"],
        },
    },
    {
        "name": "wait_for_screen_change",
        "description": "Block until the screen visually changes (e.g. page loads, dialog appears). Better than blind sleep.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"timeout": {"type": "NUMBER"}, "sample_interval": {"type": "NUMBER"}},
            "required": [],
        },
    },
    # ---- The big one: batched execution ----
    {
        "name": "do_sequence",
        "description": (
            "Execute a list of tool calls in one round to save API quota. Each action is "
            "{\"tool\": \"name\", \"args\": {...}, \"continue_on_error\": false}. Stops on first failure unless "
            "continue_on_error. Use for any predictable multi-step action (open app via Start menu, fill a form, etc.)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"actions": {"type": "ARRAY", "items": {"type": "OBJECT"}}},
            "required": ["actions"],
        },
    },
    # ---- Shell ----
    {
        "name": "run_shell",
        "description": (
            f"Execute a shell command on this {_OS} machine (runs in {_SHELL}). "
            f"Use {_SHELL} syntax, NOT PowerShell. Reserve this for things you can't do via the UI."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"command": {"type": "STRING"}, "timeout": {"type": "INTEGER"}},
            "required": ["command"],
        },
    },
    # ---- Files ----
    {
        "name": "read_file",
        "description": "Read a text file.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}, "max_bytes": {"type": "INTEGER"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write text to a file (overwrites).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}, "content": {"type": "STRING"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List directory entries; optional glob pattern.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}, "pattern": {"type": "STRING"}},
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search filenames quickly. Uses Spotlight first, then overlooked places like Trash/iCloud/Downloads/Desktop.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING"},
                "root": {"type": "STRING"},
                "max_results": {"type": "INTEGER"},
                "include_overlooked": {"type": "BOOLEAN"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a URL in the default browser.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING"}},
            "required": ["url"],
        },
    },
    {
        "name": "open_app",
        "description": "Launch an app by name or path ('notepad', 'devmgmt.msc', etc). Last resort - prefer Start menu.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"name": {"type": "STRING"}},
            "required": ["name"],
        },
    },
    {
        "name": "open_path",
        "description": "Open a file or folder via the OS shell (Explorer / default app).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}},
            "required": ["path"],
        },
    },
    # ---- Diagnostics ----
    {
        "name": "get_event_logs",
        "description": "Read recent Windows Event Log entries.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "log_name": {"type": "STRING"},
                "hours": {"type": "INTEGER"},
                "level": {"type": "STRING"},
            },
            "required": [],
        },
    },
    {
        "name": "get_reliability_events",
        "description": "Reliability Monitor: crashes, hangs, install failures, BSODs.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"days": {"type": "INTEGER"}},
            "required": [],
        },
    },
    {
        "name": "get_minidumps",
        "description": "List BSOD minidump files with timestamps.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "get_system_info",
        "description": "OS, CPU, RAM, GPU, BIOS, uptime.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "get_installed_drivers",
        "description": "Installed drivers; filter_text narrows by device name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"filter_text": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "get_running_processes",
        "description": "Top 50 processes by RAM, optionally filtered.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"filter_text": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "get_performance",
        "description": "Live CPU, RAM, disk, network snapshot.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "get_windows_updates",
        "description": "Recent Windows Update history.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"days": {"type": "INTEGER"}},
            "required": [],
        },
    },
    # ---- Quick fixes ----
    {
        "name": "list_quick_fixes",
        "description": "List the named one-shot fix recipes available via quick_fix.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "quick_fix",
        "description": (
            "Run a named one-shot fix: flush_dns, restart_explorer, reset_network, sfc_scan, dism_restore, "
            "clear_temp, check_disk, release_renew_ip, show_startup, show_services."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"name": {"type": "STRING"}},
            "required": ["name"],
        },
    },
    # ---- Memory ----
    {
        "name": "remember",
        "description": "Save a fact for future sessions (e.g. user's GoPro folder path).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key": {"type": "STRING"},
                "value": {"type": "STRING"},
                "category": {"type": "STRING"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Look up saved facts. Returns all if query is empty, else fuzzy matches.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "forget",
        "description": "Delete a saved fact by key.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"key": {"type": "STRING"}},
            "required": ["key"],
        },
    },
    # ---- File organization ----
    {
        "name": "organize_folder",
        "description": (
            "Sort files in a folder into subfolders. mode='type' (Images/Videos/Documents/Audio/Archives/"
            "Code/Other), 'extension' (one subfolder per extension), 'date' (YYYY-MM), 'size' (Tiny/Small/"
            "Medium/Large/Huge). Always run with dry_run=true first to preview."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "mode": {"type": "STRING"},
                "dry_run": {"type": "BOOLEAN"},
                "include_subfolders": {"type": "BOOLEAN"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_duplicate_files",
        "description": "Find duplicate files in a folder (by content hash). Groups by identical contents.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "min_size_kb": {"type": "INTEGER"},
                "recursive": {"type": "BOOLEAN"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_large_files",
        "description": "List biggest files in a folder, descending. Great for disk-cleanup tasks.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "min_mb": {"type": "INTEGER"},
                "max_results": {"type": "INTEGER"},
                "recursive": {"type": "BOOLEAN"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "bulk_rename",
        "description": "Batch rename files in a folder by substring or regex replacement.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "folder": {"type": "STRING"},
                "pattern": {"type": "STRING"},
                "replacement": {"type": "STRING"},
                "regex": {"type": "BOOLEAN"},
                "dry_run": {"type": "BOOLEAN"},
            },
            "required": ["folder", "pattern", "replacement"],
        },
    },
    {
        "name": "move_matching_files",
        "description": "Move files matching a glob pattern from one folder to another.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source": {"type": "STRING"},
                "destination": {"type": "STRING"},
                "pattern": {"type": "STRING"},
                "dry_run": {"type": "BOOLEAN"},
            },
            "required": ["source", "destination"],
        },
    },
    {
        "name": "get_folder_size",
        "description": "Total bytes, file count, and per-type breakdown for a folder.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "recursive": {"type": "BOOLEAN"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "unzip_archive",
        "description": "Extract .zip / .tar / .tar.gz / .tar.bz2 archive.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "archive_path": {"type": "STRING"},
                "destination": {"type": "STRING"},
            },
            "required": ["archive_path"],
        },
    },
    {
        "name": "zip_files",
        "description": "Compress a list of file paths into a .zip.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "files": {"type": "ARRAY", "items": {"type": "STRING"}},
                "destination": {"type": "STRING"},
            },
            "required": ["files", "destination"],
        },
    },
    {
        "name": "folder_tree",
        "description": "Compact text tree summary of a folder's structure.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "max_depth": {"type": "INTEGER"},
                "max_per_level": {"type": "INTEGER"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_cluttered_demo_folder",
        "description": "Create a deliberately messy demo folder with duplicates, vague names, mixed types, and nested clutter.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "overwrite": {"type": "BOOLEAN"},
            },
            "required": [],
        },
    },
    {
        "name": "trash_file",
        "description": "Soft-delete: move to Recycle Bin (Win) / Trash (Mac). Reversible.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING"}},
            "required": ["path"],
        },
    },
    # ---- Web / network ----
    {"name": "http_get",
     "description": "GET an HTTP URL. Returns status, headers, text body, parsed JSON if applicable.",
     "parameters": {"type": "OBJECT", "properties": {
        "url": {"type": "STRING"}, "headers": {"type": "OBJECT"}, "timeout": {"type": "INTEGER"}},
        "required": ["url"]}},
    {"name": "http_post",
     "description": "POST to a URL. data=form-encoded dict or raw string; json_body=parsed JSON dict.",
     "parameters": {"type": "OBJECT", "properties": {
        "url": {"type": "STRING"}, "data": {"type": "STRING"}, "json_body": {"type": "OBJECT"},
        "headers": {"type": "OBJECT"}, "timeout": {"type": "INTEGER"}},
        "required": ["url"]}},
    {"name": "download_file",
     "description": "Stream a URL to a local file.",
     "parameters": {"type": "OBJECT", "properties": {
        "url": {"type": "STRING"}, "destination": {"type": "STRING"}, "timeout": {"type": "INTEGER"}},
        "required": ["url", "destination"]}},
    {"name": "public_ip", "description": "Get this machine's public IP via ipify.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "dns_lookup", "description": "Resolve a hostname to IPs.",
     "parameters": {"type": "OBJECT", "properties": {"host": {"type": "STRING"}}, "required": ["host"]}},
    {"name": "network_ping", "description": "Ping a host N times.",
     "parameters": {"type": "OBJECT", "properties": {
        "host": {"type": "STRING"}, "count": {"type": "INTEGER"}}, "required": ["host"]}},
    {"name": "web_search",
     "description": "Search the web via DuckDuckGo (no API key). Returns titles, URLs, snippets.",
     "parameters": {"type": "OBJECT", "properties": {
        "query": {"type": "STRING"}, "max_results": {"type": "INTEGER"}}, "required": ["query"]}},
    {"name": "wikipedia_summary",
     "description": "Get the intro paragraph of a Wikipedia article.",
     "parameters": {"type": "OBJECT", "properties": {
        "topic": {"type": "STRING"}, "sentences": {"type": "INTEGER"}}, "required": ["topic"]}},
    {"name": "weather_lookup",
     "description": "Free Open-Meteo current weather by lat/lon (decimal degrees).",
     "parameters": {"type": "OBJECT", "properties": {
        "latitude": {"type": "NUMBER"}, "longitude": {"type": "NUMBER"}},
        "required": ["latitude", "longitude"]}},
    {"name": "translate_text",
     "description": "Translate text between languages. target_lang/source_lang are 2-letter codes (en, es, fr, ja, etc.) or 'auto'.",
     "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"}, "target_lang": {"type": "STRING"}, "source_lang": {"type": "STRING"}},
        "required": ["text"]}},
    # ---- Email ----
    {"name": "send_email",
     "description": "Send an email via SMTP. Credentials come from settings (email_smtp_*) if not passed. For Gmail use an App Password.",
     "parameters": {"type": "OBJECT", "properties": {
        "to": {"type": "STRING"}, "subject": {"type": "STRING"}, "body": {"type": "STRING"},
        "html": {"type": "BOOLEAN"}}, "required": ["to", "subject", "body"]}},
    # ---- Documents ----
    {"name": "pdf_extract_text",
     "description": "Extract text from a PDF (embedded text only - no OCR).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "max_pages": {"type": "INTEGER"}}, "required": ["path"]}},
    {"name": "excel_read",
     "description": "Read an .xlsx file into a list of rows.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "sheet": {"type": "STRING"}, "max_rows": {"type": "INTEGER"}},
        "required": ["path"]}},
    {"name": "excel_write",
     "description": "Write a list of rows to an .xlsx file. rows is a list of lists of cells.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "rows": {"type": "ARRAY", "items": {"type": "ARRAY", "items": {"type": "STRING"}}},
        "sheet": {"type": "STRING"}}, "required": ["path", "rows"]}},
    {"name": "json_query",
     "description": "Read a JSON file and pull a value via dotted path ('users.0.name').",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "dotted_path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "csv_read",
     "description": "Read a CSV file into rows.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "max_rows": {"type": "INTEGER"}, "delimiter": {"type": "STRING"}},
        "required": ["path"]}},
    # ---- Math / utility ----
    {"name": "calculator",
     "description": "Safely evaluate a math expression. Supports + - * / // % **, math.* functions.",
     "parameters": {"type": "OBJECT", "properties": {"expression": {"type": "STRING"}}, "required": ["expression"]}},
    {"name": "generate_password",
     "description": "Generate a cryptographically random password.",
     "parameters": {"type": "OBJECT", "properties": {
        "length": {"type": "INTEGER"}, "include_symbols": {"type": "BOOLEAN"}}, "required": []}},
    {"name": "generate_uuid", "description": "Generate a v4 UUID.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "hash_text",
     "description": "Hash a string. algorithm = md5, sha1, sha256 (default), sha512, blake2b, etc.",
     "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"}, "algorithm": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "hash_file", "description": "Hash a file's contents.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "algorithm": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "base64_encode", "description": "Base64-encode text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "base64_decode", "description": "Base64-decode text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "url_encode", "description": "URL-encode text (percent-encoding).",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "url_decode", "description": "URL-decode (percent decode) text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "now",
     "description": "Current date/time. timezone_name='local'|'utc'|'America/New_York'|etc.",
     "parameters": {"type": "OBJECT", "properties": {"timezone_name": {"type": "STRING"}}, "required": []}},
    # ---- System / hardware ----
    {"name": "power_action",
     "description": "Lock/sleep/restart/shutdown/hibernate/logoff the PC. DESTRUCTIVE for restart/shutdown.",
     "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING"}, "force": {"type": "BOOLEAN"}}, "required": ["action"]}},
    {"name": "get_battery", "description": "Battery percent + plugged-in state.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "get_volume", "description": "Get system master volume (Windows).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "set_volume", "description": "Set system master volume 0-100 (Windows).",
     "parameters": {"type": "OBJECT", "properties": {"percent": {"type": "NUMBER"}}, "required": ["percent"]}},
    {"name": "toggle_mute", "description": "Toggle system mute state.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "env_get", "description": "Read an environment variable.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "env_list", "description": "List environment variables (optionally filter by prefix).",
     "parameters": {"type": "OBJECT", "properties": {"prefix": {"type": "STRING"}}, "required": []}},
    # ---- Image ----
    {"name": "image_resize",
     "description": "Resize an image so its longer side is at most max_dimension.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "max_dimension": {"type": "INTEGER"},
        "destination": {"type": "STRING"}, "quality": {"type": "INTEGER"}}, "required": ["path"]}},
    {"name": "image_crop", "description": "Crop an image to (left,top,right,bottom).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "left": {"type": "INTEGER"}, "top": {"type": "INTEGER"},
        "right": {"type": "INTEGER"}, "bottom": {"type": "INTEGER"}, "destination": {"type": "STRING"}},
        "required": ["path", "left", "top", "right", "bottom"]}},
    {"name": "image_convert", "description": "Convert image format (png, jpg, webp, bmp, etc.).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "output_format": {"type": "STRING"}, "destination": {"type": "STRING"}},
        "required": ["path", "output_format"]}},
    {"name": "color_at", "description": "Get RGB color at a screen coordinate.",
     "parameters": {"type": "OBJECT", "properties": {
        "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}, "required": ["x", "y"]}},
    {"name": "qr_generate", "description": "Generate a QR-code PNG.",
     "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"}, "destination": {"type": "STRING"}, "size": {"type": "INTEGER"}},
        "required": ["text"]}},
    # ---- Clipboard ----
    {"name": "clipboard_get", "description": "Get current clipboard text.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "clipboard_set", "description": "Set clipboard text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "clipboard_history_get", "description": "Get in-process clipboard history snapshots.",
     "parameters": {"type": "OBJECT", "properties": {"max_items": {"type": "INTEGER"}}, "required": []}},
    {"name": "clipboard_history_snapshot", "description": "Capture current clipboard into history ring.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Git ----
    {"name": "git_status", "description": "git status -sb of a repo.",
     "parameters": {"type": "OBJECT", "properties": {"repo": {"type": "STRING"}}, "required": []}},
    {"name": "git_log", "description": "Recent git log entries.",
     "parameters": {"type": "OBJECT", "properties": {
        "repo": {"type": "STRING"}, "count": {"type": "INTEGER"}}, "required": []}},
    {"name": "git_diff", "description": "git diff (or --staged).",
     "parameters": {"type": "OBJECT", "properties": {
        "repo": {"type": "STRING"}, "staged": {"type": "BOOLEAN"}}, "required": []}},
    # ---- Calendar ----
    {"name": "create_calendar_event",
     "description": "Generate an .ics calendar event file. Open it to add to Outlook / Google Calendar.",
     "parameters": {"type": "OBJECT", "properties": {
        "title": {"type": "STRING"}, "start": {"type": "STRING", "description": "ISO 8601 e.g. 2026-06-01T15:00"},
        "end": {"type": "STRING"}, "description": {"type": "STRING"},
        "location": {"type": "STRING"}, "destination": {"type": "STRING"}},
        "required": ["title", "start"]}},
    # ---- Persistent scheduling ----
    {"name": "schedule_shell_command",
     "description": (
         "Schedule a persistent future shell command. macOS uses a per-user LaunchAgent; "
         "Windows uses Task Scheduler. repeat = once/hourly/daily/weekly. run_at local time "
         "like '2026-06-12 21:30'. Ask before destructive commands."
     ),
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"},
        "command": {"type": "STRING"},
        "run_at": {"type": "STRING"},
        "repeat": {"type": "STRING"},
        "working_directory": {"type": "STRING"}},
        "required": ["name", "command", "run_at"]}},
    {"name": "list_scheduled_tasks",
     "description": "List scheduled tasks that Ember created.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "cancel_scheduled_task",
     "description": "Cancel a scheduled task by Ember task_id.",
     "parameters": {"type": "OBJECT", "properties": {"task_id": {"type": "STRING"}}, "required": ["task_id"]}},
    # ---- Window management ----
    {"name": "snap_window",
     "description": "Snap focused window left/right/maximize (Win+arrow). direction = left/right/up/down/maximize/restore.",
     "parameters": {"type": "OBJECT", "properties": {"direction": {"type": "STRING"}}, "required": ["direction"]}},
    {"name": "move_window",
     "description": "Move (and optionally resize) a window by title match.",
     "parameters": {"type": "OBJECT", "properties": {
        "title_contains": {"type": "STRING"}, "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
        "w": {"type": "INTEGER"}, "h": {"type": "INTEGER"}}, "required": ["title_contains", "x", "y"]}},
    {"name": "minimize_all_other_windows", "description": "Win+Home - minimize everything but the focused window.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "list_monitors", "description": "Enumerate monitors with their bounds.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "show_desktop", "description": "Win+D - show desktop / restore.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "switch_window", "description": "Alt+Tab to the previous window.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Media keys ----
    {"name": "media_keys",
     "description": "Send media-key keystrokes - controls Spotify/YouTube/etc. action=play_pause|next|prev|stop|vol_up|vol_down|mute",
     "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}}, "required": ["action"]}},
    # ---- Notifications ----
    {"name": "show_notification", "description": "Show a native Windows toast notification.",
     "parameters": {"type": "OBJECT", "properties": {
        "title": {"type": "STRING"}, "body": {"type": "STRING"}}, "required": ["title"]}},
    # ---- Process control ----
    {"name": "kill_process",
     "description": "Terminate a process by name (e.g. 'chrome.exe') or PID. USE WITH CARE.",
     "parameters": {"type": "OBJECT", "properties": {"name_or_pid": {"type": "STRING"}}, "required": ["name_or_pid"]}},
    {"name": "service_action",
     "description": "Windows service: action = status | start | stop | restart.",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "action": {"type": "STRING"}}, "required": ["name"]}},
    # ---- File content search ----
    {"name": "grep_files",
     "description": "Search file contents for a pattern. Optionally regex.",
     "parameters": {"type": "OBJECT", "properties": {
        "folder": {"type": "STRING"}, "pattern": {"type": "STRING"},
        "file_glob": {"type": "STRING"}, "regex": {"type": "BOOLEAN"},
        "max_results": {"type": "INTEGER"}}, "required": ["folder", "pattern"]}},
    {"name": "diff_files", "description": "Unified diff between two text files.",
     "parameters": {"type": "OBJECT", "properties": {
        "path1": {"type": "STRING"}, "path2": {"type": "STRING"}}, "required": ["path1", "path2"]}},
    {"name": "count_lines", "description": "Lines, words, chars of a text file.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    # ---- Web extras ----
    {"name": "speed_test", "description": "Internet download speed test via CloudFlare (~10 MB sample).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "define_word", "description": "English dictionary lookup (definitions + part of speech).",
     "parameters": {"type": "OBJECT", "properties": {"word": {"type": "STRING"}}, "required": ["word"]}},
    {"name": "currency_convert", "description": "Convert between currencies (e.g. USD->EUR, free Frankfurter.app).",
     "parameters": {"type": "OBJECT", "properties": {
        "amount": {"type": "NUMBER"}, "from_currency": {"type": "STRING"},
        "to_currency": {"type": "STRING"}}, "required": ["amount", "from_currency", "to_currency"]}},
    {"name": "stock_quote", "description": "Quick stock quote from Yahoo Finance.",
     "parameters": {"type": "OBJECT", "properties": {"symbol": {"type": "STRING"}}, "required": ["symbol"]}},
    {"name": "github_search_repos", "description": "Search GitHub repositories by query.",
     "parameters": {"type": "OBJECT", "properties": {
        "query": {"type": "STRING"}, "max_results": {"type": "INTEGER"}}, "required": ["query"]}},
    # ---- Random ----
    {"name": "random_number", "description": "Random integer in [low, high].",
     "parameters": {"type": "OBJECT", "properties": {
        "low": {"type": "INTEGER"}, "high": {"type": "INTEGER"}}, "required": []}},
    {"name": "random_choice", "description": "Pick a random element from a list.",
     "parameters": {"type": "OBJECT", "properties": {"options": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["options"]}},
    {"name": "dice_roll", "description": "Roll dice. sides default 6, count default 1.",
     "parameters": {"type": "OBJECT", "properties": {
        "sides": {"type": "INTEGER"}, "count": {"type": "INTEGER"}}, "required": []}},
    {"name": "flip_coin", "description": "Flip N coins.",
     "parameters": {"type": "OBJECT", "properties": {"count": {"type": "INTEGER"}}, "required": []}},
    # ---- OCR ----
    {"name": "ocr_image",
     "description": "Extract text from an image file using built-in Windows OCR (no extra install).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "language": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "ocr_screen",
     "description": "Capture screen (or region) and extract any visible text via OCR.",
     "parameters": {"type": "OBJECT", "properties": {"region": {"type": "OBJECT"}}, "required": []}},
    # ---- Audio ----
    {"name": "say_text", "description": "Speak text aloud through speakers via TTS.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "record_audio", "description": "Record from mic for N seconds to a WAV file.",
     "parameters": {"type": "OBJECT", "properties": {
        "seconds": {"type": "NUMBER"}, "path": {"type": "STRING"}}, "required": []}},
    # ---- Browser ++ ----
    {"name": "browser_wait_for_element", "description": "Wait until a CSS selector exists in the page.",
     "parameters": {"type": "OBJECT", "properties": {
        "selector": {"type": "STRING"}, "timeout": {"type": "NUMBER"}}, "required": ["selector"]}},
    {"name": "browser_get_text", "description": "Get innerText of the first element matching a selector.",
     "parameters": {"type": "OBJECT", "properties": {"selector": {"type": "STRING"}}, "required": ["selector"]}},
    # ---- QoL ----
    {"name": "calculate_text_stats",
     "description": "Word / char / sentence count + reading-time estimate for a text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    # ---- Desktop awareness ----
    {"name": "list_desktop_items",
     "description": "List icons, shortcuts, and files on the user's Windows Desktop (user + public + OneDrive desktops).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "desktop_overview",
     "description": (
         "One-shot snapshot of the desktop state: open windows + their positions, foreground (active) "
         "window, desktop icons/shortcuts, screen resolution, monitor count. Optionally include a "
         "screenshot too. Call this when the user asks 'what's open' / 'what's on my desktop'."
     ),
     "parameters": {"type": "OBJECT",
                    "properties": {"include_screenshot": {"type": "BOOLEAN"}},
                    "required": []}},
    # ---- Folder watching ----
    {"name": "watch_folder_start", "description": "Start watching a folder for new/changed/removed files.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "watch_folder_events", "description": "Read events accumulated since the last call (clear=true to reset).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "clear": {"type": "BOOLEAN"}}, "required": ["path"]}},
    {"name": "watch_folder_stop", "description": "Stop watching a folder.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    # ---- Browser (DOM-driven Chrome/Edge via CDP) ----
    {
        "name": "browser_open",
        "description": "Launch (or attach to) the Ember automation browser and optionally navigate to a URL.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Navigate the current tab to a URL.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING"}, "wait": {"type": "BOOLEAN"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_get_page",
        "description": (
            "Return a structured JSON DOM summary: URL, title, viewport, and a list of every interactive "
            "element (button, link, input, etc.) with text, role, CSS selector, and viewport coords. "
            "Use this BEFORE deciding how to click; almost never click by raw screen coords on a webpage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "visible_only": {"type": "BOOLEAN"},
                "max_items": {"type": "INTEGER"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_click_text",
        "description": "Click an on-page element whose text best matches. mode='left'|'right'|'double'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING"},
                "mode": {"type": "STRING"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_click_selector",
        "description": "Click by CSS selector. mode='left'|'right'|'double'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING"},
                "mode": {"type": "STRING"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_fill",
        "description": "Set a form input's value. Fires input + change events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "selector": {"type": "STRING"},
                "value": {"type": "STRING"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the current page.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {"type": "STRING"},
                "pixels": {"type": "INTEGER"},
            },
            "required": [],
        },
    },
    {
        "name": "browser_back",
        "description": "Browser back button.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_forward",
        "description": "Browser forward button.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_reload",
        "description": "Reload the current page.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_dismiss_cookies",
        "description": "Find and click a cookie consent button. mode='accept' (default) or 'reject'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"mode": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "browser_check_captcha",
        "description": "Detect known CAPTCHA / anti-bot challenges (reCAPTCHA, hCaptcha, Cloudflare, etc.).",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_screenshot",
        "description": "Screenshot via CDP — captures the page viewport without OS chrome.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_evaluate",
        "description": "Run an arbitrary JavaScript expression and return its value.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"expression": {"type": "STRING"}},
            "required": ["expression"],
        },
    },
    {
        "name": "browser_new_tab",
        "description": "Open a new tab.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"url": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "browser_switch_tab",
        "description": "Switch to a tab by id (from browser_list_tabs).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"tab_id": {"type": "STRING"}},
            "required": ["tab_id"],
        },
    },
    {
        "name": "browser_list_tabs",
        "description": "List open tabs in the automation browser.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "browser_close_tab",
        "description": "Close a tab (current one if no id).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"tab_id": {"type": "STRING"}},
            "required": [],
        },
    },
    {
        "name": "browser_current",
        "description": "Current URL and page title.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    # ---- Human-in-the-loop ----
    {
        "name": "pause_for_human",
        "description": (
            "Pause and ask the user to perform a step manually (CAPTCHA, 2FA, login, payment, signature). "
            "The UI shows a 'continue when done' card. NEVER try to solve CAPTCHAs yourself."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "reason": {"type": "STRING"},
                "what_you_need": {"type": "STRING", "description": "what the user should do or provide"},
            },
            "required": ["reason"],
        },
    },
    # ---- Escalation ----
    {
        "name": "ask_claude",
        "description": (
            "Hand off to Claude (smarter model) when you're stuck. The user pastes Claude's reply back. "
            "Provide complete context — Claude can't see the screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "situation": {"type": "STRING"},
                "gemini_summary": {"type": "STRING"},
                "attempted_actions": {"type": "ARRAY", "items": {"type": "STRING"}},
                "screen_observations": {"type": "STRING"},
                "specific_question": {"type": "STRING"},
            },
            "required": ["situation", "gemini_summary", "specific_question"],
        },
    },
    {
        "name": "spawn_agent",
        "description": (
            "Delegate a self-contained sub-task to a fresh sub-agent (like Claude's Task tool). "
            "It runs its own scoped tool loop and reports back a summary, so you stay focused on "
            "the main task. Optionally restrict its tools and run mode."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task": {"type": "STRING", "description": "the complete, self-contained instruction"},
                "mode": {"type": "STRING", "description": "auto | read_only (default auto)"},
                "tools": {"type": "ARRAY", "items": {"type": "STRING"},
                          "description": "optional whitelist of tool names the sub-agent may use"},
            },
            "required": ["task"],
        },
    },
    # ---- Security / antivirus ----
    {"name": "scan_file",
     "description": "Scan a file for malware (local heuristics + platform AV + VirusTotal). "
                    "Returns a verdict: clean | suspicious | malicious, with the reasons.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "deep": {"type": "BOOLEAN"}}, "required": ["path"]}},
    {"name": "run_in_sandbox",
     "description": "Run an unknown/suspicious program in an isolated sandbox (Docker if "
                    "available, else OS-native confinement) to observe it safely. Refuses to "
                    "run files already known to be malicious.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "args": {"type": "ARRAY", "items": {"type": "STRING"}},
        "timeout": {"type": "INTEGER"}}, "required": ["path"]}},
    {"name": "list_quarantine",
     "description": "List files Ember has quarantined as malicious, and when each auto-deletes.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "restore_quarantined",
     "description": "Restore a quarantined file (by id) to its original location. DANGEROUS: "
                    "this re-arms a file flagged as malicious — confirm with the user first.",
     "parameters": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"}, "destination": {"type": "STRING"}}, "required": ["id"]}},
    {"name": "delete_quarantined",
     "description": "Permanently delete a single quarantined file by id.",
     "parameters": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}}, "required": ["id"]}},
    {"name": "security_status",
     "description": "Report Ember's malware-protection status: engines available, settings, "
                    "sandbox type, and quarantine count.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Web protection ----
    {"name": "check_url",
     "description": "Check a URL/website against block & allow lists, malware/phishing "
                    "reputation, and typosquat detection. Verdict: clean | suspicious | blocked.",
     "parameters": {"type": "OBJECT", "properties": {"url": {"type": "STRING"}}, "required": ["url"]}},
    {"name": "add_web_block",
     "description": "Add a host/domain to the website block list (future navigation is refused).",
     "parameters": {"type": "OBJECT", "properties": {"host": {"type": "STRING"}}, "required": ["host"]}},
    {"name": "remove_web_block",
     "description": "Remove a host/domain from the website block list.",
     "parameters": {"type": "OBJECT", "properties": {"host": {"type": "STRING"}}, "required": ["host"]}},
    {"name": "add_web_allow",
     "description": "Add a host/domain to the always-allow list (overrides block & reputation).",
     "parameters": {"type": "OBJECT", "properties": {"host": {"type": "STRING"}}, "required": ["host"]}},
    {"name": "list_web_policy",
     "description": "List the website block list, allow list, and built-in blocked domains.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "web_status",
     "description": "Report web-protection status: enabled, reputation backends, list sizes.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Audit log & capability modes ----
    {"name": "get_audit_log",
     "description": "Return the most recent N entries of Ember's tamper-evident action log.",
     "parameters": {"type": "OBJECT", "properties": {"n": {"type": "INTEGER"}}, "required": []}},
    {"name": "verify_audit_log",
     "description": "Verify the audit log's hash chain is intact (detects tampering).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "get_security_mode",
     "description": "Report Ember's current capability mode: full | restricted | read_only.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "set_agent_mode",
     "description": "Set Ember's capability mode: full (all tools), restricted (no high-risk "
                    "actions), or read_only (only safe read-only tools). DANGEROUS to relax.",
     "parameters": {"type": "OBJECT", "properties": {"mode": {"type": "STRING"}}, "required": ["mode"]}},
    # ---- Plan / Pro ----
    {"name": "get_plan",
     "description": "Show the current plan (free/pro) and which features are unlocked. "
                    "Note: every user currently has the full Pro feature set for free.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "list_pro_features",
     "description": "List the Ember Pro features and benefits.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "set_plan",
     "description": "Set the local plan to 'free' or 'pro' (no payment; everyone is Pro by default).",
     "parameters": {"type": "OBJECT", "properties": {"plan": {"type": "STRING"}}, "required": ["plan"]}},
    # ---- Advanced antivirus ----
    {"name": "scan_directory",
     "description": "Recursively scan a folder for malware; quarantines confirmed threats. "
                    "deep=true also consults VirusTotal (Pro).",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "deep": {"type": "BOOLEAN"}, "max_files": {"type": "INTEGER"}},
        "required": ["path"]}},
    # ---- VPN (bring-your-own WireGuard) ----
    {"name": "vpn_status",
     "description": "Report VPN status: whether a WireGuard tunnel is up and the current public IP.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "list_vpn_locations",
     "description": "List saved VPN locations (your WireGuard configs) and suggested locations.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "add_vpn_location",
     "description": "Add a WireGuard .conf under a location name (from Mullvad/ProtonVPN/your server).",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "config_path": {"type": "STRING"}}, "required": ["name", "config_path"]}},
    {"name": "remove_vpn_location",
     "description": "Remove a saved VPN location.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "vpn_connect",
     "description": "Connect the VPN through a saved location (needs WireGuard + admin rights).",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "vpn_disconnect",
     "description": "Disconnect the VPN (a specific location, or all active tunnels).",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": []}},
    # ---- Multitool utilities ----
    {"name": "disk_usage",
     "description": "Show the biggest files/folders under a path (a quick 'du'). Read-only.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "top": {"type": "INTEGER"}}, "required": []}},
    {"name": "list_open_ports",
     "description": "List listening network ports on this machine and the owning process.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "password_strength",
     "description": "Estimate a password's strength (entropy + rough crack time). Local only.",
     "parameters": {"type": "OBJECT", "properties": {"password": {"type": "STRING"}}, "required": ["password"]}},
    {"name": "system_health",
     "description": "Quick system health: uptime, CPU, memory, and disk usage.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- System cleanup ----
    {"name": "clean_temp",
     "description": "Reclaim space from temp/cache dirs. dry_run=true (default) only reports; "
                    "dry_run=false deletes eligible files. Skips files touched in the last hour.",
     "parameters": {"type": "OBJECT", "properties": {
        "dry_run": {"type": "BOOLEAN"}, "max_age_days": {"type": "INTEGER"}}, "required": []}},
    {"name": "list_startup_items",
     "description": "List apps/agents that launch at login/startup (read-only).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Network toolkit ----
    {"name": "scan_host_ports",
     "description": "TCP port-scan a host (default common ports) for open services. Diagnostic.",
     "parameters": {"type": "OBJECT", "properties": {
        "host": {"type": "STRING"}, "ports": {"type": "STRING"}}, "required": []}},
    {"name": "network_devices",
     "description": "List devices on the local network (from the ARP table).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "wifi_info",
     "description": "Current Wi-Fi network name and signal.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- File & media ----
    {"name": "file_info",
     "description": "Inspect a file: type, size, SHA-256, and image dimensions.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "media_convert",
     "description": "Convert audio/video/images between formats with ffmpeg (src -> dst).",
     "parameters": {"type": "OBJECT", "properties": {
        "src": {"type": "STRING"}, "dst": {"type": "STRING"}}, "required": ["src", "dst"]}},
    # ---- Privacy & security ----
    {"name": "password_pwned_check",
     "description": "Check if a password is in known breaches (Have I Been Pwned, k-anonymity — "
                    "only a partial hash is sent; the password never leaves the machine).",
     "parameters": {"type": "OBJECT", "properties": {"password": {"type": "STRING"}}, "required": ["password"]}},
    {"name": "keychain_store",
     "description": "Store a secret in the OS keychain (instead of a plaintext file).",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "secret": {"type": "STRING"}}, "required": ["name", "secret"]}},
    {"name": "keychain_get",
     "description": "Retrieve a secret previously stored with keychain_store.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "encrypt_file",
     "description": "Encrypt a file with AES-256 (openssl). Output defaults to <path>.enc.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "passphrase": {"type": "STRING"}, "output": {"type": "STRING"}},
        "required": ["path", "passphrase"]}},
    {"name": "decrypt_file",
     "description": "Decrypt a file produced by encrypt_file.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "passphrase": {"type": "STRING"}, "output": {"type": "STRING"}},
        "required": ["path", "passphrase"]}},
    {"name": "ai_detect_text",
     "description": "Estimate whether a block of TEXT is AI-generated (heuristic: sentence "
                    "burstiness, AI 'tell' phrases, contractions). Returns a 0-100 likelihood.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "ai_detect_image",
     "description": "Estimate whether an IMAGE is AI-generated from its metadata / EXIF / "
                    "content-credentials. Returns a 0-100 likelihood + signals.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "password_generate",
     "description": "Generate a strong random password (mixed character classes).",
     "parameters": {"type": "OBJECT", "properties": {
        "length": {"type": "INTEGER"}, "symbols": {"type": "BOOLEAN"}}, "required": []}},
    {"name": "qr_make",
     "description": "Create a QR-code PNG for text / a URL / a Wi-Fi string.",
     "parameters": {"type": "OBJECT", "properties": {
        "text": {"type": "STRING"}, "output": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "strip_metadata",
     "description": "Remove EXIF / embedded metadata (GPS, camera, AI-generator tags) from an "
                    "image before sharing; writes a cleaned copy.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "output": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "read_document",
     "description": "Extract text from a document (PDF, txt, csv, docx, xlsx) so it can be "
                    "summarized or queried — 'chat with your documents'.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "scan_secrets",
     "description": "Flag likely API keys / tokens / PII in text before sharing it (counts only; "
                    "never echoes the values).",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "secure_delete",
     "description": "Securely shred a file: overwrite with random data, then delete. Irreversible.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "passes": {"type": "INTEGER"}}, "required": ["path"]}},
    {"name": "unit_convert",
     "description": "Convert a value between units (length, mass, data size, temperature).",
     "parameters": {"type": "OBJECT", "properties": {
        "value": {"type": "NUMBER"}, "from_unit": {"type": "STRING"}, "to_unit": {"type": "STRING"}},
        "required": ["value", "from_unit", "to_unit"]}},
    {"name": "make_chart",
     "description": "Make a chart/graph (kind: bar, line, pie, scatter) from numbers and save it "
                    "as a PNG that can be shown or attached.",
     "parameters": {"type": "OBJECT", "properties": {
        "kind": {"type": "STRING"},
        "values": {"type": "ARRAY", "items": {"type": "NUMBER"}},
        "labels": {"type": "ARRAY", "items": {"type": "STRING"}},
        "title": {"type": "STRING"}, "xlabel": {"type": "STRING"}, "ylabel": {"type": "STRING"},
        "output": {"type": "STRING"}},
        "required": ["kind", "values"]}},
    {"name": "network_connections",
     "description": "List active established network connections + owning process (security monitor).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "local_ai_status",
     "description": "Check if a local Ollama model server is running and list local models.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "local_ai_ask",
     "description": "Answer a prompt with a LOCAL model via Ollama (offline, no API key, no rate "
                    "limit). Use to offload text work when cloud-limited.",
     "parameters": {"type": "OBJECT", "properties": {
        "prompt": {"type": "STRING"}, "model": {"type": "STRING"}}, "required": ["prompt"]}},
    {"name": "save_macro",
     "description": "Save a reusable named task (macro) to replay later.",
     "parameters": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "task": {"type": "STRING"}}, "required": ["name", "task"]}},
    {"name": "list_macros",
     "description": "List saved task macros.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "get_macro",
     "description": "Get a saved macro's task text.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "run_macro",
     "description": "Fetch a saved macro's task so you can execute it now (carry out the returned task).",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "delete_macro",
     "description": "Delete a saved macro.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "generate_image",
     "description": "Generate an image from a text prompt (saves a PNG; needs a Gemini key with "
                    "image-model access).",
     "parameters": {"type": "OBJECT", "properties": {
        "prompt": {"type": "STRING"}, "output": {"type": "STRING"}}, "required": ["prompt"]}},
    {"name": "describe_image",
     "description": "Vision Q&A: describe an image file or answer a question about it.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "question": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "transcribe_audio",
     "description": "Transcribe an audio file and summarize it.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}, "required": ["path"]}},
    {"name": "security_checkup",
     "description": "Summarize Ember's protection status (antivirus, web protection, sandbox) + a score.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "free_vpn_configs",
     "description": "List providers that give a FREE personal WireGuard VPN config to add to Ember.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "url_quote", "description": "Percent-encode text for a URL.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "url_unquote", "description": "Decode percent-encoded URL text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "json_pretty", "description": "Validate and pretty-print a JSON string.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "case_convert", "description": "Convert text case: upper/lower/title/snake/kebab/camel.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "mode": {"type": "STRING"}}, "required": ["text", "mode"]}},
    {"name": "slugify", "description": "Make a URL-friendly slug from text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "text_stats", "description": "Count characters/words/lines/sentences + reading time.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "word_frequency", "description": "Most common words in text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "top": {"type": "INTEGER"}}, "required": ["text"]}},
    {"name": "extract_emails", "description": "Find email addresses in text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "extract_urls", "description": "Find URLs in text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "regex_find", "description": "Find all regex matches in text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "pattern": {"type": "STRING"}}, "required": ["text", "pattern"]}},
    {"name": "find_replace", "description": "Replace all occurrences of a substring in text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "find": {"type": "STRING"}, "replace": {"type": "STRING"}}, "required": ["text", "find"]}},
    {"name": "sort_lines", "description": "Sort the lines of a block of text (optionally reverse/numeric).",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}, "reverse": {"type": "BOOLEAN"}, "numeric": {"type": "BOOLEAN"}}, "required": ["text"]}},
    {"name": "dedupe_lines", "description": "Remove duplicate lines from text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "reverse_text", "description": "Reverse a string.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "rot13", "description": "ROT13-encode/decode text.",
     "parameters": {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    {"name": "uuid4", "description": "Generate a random UUID.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "random_int", "description": "Random integer between minimum and maximum (inclusive).",
     "parameters": {"type": "OBJECT", "properties": {"minimum": {"type": "INTEGER"}, "maximum": {"type": "INTEGER"}}, "required": []}},
    {"name": "random_pick", "description": "Pick a random item from a comma-separated list.",
     "parameters": {"type": "OBJECT", "properties": {"items": {"type": "STRING"}}, "required": ["items"]}},
    {"name": "lorem_ipsum", "description": "Generate placeholder lorem-ipsum text of N words.",
     "parameters": {"type": "OBJECT", "properties": {"words": {"type": "INTEGER"}}, "required": []}},
    {"name": "int_to_roman", "description": "Convert an integer (1-3999) to a Roman numeral.",
     "parameters": {"type": "OBJECT", "properties": {"number": {"type": "INTEGER"}}, "required": ["number"]}},
    {"name": "roman_to_int", "description": "Convert a Roman numeral to an integer.",
     "parameters": {"type": "OBJECT", "properties": {"roman": {"type": "STRING"}}, "required": ["roman"]}},
    {"name": "hex_to_rgb", "description": "Convert a hex color (#rrggbb) to RGB.",
     "parameters": {"type": "OBJECT", "properties": {"hex_color": {"type": "STRING"}}, "required": ["hex_color"]}},
    {"name": "rgb_to_hex", "description": "Convert RGB values to a hex color.",
     "parameters": {"type": "OBJECT", "properties": {"r": {"type": "INTEGER"}, "g": {"type": "INTEGER"}, "b": {"type": "INTEGER"}}, "required": ["r", "g", "b"]}},
    {"name": "number_to_words", "description": "Spell an integer in English words.",
     "parameters": {"type": "OBJECT", "properties": {"number": {"type": "INTEGER"}}, "required": ["number"]}},
    {"name": "is_prime", "description": "Check whether a number is prime.",
     "parameters": {"type": "OBJECT", "properties": {"number": {"type": "INTEGER"}}, "required": ["number"]}},
    {"name": "days_between", "description": "Days between two dates (YYYY-MM-DD).",
     "parameters": {"type": "OBJECT", "properties": {"date1": {"type": "STRING"}, "date2": {"type": "STRING"}}, "required": ["date1", "date2"]}},
    {"name": "tip_calculator", "description": "Calculate tip + total, optionally split per person.",
     "parameters": {"type": "OBJECT", "properties": {"amount": {"type": "NUMBER"}, "percent": {"type": "NUMBER"}, "people": {"type": "INTEGER"}}, "required": ["amount"]}},
    {"name": "bmi_calculator", "description": "Calculate BMI from weight (kg) and height (cm).",
     "parameters": {"type": "OBJECT", "properties": {"weight_kg": {"type": "NUMBER"}, "height_cm": {"type": "NUMBER"}}, "required": ["weight_kg", "height_cm"]}},
]


TOOL_DISPATCH: dict[str, Callable[..., dict]] = {
    "take_screenshot": tools.take_screenshot,
    "capture_window": tools.capture_window,
    "get_screen_size": tools.get_screen_size,
    "zoom_screenshot": tools.zoom_screenshot,
    "mouse_position": tools.mouse_position,
    "smart_click": screen_vision.smart_click,
    "read_screen_text": screen_vision.read_screen_text,
    "locate_text": screen_vision.locate_text,
    "select_screen_text": screen_vision.select_screen_text,
    "wait_for_text": screen_vision.wait_for_text,
    "assert_text_visible": screen_vision.assert_text_visible,
    "find_ui_elements": tools.find_ui_elements,
    "click_element_by_text": tools.click_element_by_text,
    "right_click_element_by_text": tools.right_click_element_by_text,
    "list_windows": tools.list_windows,
    "focus_window": tools.focus_window,
    "click": tools.click,
    "move_mouse": tools.move_mouse,
    "drag": tools.drag,
    "type_text": tools.type_text,
    "paste_text": tools.paste_text,
    "press_key": tools.press_key,
    "scroll": tools.scroll,
    "wait": tools.wait,
    "wait_for_screen_change": tools.wait_for_screen_change,
    "do_sequence": tools.do_sequence,
    "run_shell": tools.run_powershell,   # OS-aware: zsh on macOS, PowerShell on Windows
    "run_powershell": tools.run_powershell,  # kept for back-compat (saved automations)
    "run_cmd": tools.run_cmd,
    "read_file": tools.read_file,
    "write_file": tools.write_file,
    "list_directory": tools.list_directory,
    "search_files": tools.search_files,
    "open_url": tools.open_url,
    "open_app": tools.open_app,
    "open_path": tools.open_path,
    "get_event_logs": tools.get_event_logs,
    "get_reliability_events": tools.get_reliability_events,
    "get_minidumps": tools.get_minidumps,
    "get_system_info": tools.get_system_info,
    "get_installed_drivers": tools.get_installed_drivers,
    "get_running_processes": tools.get_running_processes,
    "get_performance": tools.get_performance,
    "get_windows_updates": tools.get_windows_updates,
    "quick_fix": tools.quick_fix,
    "list_quick_fixes": tools.list_quick_fixes,
    "remember": memory.remember,
    "recall": memory.recall,
    "forget": memory.forget,
    "browser_open": tools.browser_open,
    "browser_navigate": tools.browser_navigate,
    "browser_get_page": tools.browser_get_page,
    "browser_click_text": tools.browser_click_text,
    "browser_click_selector": tools.browser_click_selector,
    "browser_fill": tools.browser_fill,
    "browser_scroll": tools.browser_scroll,
    "browser_back": tools.browser_back,
    "browser_forward": tools.browser_forward,
    "browser_reload": tools.browser_reload,
    "browser_dismiss_cookies": tools.browser_dismiss_cookies,
    "browser_check_captcha": tools.browser_check_captcha,
    "browser_screenshot": tools.browser_screenshot,
    "browser_evaluate": tools.browser_evaluate,
    "browser_new_tab": tools.browser_new_tab,
    "browser_switch_tab": tools.browser_switch_tab,
    "browser_list_tabs": tools.browser_list_tabs,
    "browser_close_tab": tools.browser_close_tab,
    "browser_current": tools.browser_current,
    "organize_folder": file_ops.organize_folder,
    "find_duplicate_files": file_ops.find_duplicate_files,
    "find_large_files": file_ops.find_large_files,
    "bulk_rename": file_ops.bulk_rename,
    "move_matching_files": file_ops.move_matching_files,
    "get_folder_size": file_ops.get_folder_size,
    "unzip_archive": file_ops.unzip_archive,
    "zip_files": file_ops.zip_files,
    "folder_tree": file_ops.folder_tree,
    "create_cluttered_demo_folder": file_ops.create_cluttered_demo_folder,
    "trash_file": file_ops.trash_file,
    # web / network
    "http_get": more_tools.http_get,
    "http_post": more_tools.http_post,
    "download_file": more_tools.download_file,
    # security / antivirus
    "scan_file": antivirus.scan_file,
    "run_in_sandbox": antivirus.run_in_sandbox,
    "list_quarantine": antivirus.list_quarantine,
    "restore_quarantined": antivirus.restore_quarantined,
    "delete_quarantined": antivirus.delete_quarantined,
    "security_status": antivirus.security_status,
    # web protection
    "check_url": web_policy.check_url,
    "add_web_block": web_policy.add_block,
    "remove_web_block": web_policy.remove_block,
    "add_web_allow": web_policy.add_allow,
    "list_web_policy": web_policy.list_web_policy,
    "web_status": web_policy.web_status,
    # audit log & capability modes
    "get_audit_log": audit.tail,
    "verify_audit_log": audit.verify,
    "get_security_mode": safety.get_mode,
    "set_agent_mode": safety.set_mode,
    # plan / Pro
    "get_plan": plan.get_plan,
    "list_pro_features": plan.list_pro_features,
    "set_plan": plan.set_plan,
    # advanced antivirus
    "scan_directory": antivirus.scan_directory,
    # vpn
    "vpn_status": vpn.status,
    "list_vpn_locations": vpn.list_locations,
    "add_vpn_location": vpn.add_location,
    "remove_vpn_location": vpn.remove_location,
    "vpn_connect": vpn.connect,
    "vpn_disconnect": vpn.disconnect,
    # multitool utilities
    "disk_usage": utilities.disk_usage,
    "list_open_ports": utilities.list_open_ports,
    "password_strength": utilities.password_strength,
    "system_health": utilities.system_health,
    # system cleanup
    "clean_temp": cleanup.clean_temp,
    "list_startup_items": cleanup.list_startup_items,
    # network toolkit
    "scan_host_ports": nettools.scan_host_ports,
    "network_devices": nettools.network_devices,
    "wifi_info": nettools.wifi_info,
    # file & media
    "file_info": mediatools.file_info,
    "media_convert": mediatools.media_convert,
    # privacy & security
    "password_pwned_check": privacy.password_pwned_check,
    "keychain_store": privacy.keychain_store,
    "keychain_get": privacy.keychain_get,
    "encrypt_file": privacy.encrypt_file,
    "decrypt_file": privacy.decrypt_file,
    "ai_detect_text": ai_detect.detect_text,
    "ai_detect_image": ai_detect.detect_image,
    "password_generate": quick_tools.password_generate,
    "qr_make": quick_tools.qr_make,
    "strip_metadata": quick_tools.strip_metadata,
    "read_document": power_tools.read_document,
    "scan_secrets": power_tools.scan_secrets,
    "secure_delete": power_tools.secure_delete,
    "unit_convert": power_tools.unit_convert,
    "make_chart": chart_tools.make_chart,
    "network_connections": nettools.network_connections,
    "local_ai_status": local_ai.local_ai_status,
    "local_ai_ask": local_ai.local_ai_ask,
    "save_macro": macros.save_macro,
    "list_macros": macros.list_macros,
    "get_macro": macros.get_macro,
    "run_macro": macros.run_macro,
    "delete_macro": macros.delete_macro,
    "generate_image": creative.generate_image,
    "describe_image": creative.describe_image,
    "transcribe_audio": creative.transcribe_audio,
    "security_checkup": security_extras.security_checkup,
    "free_vpn_configs": vpn.free_providers,
    "url_quote": text_tools.url_quote,
    "url_unquote": text_tools.url_unquote,
    "json_pretty": text_tools.json_pretty,
    "case_convert": text_tools.case_convert,
    "slugify": text_tools.slugify,
    "text_stats": text_tools.text_stats,
    "word_frequency": text_tools.word_frequency,
    "extract_emails": text_tools.extract_emails,
    "extract_urls": text_tools.extract_urls,
    "regex_find": text_tools.regex_find,
    "find_replace": text_tools.find_replace,
    "sort_lines": text_tools.sort_lines,
    "dedupe_lines": text_tools.dedupe_lines,
    "reverse_text": text_tools.reverse_text,
    "rot13": text_tools.rot13,
    "uuid4": text_tools.uuid4,
    "random_int": text_tools.random_int,
    "random_pick": text_tools.random_pick,
    "lorem_ipsum": text_tools.lorem_ipsum,
    "int_to_roman": text_tools.int_to_roman,
    "roman_to_int": text_tools.roman_to_int,
    "hex_to_rgb": text_tools.hex_to_rgb,
    "rgb_to_hex": text_tools.rgb_to_hex,
    "number_to_words": text_tools.number_to_words,
    "is_prime": text_tools.is_prime,
    "days_between": text_tools.days_between,
    "tip_calculator": text_tools.tip_calculator,
    "bmi_calculator": text_tools.bmi_calculator,
    "public_ip": more_tools.public_ip,
    "dns_lookup": more_tools.dns_lookup,
    "network_ping": more_tools.network_ping,
    "web_search": more_tools.web_search,
    "wikipedia_summary": more_tools.wikipedia_summary,
    "weather_lookup": more_tools.weather_lookup,
    "translate_text": more_tools.translate_text,
    # email
    "send_email": more_tools.send_email,
    # documents
    "pdf_extract_text": more_tools.pdf_extract_text,
    "excel_read": more_tools.excel_read,
    "excel_write": more_tools.excel_write,
    "json_query": more_tools.json_query,
    "csv_read": more_tools.csv_read,
    # math / utility
    "calculator": more_tools.calculator,
    "generate_password": more_tools.generate_password,
    "generate_uuid": more_tools.generate_uuid,
    "hash_text": more_tools.hash_text,
    "hash_file": more_tools.hash_file,
    "base64_encode": more_tools.base64_encode,
    "base64_decode": more_tools.base64_decode,
    "url_encode": more_tools.url_encode,
    "url_decode": more_tools.url_decode,
    "now": more_tools.now,
    # system / hardware
    "power_action": more_tools.power_action,
    "get_battery": more_tools.get_battery,
    "get_volume": more_tools.get_volume,
    "set_volume": more_tools.set_volume,
    "toggle_mute": more_tools.toggle_mute,
    "env_get": more_tools.env_get,
    "env_list": more_tools.env_list,
    # image
    "image_resize": more_tools.image_resize,
    "image_crop": more_tools.image_crop,
    "image_convert": more_tools.image_convert,
    "color_at": more_tools.color_at,
    "qr_generate": more_tools.qr_generate,
    # clipboard
    "clipboard_get": more_tools.clipboard_get,
    "clipboard_set": more_tools.clipboard_set,
    "clipboard_history_get": more_tools.clipboard_history_get,
    "clipboard_history_snapshot": more_tools.clipboard_history_snapshot,
    # git
    "git_status": more_tools.git_status,
    "git_log": more_tools.git_log,
    "git_diff": more_tools.git_diff,
    # calendar
    "create_calendar_event": more_tools.create_calendar_event,
    # persistent scheduling
    "schedule_shell_command": scheduled_tasks.schedule_shell_command,
    "list_scheduled_tasks": scheduled_tasks.list_scheduled_tasks,
    "cancel_scheduled_task": scheduled_tasks.cancel_scheduled_task,
    # ---- extra_tools ----
    # window management
    "snap_window": extra_tools.snap_window,
    "move_window": extra_tools.move_window,
    "minimize_all_other_windows": extra_tools.minimize_all_other_windows,
    "list_monitors": extra_tools.list_monitors,
    "show_desktop": extra_tools.show_desktop,
    "switch_window": extra_tools.switch_window,
    # media keys
    "media_keys": extra_tools.media_keys,
    # notifications
    "show_notification": extra_tools.show_notification,
    # process control
    "kill_process": extra_tools.kill_process,
    "service_action": extra_tools.service_action,
    # file content search
    "grep_files": extra_tools.grep_files,
    "diff_files": extra_tools.diff_files,
    "count_lines": extra_tools.count_lines,
    # web extras
    "speed_test": extra_tools.speed_test,
    "define_word": extra_tools.define_word,
    "currency_convert": extra_tools.currency_convert,
    "stock_quote": extra_tools.stock_quote,
    "github_search_repos": extra_tools.github_search_repos,
    # random
    "random_number": extra_tools.random_number,
    "random_choice": extra_tools.random_choice,
    "dice_roll": extra_tools.dice_roll,
    "flip_coin": extra_tools.flip_coin,
    # OCR
    "ocr_image": extra_tools.ocr_image,
    "ocr_screen": extra_tools.ocr_screen,
    # audio
    "say_text": extra_tools.say_text,
    "record_audio": extra_tools.record_audio,
    # browser additions
    "browser_wait_for_element": extra_tools.browser_wait_for_element,
    "browser_get_text": extra_tools.browser_get_text,
    # QoL
    "calculate_text_stats": extra_tools.calculate_text_stats,
    # folder watching
    "watch_folder_start": extra_tools.watch_folder_start,
    "watch_folder_events": extra_tools.watch_folder_events,
    "watch_folder_stop": extra_tools.watch_folder_stop,
    # desktop awareness
    "list_desktop_items": extra_tools.list_desktop_items,
    "desktop_overview": extra_tools.desktop_overview,
    # remote phone control (Ember Link)
    "start_remote_control": remote_server.start,
    "stop_remote_control": remote_server.stop,
    "remote_control_status": remote_server.status,
}


# ---- Roadmap backlog feature tools ----------------------------------------------
# Each module exports its own TOOL_DECLARATIONS / TOOL_DISPATCH (+ READONLY/INTERACTION
# sets used by safety.py). Merge them here so the central tables stay the single source
# of truth the agent + lean-mode filter read from.
for _feat in (key_vault, usage_tracker, download_guard, fileless_guard, security_center,
              agent_profiles, agent_scheduler, integrations,
              workflow_recorder, productivity_tools, plugin_system, custom_tools):
    for _decl in _feat.TOOL_DECLARATIONS:
        if _decl["name"] not in TOOL_DISPATCH:
            TOOL_DECLARATIONS.append(_decl)
    TOOL_DISPATCH.update(_feat.TOOL_DISPATCH)

# Tell custom_tools the full live tool registry so create_custom_tool can reject a recipe
# step that names a tool Ember doesn't actually have. (run_custom_tool is host-executed, so
# it isn't in TOOL_DISPATCH — add it explicitly.)
custom_tools.KNOWN_TOOLS = set(TOOL_DISPATCH) | {
    "run_custom_tool", "ask_claude", "pause_for_human", "spawn_agent", "agent_run"}

# Dynamically loaded user plugins (drop a .py into the plugins/ folder -> auto-registers).
# A broken plugin is skipped by load_plugins(); we never let it break startup.
try:
    _plug = plugin_system.load_plugins()
    for _decl in _plug.get("declarations", []):
        if _decl["name"] not in TOOL_DISPATCH:
            TOOL_DECLARATIONS.append(_decl)
            TOOL_DISPATCH[_decl["name"]] = _plug["dispatch"][_decl["name"]]
    # Read-only plugin tools get the safe classification; the rest stay medium-risk.
    safety.SAFE_READONLY |= set(_plug.get("read_only_names", set()))
except Exception:
    pass


# Read-only tools with no side effects (no input injection, no mouse, no writes).
# When the model emits a batch of ONLY these, Ember runs them concurrently instead of
# one-at-a-time - a meaningful latency win on multi-read turns (e.g. "diagnose my PC").
PARALLEL_SAFE_TOOLS = frozenset({
    "take_screenshot", "capture_window", "get_screen_size", "zoom_screenshot",
    "mouse_position", "read_screen_text", "locate_text", "assert_text_visible", "find_ui_elements",
    "list_windows", "read_file", "list_directory", "search_files", "folder_tree",
    "get_folder_size", "find_large_files", "find_duplicate_files", "grep_files",
    "diff_files", "count_lines", "json_query", "csv_read", "pdf_extract_text",
    "excel_read", "ocr_image", "ocr_screen", "get_event_logs", "get_reliability_events",
    "get_minidumps", "get_system_info", "get_installed_drivers", "get_running_processes",
    "get_performance", "get_windows_updates", "list_quick_fixes", "recall",
    "http_get", "public_ip", "dns_lookup", "network_ping", "web_search",
    "wikipedia_summary", "weather_lookup", "define_word", "currency_convert",
    "stock_quote", "github_search_repos", "calculator", "now", "hash_text", "hash_file",
    "base64_encode", "base64_decode", "url_encode", "url_decode", "color_at",
    "clipboard_get", "clipboard_history_get", "get_battery", "get_volume",
    "env_get", "env_list", "list_monitors", "list_desktop_items", "desktop_overview",
    "browser_get_page", "browser_get_text", "browser_list_tabs", "browser_current",
    "git_status", "git_log", "git_diff", "speed_test", "calculate_text_stats",
    "list_scheduled_tasks",
    # roadmap backlog read-only tools
    "vault_status", "vault_get_key", "vault_list_keys", "usage_summary",
    "download_guard_status", "download_guard_events", "list_workflows",
    "snippet_list", "snippet_get", "snippet_expand", "email_breach_check",
    "list_plugins",
    # real-time fileless / behavioral protection (read-only)
    "scan_processes", "scan_command",
    "fileless_guard_status", "fileless_guard_events",
    # always-on Security Center (read-only)
    "security_center_status", "security_center_events",
    "scan_network", "scan_persistence",
    # agent run modes / profiles (read-only)
    "list_run_modes", "agent_list", "agent_get",
    "scheduler_status", "scheduler_events", "integration_list",
    # AI-authored custom tools (read-only management)
    "list_custom_tools", "get_custom_tool", "export_custom_tool",
})

# Declared-type map for argument coercion (built AFTER all module tools merged in).
_PARAM_TYPES = tool_args.build_param_types(TOOL_DECLARATIONS)

# Tools a scoped sub-agent may ALWAYS call regardless of its tool whitelist
# (control/coordination tools, never the actuators).
_SCOPE_ALWAYS_ALLOW = {"pause_for_human", "ask_claude", "list_run_modes",
                       "set_run_mode", "take_screenshot", "get_screen_size"}


def _make_safety_settings():
    cats = [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    ]
    settings = []
    for c in cats:
        try:
            settings.append(types.SafetySetting(category=c, threshold="BLOCK_NONE"))
        except Exception:
            pass
    return settings


@dataclass
class AgentEvent:
    kind: str
    payload: Any = None


@dataclass
class PendingConfirmation:
    tool_name: str
    args: dict
    reason: str
    response: queue.Queue = field(default_factory=queue.Queue)


@dataclass
class PendingClaudeResponse:
    handoff_prompt: str
    response: queue.Queue = field(default_factory=queue.Queue)


@dataclass
class PendingHumanPause:
    reason: str
    what_you_need: str
    response: queue.Queue = field(default_factory=queue.Queue)


class _CustomFC:
    """Minimal function-call shim (.name / .args) so a custom tool's recipe steps can be
    run through the same _execute_fc path as a model-issued tool call."""
    __slots__ = ("name", "args")

    def __init__(self, name: str, args: dict):
        self.name = name
        self.args = args or {}


class Agent:
    # Conservative fallback chain - only confirmed-working free-tier IDs.
    # If any returns 404 it's auto-blacklisted for the session.
    DEFAULT_FALLBACKS = [
        "gemini-2.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]

    def __init__(self, api_key: str, model_name: str = "gemini-3.1-flash-lite",
                 secondary_api_key: str | None = None,
                 backup_api_keys: list[str] | None = None,
                 dual_api_failover: bool = True,
                 anthropic_key: str | None = None,
                 anthropic_model: str = "claude-opus-4-8",
                 fallback_models: list[str] | None = None,
                 auto_screenshot: bool = True,
                 request_timeout_seconds: int = 15,
                 lean_tools: bool = False):
        self.lean_tools = bool(lean_tools)
        # Strip ALL whitespace from keys (incl. accidental newlines from a bad paste) so a key
        # can never become an illegal HTTP header value (LocalProtocolError).
        def _clean(k):
            return "".join((k or "").split())
        self.api_key = _clean(api_key)
        self.secondary_api_key = _clean(secondary_api_key) or None
        self.dual_api_failover = bool(dual_api_failover)
        # Build the ordered failover chain: primary first, then each backup key, de-duplicated
        # and stripped of blanks. Supports a primary + up to several Gemini backup keys, so when
        # one key is rate-limited Ember rotates to the next instead of stalling.
        self.api_keys = [self.api_key]
        if self.dual_api_failover:
            candidates = []
            if self.secondary_api_key:
                candidates.append(self.secondary_api_key)
            for k in (backup_api_keys or []):
                candidates.append(_clean(k))
            for k in candidates:
                if k and k not in self.api_keys:
                    self.api_keys.append(k)
        self._api_key_index = 0
        self.model_name = model_name
        self.active_model = model_name
        self.fallback_models = fallback_models if fallback_models is not None else list(self.DEFAULT_FALLBACKS)
        self.anthropic_key = "".join((anthropic_key or "").split()) or None
        self.anthropic_model = anthropic_model
        self.auto_screenshot = auto_screenshot
        # Gemini enforces a 10s minimum deadline. Cap upper limit so the agent can't
        # accidentally wait minutes per call.
        self.request_timeout_seconds = max(10, min(60, int(request_timeout_seconds or 15)))
        # User-configurable request deadline. Gemini enforces 10s minimum.
        self._client = self._make_client(self.api_keys[self._api_key_index])
        # Per-session latency tracking so we auto-prefer fast models on later turns.
        self._model_latencies: dict[str, float] = {}
        self._call_times: list[float] = []
        self._chat = None
        self._event_subs: list[Callable[[AgentEvent], None]] = []
        self._stop_flag = threading.Event()
        # Run mode (auto/plan/chat/read_only) + optional live tool scope for sub-agents.
        try:
            self.run_mode = agent_profiles.get_run_mode()
        except Exception:
            self.run_mode = "auto"
        self._active_tool_scope = None     # None = all tools; else a set of allowed names
        self._spawn_depth = 0              # guards sub-agent recursion
        # Serialize turns: the chat object isn't thread-safe, so overlapping turns must queue.
        self._turn_lock = threading.Lock()
        self._turn_queue: list[str] = []
        self._busy = False
        # Thinking is on by default (better tool selection), but Gemini's thinking models
        # attach a thought_signature to every function call that must round-trip back. If a
        # turn ever 400s for a missing thought_signature (e.g. history rebuilt across a key/
        # model switch dropped it), we auto-disable thinking for the session to stay reliable.
        self._thinking_enabled = True
        self._init_chat()

    def _make_client(self, api_key: str):
        try:
            return genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=self.request_timeout_seconds * 1000),
            )
        except Exception:
            return genai.Client(api_key=api_key)

    def _capture_history(self):
        """Return the current chat's history so it can be carried into a new chat when we
        switch API key or model — so switching NEVER wipes the conversation. The
        comprehensive history (default) includes a mid-turn function_call, which is what
        lets an in-flight tool turn survive the switch. Returns None if unavailable."""
        ch = getattr(self, "_chat", None)
        if ch is None:
            return None
        try:
            return list(ch.get_history())
        except TypeError:
            try:
                return list(ch.get_history(curated=False))
            except Exception:
                return None
        except Exception:
            return None

    def _switch_api_key(self, index: int):
        # Carry the conversation across the key switch (history preserved).
        hist = self._capture_history()
        self._api_key_index = index
        self._client = self._make_client(self.api_keys[self._api_key_index])
        self._init_chat(model=self.active_model, history=hist)

    def _api_label(self, index: int | None = None) -> str:
        i = self._api_key_index if index is None else index
        return "primary API" if i == 0 else f"backup API {i}"

    def _init_chat(self, model: str | None = None, history=None):
        if model:
            self.active_model = model
        decls = TOOL_DECLARATIONS
        if getattr(self, "lean_tools", False):
            # Lean mode: drop the niche utility tools to shrink the per-call tool list (faster
            # responses, fewer tokens). Core computer-control / browser / file / security tools stay.
            niche = {"text_tools", "chart_tools", "quick_tools", "power_tools", "ai_detect",
                     "local_ai", "macros", "creative", "security_extras", "cleanup",
                     "nettools", "mediatools", "privacy", "productivity_tools"}
            drop = {name for name, fn in TOOL_DISPATCH.items()
                    if getattr(fn, "__module__", "") in niche}
            decls = [td for td in TOOL_DECLARATIONS if td["name"] not in drop]
        tool_decls = [types.FunctionDeclaration(**td) for td in decls]
        tool_obj = types.Tool(function_declarations=tool_decls)
        # Speed-tuned generation:
        #  - low temperature for faster, more deterministic tool selection
        #  - cap output tokens (most responses are short text + tool calls)
        try:
            _sys_prompt = build_system_prompt()
        except Exception:
            # Never let a transient error reading memory/system context abort agent init.
            _sys_prompt = BASE_SYSTEM_PROMPT
        config_kwargs = dict(
            system_instruction=_sys_prompt,
            tools=[tool_obj],
            safety_settings=_make_safety_settings(),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.15,
            top_p=0.9,
            max_output_tokens=8000,
        )
        # Let the model THINK before answering, so it reads the screen right the first time
        # and retries less. Fewer round-trips = fewer API requests (the free tier counts
        # requests/day, not tokens). Dynamic budget; guarded for models/SDKs without it.
        if getattr(self, "_thinking_enabled", True):
            try:
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)
            except Exception:
                pass
        try:
            config = types.GenerateContentConfig(**config_kwargs)
        except TypeError:
            # Older SDK without some fields - drop the optional ones.
            for k in ("temperature", "top_p", "max_output_tokens", "thinking_config"):
                config_kwargs.pop(k, None)
            config = types.GenerateContentConfig(**config_kwargs)
        # Re-create the chat, carrying prior history when given (preserves the
        # conversation across an API-key or model switch). If the SDK doesn't accept a
        # history kwarg, or the history can't be replayed, fall back to a clean chat
        # rather than crashing.
        if history:
            try:
                self._chat = self._client.chats.create(
                    model=self.active_model, config=config, history=history)
                return
            except Exception:
                pass
        self._chat = self._client.chats.create(model=self.active_model, config=config)

    def reset(self):
        self._init_chat()
        self._model_latencies = {}
        if hasattr(self, "_bad_models"):
            self._bad_models = set()

    def stop(self):
        self._stop_flag.set()
        # Drop any queued (not-yet-started) turns so a stop really stops everything.
        try:
            with self._turn_lock:
                self._turn_queue.clear()
        except Exception:
            pass

    def subscribe(self, fn: Callable[[AgentEvent], None]):
        self._event_subs.append(fn)

    def _emit(self, event: AgentEvent):
        for fn in self._event_subs:
            try:
                fn(event)
            except Exception:
                traceback.print_exc()

    def send_user_message(self, text: str):
        """Queue a user turn. Turns run ONE AT A TIME — the Gemini chat object is not
        thread-safe, so firing overlapping turns (e.g. hitting Enter again while a slow or
        rate-limited turn is still running) corrupted history and made Ember hang / stop
        replying. Extra messages now queue and run in order instead of racing."""
        start_worker = False
        with self._turn_lock:
            self._turn_queue.append(text)
            if not self._busy:
                self._busy = True
                start_worker = True
        if start_worker:
            threading.Thread(target=self._turn_worker, daemon=True).start()

    def _turn_worker(self):
        while True:
            with self._turn_lock:
                if not self._turn_queue:
                    self._busy = False
                    return
                text = self._turn_queue.pop(0)
            try:
                self._run_turn(text)   # emits its own "done" in a finally
            except Exception as e:
                try:
                    self._emit(AgentEvent("error", f"{type(e).__name__}: {str(e)[:400]}"))
                    self._emit(AgentEvent("done"))
                except Exception:
                    pass

    def _image_part(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> types.Part:
        return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    def _pace(self):
        """Keep under the free tier's ~15 model requests/minute. If 14 calls happened in the
        last 60s, wait for the oldest to age out before sending — this PREVENTS 429 rate-limit
        errors rather than just reacting to them."""
        now = time.time()
        self._call_times = [c for c in self._call_times if now - c < 60.0]
        if len(self._call_times) >= 12 and not self._stop_flag.is_set():
            wait = max(0.0, 60.0 - (now - self._call_times[0]) + 0.25)
            if wait > 0:
                self._emit(AgentEvent("message",
                    f"[pacing the ~15/min free-tier limit — waiting {wait:.0f}s to avoid a rate-limit error]"))
                deadline = now + min(wait, 62.0)
                while time.time() < deadline and not self._stop_flag.is_set():
                    time.sleep(0.3)
                now = time.time()
                self._call_times = [c for c in self._call_times if now - c < 60.0]
        self._call_times.append(time.time())

    def _send_streaming(self, parts):
        """Stream the response when SAFE (i.e. when parts don't contain function_response).
        Streaming during function-response phase corrupted chat history and caused 400 errors,
        so we only stream the initial user-message turn (huge UX win: first token in ~300ms)."""
        has_fn_resp = False
        try:
            for p in parts:
                if hasattr(p, "function_response") and getattr(p, "function_response", None) is not None:
                    has_fn_resp = True
                    break
        except Exception:
            pass
        if has_fn_resp:
            return self._chat.send_message(parts)

        try:
            stream = self._chat.send_message_stream(parts)
        except AttributeError:
            return self._chat.send_message(parts)
        except Exception:
            raise

        accumulated_parts = []
        text_buf = []
        function_calls = []
        usage = None
        finish_reason = None

        try:
            for chunk in stream:
                cands = getattr(chunk, "candidates", None) or []
                for cand in cands:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in (content.parts or []):
                        fc = getattr(part, "function_call", None)
                        if fc and getattr(fc, "name", None):
                            function_calls.append(fc)
                            accumulated_parts.append(part)
                        text = getattr(part, "text", None)
                        if text:
                            text_buf.append(text)
                            accumulated_parts.append(part)
                            self._emit(AgentEvent("stream_chunk", text))
                    finish_reason = getattr(cand, "finish_reason", finish_reason)
                usage = getattr(chunk, "usage_metadata", usage)
        except Exception:
            # Stream blew up - fall back to a clean non-streaming call.
            self._emit(AgentEvent("stream_end", None))
            return self._chat.send_message(parts)

        if text_buf:
            self._emit(AgentEvent("stream_end", None))

        # Build a response-shaped object the rest of the agent can read.
        class _StreamCandidate:
            def __init__(self, parts_, finish):
                self.content = type("C", (), {"parts": parts_})()
                self.finish_reason = finish

        class _StreamResponse:
            def __init__(self, parts_, finish, usage_):
                self.candidates = [_StreamCandidate(parts_, finish)]
                self.usage_metadata = usage_

        return _StreamResponse(accumulated_parts, finish_reason, usage)

    def _is_retryable(self, err: Exception) -> tuple[bool, int | None]:
        s = str(err)
        # 404 (model not found) is "retryable" only via model fallback - blacklist + skip.
        if "404" in s and ("NOT_FOUND" in s or "not found" in s):
            return True, 404
        # 400 about function_response ordering = chat history is corrupted. Reset + retry.
        if "function response" in s.lower() and "function call" in s.lower():
            return True, 400
        # 400 about a missing thought_signature (thinking models + tools). Recoverable:
        # disable thinking for the session, reset, and retry.
        if "thought_signature" in s.lower() or "thought signature" in s.lower():
            return True, 400
        m = re.search(r"\b(429|500|502|503|504)\b", s)
        if m:
            return True, int(m.group(1))
        if any(tok in s for tok in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED",
                                     "INTERNAL", "ServerError", "Timeout")):
            return True, None
        return False, None

    def _is_limit_error(self, err: Exception, status: int | None) -> bool:
        if status == 429:
            return True
        s = str(err).lower()
        return any(tok in s for tok in (
            "resource_exhausted", "quota", "rate limit", "rate-limit",
            "too many requests", "requests per", "exceeded your current quota",
        ))

    def _send_with_retry(self, parts):
        delays = [1, 3]
        tried_models = [self.active_model]
        if not hasattr(self, "_bad_models"):
            self._bad_models: set[str] = set()

        def _try_fallbacks(reason: str):
            """Iterate the fallback chain, skipping blacklisted + already-tried models.
            Chat history is carried into each switched-to model, so context (including a
            mid-turn tool call) survives the switch — nothing is wiped."""
            hist = self._capture_history()
            last_err = None
            for fb in self.fallback_models:
                if not fb or fb in tried_models or fb in self._bad_models:
                    continue
                self._emit(AgentEvent("message",
                    f"[{self.active_model} {reason} - switching to {fb} (history kept)]"))
                try:
                    self._init_chat(model=fb, history=hist)
                    tried_models.append(fb)
                    return self._chat.send_message(parts)
                except Exception as fe:
                    last_err = fe
                    retryable_fe, status_fe = self._is_retryable(fe)
                    if status_fe == 404:
                        self._bad_models.add(fb)
                        continue
                    if status_fe == 429:
                        continue
                    if not retryable_fe:
                        raise
            if last_err:
                raise last_err
            raise RuntimeError(f"all fallback models exhausted (reason: {reason})")

        def _try_api_key_fallback(reason: str):
            """Retry the SAME model with another Gemini API key before changing models.
            History is carried across the key switch, so this works mid-turn too."""
            if len(self.api_keys) < 2:
                return None
            current_key = self._api_key_index
            last_err = None
            for i, _key in enumerate(self.api_keys):
                if i == current_key:
                    continue
                self._emit(AgentEvent("message",
                    f"[{self.active_model} {reason} on {self._api_label(current_key)} - "
                    f"switching to {self._api_label(i)} (history kept)]"))
                try:
                    self._switch_api_key(i)
                    return self._chat.send_message(parts)
                except Exception as ke:
                    last_err = ke
                    retryable_ke, status_ke = self._is_retryable(ke)
                    if self._is_limit_error(ke, status_ke):
                        continue
                    if not retryable_ke:
                        raise
            if last_err:
                raise last_err
            return None

        # First attempt: NON-streaming send_message.
        # Why not streaming? send_message_stream does not reliably record a function_call-only
        # model turn into the chat's curated history. The next function_response then has no
        # matching function_call -> Gemini 400 "function response without function call", which
        # broke EVERY tool-using task. Non-streaming send_message records history correctly. The
        # user still sees the model's text immediately via the "message" event in _process_response.
        if self._stop_flag.is_set():
            raise RuntimeError("stopped by user")
        self._pace()
        t0 = time.time()
        try:
            resp = self._chat.send_message(parts)
            elapsed = time.time() - t0
            prev = self._model_latencies.get(self.active_model, elapsed)
            self._model_latencies[self.active_model] = 0.3 * elapsed + 0.7 * prev
            if elapsed > 8 and prev > 8:
                self._emit(AgentEvent("message",
                    f"[note: {self.active_model} averaging {self._model_latencies[self.active_model]:.1f}s "
                    "per call - consider switching primary model in Settings]"))
            return resp
        except Exception as e:
            retryable, status = self._is_retryable(e)
            if not retryable:
                raise
            # Any retryable failure on the primary -> skip same-model retries, fall back immediately.
            # The same-model "retry with delay" pattern wastes 25s+ per attempt against a hanging model.
            if status == 404:
                self._bad_models.add(self.active_model)
                reason = "does not exist (404)"
            elif status == 400:
                low = str(e).lower()
                if "thought_signature" in low or "thought signature" in low:
                    # Thinking model + tools: a function call lost its required thought_signature
                    # (often when history was rebuilt across a key/model switch). Turn thinking
                    # OFF for the rest of the session so it can't recur, reset, and ask to resend.
                    self._thinking_enabled = False
                    try:
                        self._init_chat()
                    except Exception:
                        pass
                    raise RuntimeError(
                        "That turn hit a Gemini thinking/tool-signature error and was "
                        "auto-recovered (thinking is now off for this session for stability) — "
                        "just send your request again."
                    )
                # Chat history desync mid-turn: the function_call context can't be replayed on a
                # fresh chat, so we can't finish THIS turn. But auto-reset the history now so the
                # user's NEXT message just works - no manual reset button needed.
                try:
                    self._init_chat()
                except Exception:
                    pass
                raise RuntimeError(
                    "That turn desynced and was auto-recovered - just send your request again "
                    "(history has been cleared for you)."
                )
            elif status == 429:
                reason = "rate-limited (429)"
            elif status in (500, 502, 503, 504):
                reason = f"server error ({status})"
            else:
                reason = "timed out / no response"
            if self._is_limit_error(e, status):
                # Rate-limited. Recover automatically, in order, ALWAYS keeping chat history:
                #   1) instantly retry the SAME model on a different API key (if configured),
                #   2) else wait out the per-minute limit and retry the same model+chat,
                #   3) else switch to a fallback MODEL (history carried over).
                api_resp = _try_api_key_fallback(reason)
                if api_resp is not None:
                    return api_resp
                for backoff in (20, 40):
                    if self._stop_flag.is_set():
                        raise RuntimeError("stopped by user")
                    self._emit(AgentEvent("message",
                        f"[rate-limited — waiting {backoff}s, then retrying {self.active_model}; "
                        "your progress is preserved]"))
                    waited = 0.0
                    while waited < backoff and not self._stop_flag.is_set():
                        time.sleep(0.5)
                        waited += 0.5
                    try:
                        return self._chat.send_message(parts)
                    except Exception as re_err:
                        rt, st = self._is_retryable(re_err)
                        if not self._is_limit_error(re_err, st):
                            e, retryable, status = re_err, rt, st  # different failure now
                            reason = (f"server error ({st})" if st in (500, 502, 503, 504)
                                      else "does not exist (404)" if st == 404
                                      else "timed out / no response")
                            break
                        # a spare key may have freed up during the wait — try it again
                        api_resp = _try_api_key_fallback(reason)
                        if api_resp is not None:
                            return api_resp
                        continue  # still limited -> wait longer and retry
            return _try_fallbacks(reason)

    def _run_turn(self, user_text: str):
        self._stop_flag.clear()
        self._fail_counts: dict[str, int] = {}  # tool name -> consecutive failures this turn
        self._fail_lock = threading.Lock()
        # Frame the turn with the current run-mode directive so plan/chat/read-only/auto
        # behavior is live every turn (Gemini's system_instruction is set only at chat init).
        try:
            directive = agent_profiles.run_mode_directive(getattr(self, "run_mode", None))
        except Exception:
            directive = ""
        if not getattr(self, "auto_screenshot", True):
            # Privacy control: user has turned screen viewing OFF.
            directive += ("\n# Screen viewing is OFF (user setting): do NOT call take_screenshot, "
                          "capture_window, zoom_screenshot, or read_screen_text. Use the browser DOM, "
                          "files, and shell instead; if a task truly needs the screen, ask the user to "
                          "re-enable screen viewing in Settings.\n")
        user_text = (directive + "\n" + user_text) if directive else user_text
        try:
            # No keyword auto-screenshot: the model DECIDES whether it needs to see the
            # screen and calls take_screenshot / read_screen_text itself (per the system
            # prompt), so we never waste a capture on a request that doesn't need pixels.
            response = self._send_with_retry([user_text])
            self._process_response(response)
        except Exception as e:
            self._emit(AgentEvent("error", f"{type(e).__name__}: {str(e)[:600]}"))
        finally:
            self._emit(AgentEvent("done"))

    def _compact_result(self, result: dict, max_str: int = 3000) -> dict:
        """Trim noisy fields and clip strings so failed tools don't bloat the next prompt."""
        out = {}
        for k, v in result.items():
            if k == "image_b64":
                continue
            if isinstance(v, str) and len(v) > max_str:
                out[k] = v[:max_str] + f"...[truncated {len(v) - max_str} chars]"
            elif isinstance(v, list) and len(v) > 60:
                out[k] = list(v[:60]) + [f"...[{len(v) - 60} more]"]
            else:
                out[k] = v
        return out

    def _handle_run_custom_tool(self, args: dict) -> dict:
        """Run an AI-authored custom tool: resolve its saved recipe (with the call's args
        substituted in) and execute each step through THIS agent's own _execute_fc, so every
        step keeps its normal events + safety/confirmation + audit. Stops on the first failure."""
        name = (args.get("name") or "").strip()
        call_args = args.get("args") if isinstance(args.get("args"), dict) else {}
        # Bounded recursion: a custom tool may call run_custom_tool, but not endlessly.
        depth = getattr(self, "_custom_depth", 0)
        if depth >= 3:
            return {"ok": False, "error": "custom-tool nesting limit reached (3)"}
        resolved = custom_tools.resolve_steps(name, call_args)
        if not resolved.get("ok"):
            return resolved
        steps = resolved.get("steps", [])
        if not steps:
            return {"ok": False, "error": f"custom tool '{name}' has no steps"}
        self._custom_depth = depth + 1
        results = []
        try:
            for i, step in enumerate(steps):
                if self._stop_flag.is_set():
                    return {"ok": False, "error": "stopped", "ran": i, "results": results}
                _n, res = self._execute_fc(_CustomFC(step["tool"], step.get("args") or {}))
                compact = self._compact_result(res) if isinstance(res, dict) else res
                results.append({"step": i, "tool": _n, "result": compact})
                if isinstance(res, dict) and not res.get("ok", True):
                    return {"ok": False, "tool": name, "ran": i + 1, "failed_step": i,
                            "failed_tool": _n, "error": res.get("error"), "results": results}
        finally:
            self._custom_depth = depth
        return {"ok": True, "tool": name, "steps_run": len(results), "results": results}

    def _execute_fc(self, fc) -> tuple[str, dict]:
        """Execute a single tool call: emit events, handle confirmation, run, fail-track.
        Returns (name, result). Safe to call from worker threads (events marshal via Qt)."""
        name = fc.name
        args = tools._to_plain(dict(fc.args)) if fc.args else {}
        if not isinstance(args, dict):
            args = {}
        # Polish: coerce args to their declared types ("100"->100, "true"->True) so
        # well-formed calls don't fail on a stringly-typed value.
        try:
            args = tool_args.coerce(_PARAM_TYPES.get(name, {}), args)
        except Exception:
            pass
        self._emit(AgentEvent("tool_call", {"name": name, "args": args}))

        # Sub-agent tool scoping: a spawned agent may be restricted to a whitelist.
        scope = getattr(self, "_active_tool_scope", None)
        if scope is not None and name not in scope and name not in _SCOPE_ALWAYS_ALLOW:
            result = {"ok": False, "error": f"tool '{name}' is outside this agent's allowed scope",
                      "allowed_sample": sorted(scope)[:12]}
            self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
            return (name, result)

        risk, reason = safety.classify(name, args)
        allowed_by_mode, mode_reason = safety.mode_allows(name, risk)
        if not allowed_by_mode:
            result = {"ok": False, "error": mode_reason, "blocked_by_mode": safety.current_mode()}
            self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
            memory.log_action(name, args, mode_reason)
            try:
                audit.record(name, args, risk, mode_reason)
            except Exception:
                pass
            return (name, result)
        if safety.needs_confirmation(risk):
            pending = PendingConfirmation(name, args, reason)
            self._emit(AgentEvent("confirm", pending))
            approved = pending.response.get()
            if not approved:
                result = {"ok": False, "error": "user denied this action"}
                self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                memory.log_action(name, args, "denied by user")
                return (name, result)

        if name == "ask_claude":
            result = self._handle_ask_claude(args)
        elif name == "pause_for_human":
            result = self._handle_human_pause(args)
        elif name == "spawn_agent":
            result = self._handle_spawn_agent(args)
        elif name == "agent_run":
            result = self._handle_agent_run(args)
        elif name == "run_custom_tool":
            result = self._handle_run_custom_tool(args)
        else:
            fn = TOOL_DISPATCH.get(name)
            if not fn:
                result = {"ok": False, "error": f"unknown tool {name}"}
            else:
                try:
                    result = fn(**args)
                except TypeError as e:
                    result = {"ok": False, "error": f"bad args: {e}"}
                except Exception as e:
                    result = {"ok": False, "error": str(e)}

        self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
        summary_brief = str(result.get("error") or result.get("action") or
                            {k: result[k] for k in list(result)[:3] if k != "image_b64"})[:200]
        memory.log_action(name, args, summary_brief)
        try:
            audit.record(name, args, risk, summary_brief)
        except Exception:
            pass

        # Failure tracking: if a tool keeps failing, nudge the model toward another approach.
        if not result.get("ok", True):
            with self._fail_lock:
                self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
                fails = self._fail_counts[name]
            if fails >= 2:
                result = dict(result)
                result["hint"] = (
                    f"{name} has failed {fails} times in this turn. STOP retrying it. "
                    f"Try a different approach (different tool, different args, or pause_for_human)."
                )
        else:
            with self._fail_lock:
                self._fail_counts.pop(name, None)
        return (name, result)

    def _execute_parallel(self, batch) -> list[tuple[str, dict]]:
        """Run a batch of read-only tool calls concurrently, preserving call order in
        the returned list so the model sees responses aligned to its requests."""
        import concurrent.futures
        results: list[tuple[str, dict] | None] = [None] * len(batch)
        max_workers = min(6, len(batch))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(self._execute_fc, fc): i for i, fc in enumerate(batch)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    results[i] = (batch[i].name, {"ok": False, "error": str(e)})
        return [r for r in results if r is not None]

    def _process_response(self, response):
        max_steps = 12
        steps = 0
        _econ_nudged = False  # only inject the "wrap it up" hint once per turn
        while steps < max_steps and not self._stop_flag.is_set():
            steps += 1
            # Usage dashboard: record every model response's token usage (free-tier headroom).
            try:
                _um = getattr(response, "usage_metadata", None)
                if _um is not None:
                    usage_tracker.record_call(
                        self.active_model,
                        int(getattr(_um, "prompt_token_count", 0) or 0),
                        int(getattr(_um, "candidates_token_count", 0) or 0),
                    )
            except Exception:
                pass
            function_calls = []
            text_parts = []
            try:
                candidates = getattr(response, "candidates", None) or []
                for cand in candidates:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in (content.parts or []):
                        fc = getattr(part, "function_call", None)
                        if fc and getattr(fc, "name", None):
                            function_calls.append(fc)
                        text = getattr(part, "text", None)
                        if text:
                            text_parts.append(text)
            except Exception as e:
                self._emit(AgentEvent("error", f"parse error: {e}"))
                return

            if text_parts:
                self._emit(AgentEvent("message", "\n".join(text_parts).strip()))

            if not function_calls:
                return

            # Fast path: a batch of ONLY read-only tools runs concurrently.
            batch = [fc for fc in function_calls if getattr(fc, "name", None)]
            if (len(batch) > 1 and
                    all(fc.name in PARALLEL_SAFE_TOOLS for fc in batch) and
                    not self._stop_flag.is_set()):
                response_parts = self._execute_parallel(batch)
            else:
                response_parts = []
                for fc in function_calls:
                    if self._stop_flag.is_set():
                        return
                    response_parts.append(self._execute_fc(fc))

            parts_to_send = []
            attach_image = None
            attach_image_mime = "image/jpeg"
            for name, result in response_parts:
                sanitized = self._compact_result(result)
                parts_to_send.append(
                    types.Part.from_function_response(name=name, response={"result": sanitized})
                )
                if name in ("take_screenshot", "capture_window", "browser_screenshot",
                             "zoom_screenshot") and result.get("ok"):
                    if "image_b64" in result:
                        attach_image = base64.b64decode(result["image_b64"])
                        attach_image_mime = result.get("mime_type", "image/jpeg")
            if attach_image:
                parts_to_send.append(self._image_part(attach_image, mime_type=attach_image_mime))

            # Step-economy nudge: if this turn is burning many model calls, remind the model
            # ONCE to batch any remaining reads and converge instead of one-tool-per-round.
            if steps >= 5 and not _econ_nudged and not self._stop_flag.is_set():
                _econ_nudged = True
                parts_to_send.append(types.Part.from_text(
                    text=(f"[step-economy: you have used {steps} model calls on this turn. Each extra step "
                          "is another API call. Batch ALL remaining independent reads into ONE step "
                          "(read_file/grep_files/list_directory run concurrently; don't use run_shell to "
                          "read), then answer or act. Stop gathering context unless it is strictly required "
                          "to finish.]")
                ))

            try:
                response = self._send_with_retry(parts_to_send)
            except Exception as e:
                self._emit(AgentEvent("error", f"send error: {str(e)[:500]}"))
                return

        if steps >= max_steps:
            self._emit(AgentEvent("message", "[step limit reached - say 'continue' to keep going]"))

    def _spawn(self, instructions: str, mode: str = "auto", allowed_tools=None,
               label: str = "subagent", max_steps: int = 10) -> dict:
        """Run a fresh, scoped sub-agent on `instructions` and return its transcript.

        The child forwards its events to THIS agent's subscribers, so its progress and
        any confirmation prompts surface in the same UI; it carries an isolated tool
        scope + run mode. Bounded recursion + fully defensive (never raises)."""
        if getattr(self, "_spawn_depth", 0) >= 2:
            return {"ok": False, "error": "sub-agent nesting limit reached"}
        if not (instructions or "").strip():
            return {"ok": False, "error": "spawn_agent needs a task"}
        try:
            child = Agent(
                api_key=self.api_key, model_name=self.model_name,
                secondary_api_key=self.secondary_api_key,
                dual_api_failover=self.dual_api_failover,
                anthropic_key=self.anthropic_key, anthropic_model=self.anthropic_model,
                fallback_models=list(self.fallback_models),
                auto_screenshot=self.auto_screenshot,
                request_timeout_seconds=self.request_timeout_seconds, lean_tools=True,
            )
        except Exception as e:
            return {"ok": False, "error": f"could not create sub-agent: {e}"}
        child._spawn_depth = getattr(self, "_spawn_depth", 0) + 1
        child.run_mode = mode if mode in agent_profiles.RUN_MODES else "auto"
        # Resolve the effective tool scope: explicit whitelist, else derive from mode.
        if allowed_tools:
            child._active_tool_scope = set(allowed_tools)
        elif child.run_mode in ("read_only", "chat"):
            child._active_tool_scope = set(safety.SAFE_READONLY)
        else:
            child._active_tool_scope = None
        transcript: list[str] = []

        def _relay(ev):
            try:
                if ev.kind == "message" and isinstance(ev.payload, str):
                    transcript.append(ev.payload)
            except Exception:
                pass
            # Forward to the parent UI so progress + confirmations are visible/answerable.
            self._emit(ev)

        child.subscribe(_relay)
        self._emit(AgentEvent("message", f"↳ spawned sub-agent ({label}, mode={child.run_mode})"))
        try:
            child._run_turn(instructions)
        except Exception as e:
            return {"ok": False, "error": f"sub-agent failed: {e}",
                    "transcript": transcript[-5:]}
        summary = transcript[-1] if transcript else "(sub-agent produced no text)"
        return {"ok": True, "label": label, "mode": child.run_mode,
                "summary": summary, "steps": len(transcript)}

    def _handle_spawn_agent(self, args: dict) -> dict:
        task = args.get("task", "") or ""
        mode = (args.get("mode", "auto") or "auto").lower()
        tools_wl = args.get("tools") or None
        allowed = None
        if tools_wl:
            try:
                allowed = agent_profiles.filter_tools(
                    list(TOOL_DISPATCH), {"mode": "custom", "allow": list(tools_wl)})
            except Exception:
                allowed = list(tools_wl)
        return self._spawn(task, mode=mode, allowed_tools=allowed, label="subagent")

    def _handle_agent_run(self, args: dict) -> dict:
        name = args.get("name", "") or ""
        task = args.get("task", "") or ""
        try:
            req = agent_profiles.build_run_request(name, task=task,
                                                   all_tool_names=list(TOOL_DISPATCH))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if not req.get("ok"):
            return req
        out = self._spawn(req["instructions"], mode=req["run_mode"],
                          allowed_tools=req.get("allowed_tools"), label=req["name"])
        try:
            agent_profiles.mark_ran(name)
        except Exception:
            pass
        return out

    def _handle_human_pause(self, args: dict) -> dict:
        reason = args.get("reason", "manual step required") or "manual step required"
        need = args.get("what_you_need", "complete the on-screen step, then click resume") or ""
        pending = PendingHumanPause(reason=reason, what_you_need=need)
        self._emit(AgentEvent("human_pause", pending))
        user_note = pending.response.get()
        return {
            "ok": True,
            "resumed": True,
            "user_note": user_note or "(no note)",
            "instruction": "User completed the manual step. Re-check the page state with browser_get_page or take_screenshot.",
        }

    def _handle_ask_claude(self, args: dict) -> dict:
        prompt = build_handoff_prompt(
            situation=args.get("situation", ""),
            gemini_summary=args.get("gemini_summary", ""),
            attempted_actions=args.get("attempted_actions", []) or [],
            screen_observations=args.get("screen_observations", ""),
            specific_question=args.get("specific_question", ""),
        )
        if self.anthropic_key:
            reply = try_anthropic_api(prompt, self.anthropic_key, self.anthropic_model)
            if reply and not reply.startswith("[Anthropic API"):
                self._emit(AgentEvent("claude_handoff", {"prompt": prompt, "auto_reply": reply}))
                return {"ok": True, "source": "anthropic_api", "claude_reply": reply}
        copy_to_clipboard(prompt)
        pending = PendingClaudeResponse(handoff_prompt=prompt)
        self._emit(AgentEvent("awaiting_claude", pending))
        claude_reply = pending.response.get()
        if not claude_reply or claude_reply.strip().lower() in {"skip", "cancel"}:
            return {"ok": False, "error": "user skipped Claude handoff"}
        return {"ok": True, "source": "manual_paste", "claude_reply": claude_reply}
