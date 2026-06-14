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
import file_ops
import more_tools
import extra_tools
import screen_vision
import remote_server
import scheduled_tasks
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

# Speed
- Spend API calls like a scarce resource. Aim for one observe call, one batched action call, and one final
  verification call. If a tool result fully answers the user, answer immediately instead of gathering more.
- Batch deterministic steps with do_sequence (one API request). Include waits and a final assertion inside
  the same do_sequence whenever the next action is obvious. Don't screenshot between every action -
  screenshot/assert_text_visible once at the END to verify.
- A batch of read-only tools (read_screen_text, list_*, get_*, http_get, ...) runs concurrently - issue them
  together when gathering info.
- paste_text beats type_text for anything over a few words.
- Never repeat an identical failing call. Change tool, args, or tactic.
- Prefer high-signal tools over broad ones: browser_get_page for webpages, read_screen_text(query=...) for
  visible labels, list_directory with a narrow path/pattern for files, and shell only when it gives a shorter
  answer than UI work.

# Reasoning pattern
Think quietly before acting: identify the user's real goal, the minimum evidence needed, the safest tool path,
and how you will verify. Do not narrate this plan unless the user asks. Prefer reversible actions, preserve
state, and remember durable preferences/paths only when they will help future work.

# Self-recovery ladder (exhaust before pausing)
smart_click -> read_screen_text to see exact labels -> find_ui_elements (try a partial match, scroll, or
scope="desktop") -> keyboard navigation -> right_click_element_by_text then read the menu -> last, click(x,y)
from a tool-sourced coordinate. Only after these: ask_claude (hard reasoning) or pause_for_human (truly blocked).

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
})


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
                 dual_api_failover: bool = True,
                 anthropic_key: str | None = None,
                 anthropic_model: str = "claude-opus-4-8",
                 fallback_models: list[str] | None = None,
                 auto_screenshot: bool = True,
                 request_timeout_seconds: int = 15):
        self.api_key = api_key
        self.secondary_api_key = (secondary_api_key or "").strip() or None
        self.dual_api_failover = bool(dual_api_failover)
        self.api_keys = [api_key]
        if self.dual_api_failover and self.secondary_api_key and self.secondary_api_key != api_key:
            self.api_keys.append(self.secondary_api_key)
        self._api_key_index = 0
        self.model_name = model_name
        self.active_model = model_name
        self.fallback_models = fallback_models if fallback_models is not None else list(self.DEFAULT_FALLBACKS)
        self.anthropic_key = anthropic_key
        self.anthropic_model = anthropic_model
        self.auto_screenshot = auto_screenshot
        # Gemini enforces a 10s minimum deadline. Cap upper limit so the agent can't
        # accidentally wait minutes per call.
        self.request_timeout_seconds = max(10, min(60, int(request_timeout_seconds or 15)))
        # User-configurable request deadline. Gemini enforces 10s minimum.
        self._client = self._make_client(self.api_keys[self._api_key_index])
        # Per-session latency tracking so we auto-prefer fast models on later turns.
        self._model_latencies: dict[str, float] = {}
        self._chat = None
        self._event_subs: list[Callable[[AgentEvent], None]] = []
        self._stop_flag = threading.Event()
        self._init_chat()

    def _make_client(self, api_key: str):
        try:
            return genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=self.request_timeout_seconds * 1000),
            )
        except Exception:
            return genai.Client(api_key=api_key)

    def _switch_api_key(self, index: int):
        self._api_key_index = index
        self._client = self._make_client(self.api_keys[self._api_key_index])
        self._init_chat(model=self.active_model)

    def _api_label(self, index: int | None = None) -> str:
        i = self._api_key_index if index is None else index
        return "primary API" if i == 0 else f"backup API {i}"

    def _init_chat(self, model: str | None = None):
        if model:
            self.active_model = model
        tool_decls = [types.FunctionDeclaration(**td) for td in TOOL_DECLARATIONS]
        tool_obj = types.Tool(function_declarations=tool_decls)
        # Speed-tuned generation:
        #  - low temperature for faster, more deterministic tool selection
        #  - cap output tokens (most responses are short text + tool calls)
        config_kwargs = dict(
            system_instruction=build_system_prompt(),
            tools=[tool_obj],
            safety_settings=_make_safety_settings(),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.15,
            top_p=0.9,
            max_output_tokens=3600,
        )
        try:
            config = types.GenerateContentConfig(**config_kwargs)
        except TypeError:
            # Older SDK without some fields - drop the optional ones.
            for k in ("temperature", "top_p", "max_output_tokens"):
                config_kwargs.pop(k, None)
            config = types.GenerateContentConfig(**config_kwargs)
        self._chat = self._client.chats.create(model=self.active_model, config=config)

    def reset(self):
        self._init_chat()
        self._model_latencies = {}
        if hasattr(self, "_bad_models"):
            self._bad_models = set()

    def stop(self):
        self._stop_flag.set()

    def subscribe(self, fn: Callable[[AgentEvent], None]):
        self._event_subs.append(fn)

    def _emit(self, event: AgentEvent):
        for fn in self._event_subs:
            try:
                fn(event)
            except Exception:
                traceback.print_exc()

    def send_user_message(self, text: str):
        t = threading.Thread(target=self._run_turn, args=(text,), daemon=True)
        t.start()

    def _image_part(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> types.Part:
        return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

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
            Aborts cleanly if we're mid-turn (can't replay function_call context on a fresh chat)."""
            # Detect mid-turn: parts containing function_response can't be sent to a fresh chat.
            mid_turn = False
            try:
                for p in parts:
                    if hasattr(p, "function_response") and getattr(p, "function_response", None) is not None:
                        mid_turn = True
                        break
            except Exception:
                pass
            if mid_turn:
                raise RuntimeError(
                    f"{self.active_model} {reason} mid-turn. Click reset (↻) and try again - "
                    "switching models mid-task isn't supported because chat context can't carry over."
                )

            last_err = None
            for fb in self.fallback_models:
                if not fb or fb in tried_models or fb in self._bad_models:
                    continue
                self._emit(AgentEvent("message",
                    f"[{self.active_model} {reason} - switching to {fb}]"))
                try:
                    self._init_chat(model=fb)
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
            """Retry the SAME model with another Gemini API key before changing models."""
            if len(self.api_keys) < 2:
                return None
            mid_turn = False
            try:
                for p in parts:
                    if hasattr(p, "function_response") and getattr(p, "function_response", None) is not None:
                        mid_turn = True
                        break
            except Exception:
                pass
            if mid_turn:
                return None
            current_key = self._api_key_index
            last_err = None
            for i, _key in enumerate(self.api_keys):
                if i == current_key:
                    continue
                self._emit(AgentEvent("message",
                    f"[{self.active_model} {reason} on {self._api_label(current_key)} - "
                    f"retrying same model with {self._api_label(i)}]"))
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
                api_resp = _try_api_key_fallback(reason)
                if api_resp is not None:
                    return api_resp
            return _try_fallbacks(reason)

    # Words that suggest the model probably needs a fresh screenshot to start.
    _SCREEN_HINTS = (
        "screen", "screenshot", "see ", "look ", "what's on", "what is on", "show me",
        "click", "press", "open ", "scroll", "drag", "window", "icon", "button",
        "menu", "popup", "dialog", "tab ", "tabs", "fullscreen", "minimize",
    )

    def _should_auto_screenshot(self, user_text: str) -> bool:
        if not self.auto_screenshot:
            return False
        t = user_text.lower()
        return any(h in t for h in self._SCREEN_HINTS)

    def _run_turn(self, user_text: str):
        self._stop_flag.clear()
        self._fail_counts: dict[str, int] = {}  # tool name -> consecutive failures this turn
        self._fail_lock = threading.Lock()
        try:
            parts = [user_text]
            if self._should_auto_screenshot(user_text):
                # Fast path: no grid + no cursor for the pre-message context.
                # The AI can call take_screenshot(grid=True) when it actually needs to click.
                shot = tools.take_screenshot(grid=False, show_cursor=False)
                actual = tools.get_screen_size()
                note = (f"\n[Screenshot: {shot['width']}x{shot['height']} of {actual['width']}x{actual['height']}]")
                parts = [user_text + note, self._image_part(
                    base64.b64decode(shot["image_b64"]),
                    mime_type=shot.get("mime_type", "image/jpeg"),
                )]
            response = self._send_with_retry(parts)
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

    def _execute_fc(self, fc) -> tuple[str, dict]:
        """Execute a single tool call: emit events, handle confirmation, run, fail-track.
        Returns (name, result). Safe to call from worker threads (events marshal via Qt)."""
        name = fc.name
        args = tools._to_plain(dict(fc.args)) if fc.args else {}
        if not isinstance(args, dict):
            args = {}
        self._emit(AgentEvent("tool_call", {"name": name, "args": args}))

        risk, reason = safety.classify(name, args)
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
        while steps < max_steps and not self._stop_flag.is_set():
            steps += 1
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

            try:
                response = self._send_with_retry(parts_to_send)
            except Exception as e:
                self._emit(AgentEvent("error", f"send error: {str(e)[:500]}"))
                return

        if steps >= max_steps:
            self._emit(AgentEvent("message", "[step limit reached - say 'continue' to keep going]"))

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
