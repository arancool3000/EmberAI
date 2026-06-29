"""A curated, fully-offline toolset for the local Ollama brain.

Small local models can't juggle Ember's ~288 tools, so this exposes the core set a local
model can actually drive — terminal, files, screen (screenshot + OCR), mouse/keyboard,
system info, app/file opening, and memory. All of these are LOCAL (no internet), so they
work in Offline Mode.

Schemas are in the Ollama/OpenAI "tools" function format. dispatch() maps each name to the
SAME callable the cloud agent uses (in tools/screen_vision/memory). The schema list is a pure
constant (testable); the callables are imported lazily so this module loads without the GUI
deps in a headless test environment.
"""
from __future__ import annotations


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {},
                       "required": required or []}}}


_STR = {"type": "string"}
_INT = {"type": "integer"}

# The curated offline tool schemas handed to the local model.
TOOLS = [
    _fn("run_shell", "Run a terminal/shell command on this computer and return its output "
        "(zsh on macOS, PowerShell on Windows). Use for installing packages, git, file ops, etc.",
        {"command": {**_STR, "description": "the shell command to run"}}, ["command"]),
    _fn("read_file", "Read a text file's contents.",
        {"path": {**_STR, "description": "absolute or ~ path to the file"}}, ["path"]),
    _fn("write_file", "Create or overwrite a text file with the given content.",
        {"path": _STR, "content": _STR}, ["path", "content"]),
    _fn("list_directory", "List files/folders in a directory.",
        {"path": _STR, "pattern": {**_STR, "description": "optional glob, e.g. *.py"}}, ["path"]),
    _fn("search_files", "Search the filesystem for files matching a query.",
        {"query": _STR, "root": {**_STR, "description": "folder to search from (default home)"}},
        ["query"]),
    _fn("take_screenshot", "Capture the screen so you can see what's on it.", {}, []),
    _fn("read_screen_text", "Read the text currently visible on screen via on-device OCR.",
        {"query": {**_STR, "description": "optional text to look for"}}, []),
    _fn("click", "Click the mouse at screen coordinates (x, y).",
        {"x": _INT, "y": _INT}, ["x", "y"]),
    _fn("move_mouse", "Move the mouse to screen coordinates (x, y).",
        {"x": _INT, "y": _INT}, ["x", "y"]),
    _fn("type_text", "Type text on the keyboard.", {"text": _STR}, ["text"]),
    _fn("press_key", "Press a key or key-combo, e.g. 'enter', 'cmd+s', 'ctrl+c'.",
        {"keys": _STR}, ["keys"]),
    _fn("scroll", "Scroll the screen up or down.",
        {"direction": {**_STR, "description": "'up' or 'down'"}, "amount": _INT}, ["direction"]),
    _fn("list_windows", "List the open application windows.", {}, []),
    _fn("get_system_info", "Get this computer's OS, CPU, memory and disk info.", {}, []),
    _fn("get_running_processes", "List running processes (optionally filtered).",
        {"filter_text": _STR}, []),
    _fn("open_app", "Open/launch an application by name.", {"name": _STR}, ["name"]),
    _fn("open_path", "Open a file or folder in the OS.", {"path": _STR}, ["path"]),
    _fn("remember", "Save a durable fact about the user for later.",
        {"key": _STR, "value": _STR}, ["key", "value"]),
    _fn("recall", "Look up saved facts (empty query returns all).", {"query": _STR}, []),
    _fn("set_timer", "Start a countdown timer that alerts when it elapses. Use for 'set a 10 "
        "minute timer' / 'remind me in 90 seconds'.",
        {"duration": {**_STR, "description": "e.g. '5m', '90s', '1h30m'"},
         "label": {**_STR, "description": "optional name, e.g. 'tea'"}}, ["duration"]),
    _fn("list_timers", "List active countdown timers and the time left on each.", {}, []),
    _fn("cancel_timer", "Cancel a running timer by id (from list_timers), or 'all'.",
        {"timer_id": _STR}, ["timer_id"]),
]

TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOLS)
# Tools that only READ (no confirmation needed even in stricter modes).
READONLY = frozenset({"read_file", "list_directory", "search_files", "take_screenshot",
                      "read_screen_text", "list_windows", "get_system_info",
                      "get_running_processes", "recall", "list_timers"})

# Common name variants local models invent for our tools (e.g. it asks for "screenshot"
# instead of "take_screenshot"). Resolving these means the action runs instead of the raw
# tool-call JSON leaking into the chat. Map alias -> canonical tool name.
TOOL_ALIASES = {
    "screenshot": "take_screenshot",
    "take_screen_shot": "take_screenshot",
    "capture_screen": "take_screenshot",
    "capture_screenshot": "take_screenshot",
    "screen_capture": "take_screenshot",
    "grab_screen": "take_screenshot",
    "read_screen": "read_screen_text",
    "screen_text": "read_screen_text",
    "ocr": "read_screen_text",
    "ocr_screen": "read_screen_text",
    "type": "type_text",
    "type_string": "type_text",
    "keyboard_type": "type_text",
    "press": "press_key",
    "press_keys": "press_key",
    "keypress": "press_key",
    "hotkey": "press_key",
    "shell": "run_shell",
    "bash": "run_shell",
    "run_command": "run_shell",
    "execute": "run_shell",
    "exec": "run_shell",
    "terminal": "run_shell",
    "open_application": "open_app",
    "launch_app": "open_app",
    "launch": "open_app",
    "ls": "list_directory",
    "list_dir": "list_directory",
    "list_files": "list_directory",
    "cat": "read_file",
    "open_file": "open_path",
    "mouse_click": "click",
    "left_click": "click",
    "move": "move_mouse",
    "system_info": "get_system_info",
    "processes": "get_running_processes",
    "windows": "list_windows",
}


def resolve_name(name: str) -> str:
    """Canonicalise a (possibly aliased) tool name to one Ember actually has.
    Returns the canonical name if known, else the input unchanged."""
    if not isinstance(name, str):
        return ""
    if name in TOOL_NAMES:
        return name
    low = name.strip().lower()
    if low in TOOL_NAMES:
        return low
    return TOOL_ALIASES.get(low, name)

# Args that must be integers (local models often send them as strings).
_INT_ARGS = {"x", "y", "amount"}

# The arguments each tool actually declares — used to drop hallucinated extras (e.g. a model
# calling take_screenshot with a bogus {"path": ...}) so the call doesn't error on bad kwargs.
_ALLOWED_ARGS = {t["function"]["name"]: set((t["function"]["parameters"].get("properties") or {}))
                 for t in TOOLS}


def _dispatch() -> dict:
    """name -> callable, importing the tool modules lazily (they pull GUI deps)."""
    import tools
    import memory
    d = {
        "run_shell": tools.run_powershell,
        "read_file": tools.read_file,
        "write_file": tools.write_file,
        "list_directory": tools.list_directory,
        "search_files": tools.search_files,
        "take_screenshot": tools.take_screenshot,
        "click": tools.click,
        "move_mouse": tools.move_mouse,
        "type_text": tools.type_text,
        "press_key": tools.press_key,
        "scroll": tools.scroll,
        "list_windows": tools.list_windows,
        "get_system_info": tools.get_system_info,
        "get_running_processes": tools.get_running_processes,
        "open_app": tools.open_app,
        "open_path": tools.open_path,
        "remember": memory.remember,
        "recall": memory.recall,
    }
    try:
        import screen_vision
        d["read_screen_text"] = screen_vision.read_screen_text
    except Exception:
        pass
    try:
        import timers
        d["set_timer"] = timers.set_timer
        d["list_timers"] = timers.list_timers
        d["cancel_timer"] = timers.cancel_timer
    except Exception:
        pass
    return d


def coerce_args(name: str, args) -> dict:
    """Normalize the model's arguments (parse JSON strings, int-ify x/y/amount)."""
    import json
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    allowed = _ALLOWED_ARGS.get(resolve_name(name))
    out = {}
    for k, v in args.items():
        if allowed is not None and k not in allowed:
            continue   # drop hallucinated args the tool doesn't declare
        if k in _INT_ARGS and isinstance(v, str):
            try:
                v = int(float(v))
            except Exception:
                pass
        out[k] = v
    return out


def call(name: str, args) -> dict:
    """Run one curated tool by name with the model's args. Returns the tool's result dict."""
    name = resolve_name(name)
    if name not in TOOL_NAMES:
        return {"ok": False, "error": f"unknown tool {name}"}
    fn = _dispatch().get(name)
    if fn is None:
        return {"ok": False, "error": f"{name} is unavailable on this system"}
    try:
        return fn(**coerce_args(name, args))
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
