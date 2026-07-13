"""Ember builds real, standalone software for you.

"I can't install third-party software for you" — so Ember can WRITE it instead. The AI authors a
program (Python, shell, or AppleScript); Ember saves it to a visible **Ember Apps** folder in your
home directory, validates it, makes it runnable, and drops a double-click launcher for your OS. It
can then launch it, list what it has built, remove one, or reveal the folder. Unlike the in-Ember
tools from self_extend.py, these run on their OWN, outside Ember.

Also includes keep_awake / stop_keep_awake — a built-in that stops the computer sleeping (via the
OS's own caffeinate / power APIs) so, e.g., music keeps playing. That's the scenario that inspired
this: rather than telling you to install Amphetamine, Ember just does it (with the honest caveat
that a closed Mac lid still sleeps unless you disable lid sleep).

Safety: build_app writes runnable code, so it's classified HIGH — you see the code and approve
once; running/opening a built app afterward is automatic. Standard library only. _RUNNER and
_SPAWN are injection points so tests never actually launch a process.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Injection points for tests:
#   _RUNNER(argv:list, cwd:str) -> int|None   — launch a built app, return a pid
#   _SPAWN(argv:list) -> object               — start the keep-awake helper, return a handle w/ pid
_RUNNER = None
_SPAWN = None

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{1,49}$")
_KINDS = {"python", "shell", "applescript"}

# The running keep-awake helper (module-level so stop_keep_awake can end it).
_awake = None


# ---------------------------------------------------------------------------
# Apps folder (user-visible, not a hidden support dir)
# ---------------------------------------------------------------------------
def _apps_dir() -> Path:
    override = os.environ.get("EMBER_APPS_DIR")           # tests redirect here
    d = Path(override) if override else (Path.home() / "Ember Apps")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())[:50]


def _app_dir(name: str) -> Path:
    return _apps_dir() / _slug(name)


# ---------------------------------------------------------------------------
# Per-kind / per-OS file + run details
# ---------------------------------------------------------------------------
def _plan(kind: str) -> dict:
    """Return {main, shebang, run} for a program `kind` on this OS. `run` is the argv (relative
    to the app dir) that launches it."""
    kind = (kind or "python").lower()
    win = sys.platform.startswith("win")
    if kind == "python":
        return {"main": "main.py", "shebang": "",
                "run": (["python", "main.py"] if win else ["python3", "main.py"])}
    if kind == "applescript":
        return {"main": "main.applescript", "shebang": "", "run": ["osascript", "main.applescript"]}
    # shell
    if win:
        return {"main": "main.bat", "shebang": "", "run": ["cmd", "/c", "main.bat"]}
    return {"main": "main.sh", "shebang": "#!/bin/bash", "run": ["bash", "main.sh"]}


def _validate(kind: str, path: Path) -> str | None:
    """Return an error string if the program is obviously broken, else None."""
    try:
        if kind == "python":
            import py_compile
            py_compile.compile(str(path), doraise=True)
        elif kind == "shell" and not sys.platform.startswith("win") and shutil.which("bash"):
            r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return (r.stderr or "shell syntax error").strip()
        # applescript / windows .bat: best-effort, no cheap offline syntax check
    except Exception as e:
        return getattr(e, "msg", None) or str(e)
    return None


def _write_launcher(app_dir: Path, name: str, run: list) -> Path | None:
    """A double-clickable launcher next to the app folder."""
    cmd = " ".join(run)
    try:
        if sys.platform == "darwin":
            p = _apps_dir() / f"{name}.command"
            p.write_text(f'#!/bin/bash\ncd "{app_dir}"\nexec {cmd}\n', "utf-8")
            os.chmod(p, 0o755)
            return p
        if sys.platform.startswith("win"):
            p = _apps_dir() / f"{name}.bat"
            p.write_text(f'@echo off\r\ncd /d "{app_dir}"\r\n{cmd}\r\n', "utf-8")
            return p
        p = _apps_dir() / f"{name}.sh"                    # Linux: a runnable shell launcher
        p.write_text(f'#!/bin/bash\ncd "{app_dir}"\nexec {cmd}\n', "utf-8")
        os.chmod(p, 0o755)
        return p
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def build_app(name: str = "", code: str = "", kind: str = "python", description: str = "",
              make_launcher: bool = True, overwrite: bool = False) -> dict:
    """Build a STANDALONE program the user can run on its own (outside Ember). `kind` is
    'python' | 'shell' | 'applescript'. Saved to the user's 'Ember Apps' folder with a
    double-click launcher. Use this to actually MAKE software the user needs instead of telling
    them to install something."""
    name = _slug(name)
    if not name or not _NAME_RE.match(name):
        return {"ok": False, "error": "app name must be 2-50 chars: letters, digits, space, - or _"}
    if (kind or "python").lower() not in _KINDS:
        return {"ok": False, "error": f"kind must be one of {sorted(_KINDS)}"}
    if not code or not code.strip():
        return {"ok": False, "error": "no code provided"}
    kind = kind.lower()
    if kind == "applescript" and sys.platform != "darwin":
        return {"ok": False, "error": "AppleScript apps only work on macOS"}

    app_dir = _app_dir(name)
    if app_dir.exists() and not overwrite:
        return {"ok": False, "error": f"an app named '{name}' already exists; pass overwrite=true"}
    plan = _plan(kind)
    body = code
    if plan["shebang"] and not code.startswith("#!"):
        body = plan["shebang"] + "\n" + code

    app_dir.mkdir(parents=True, exist_ok=True)
    main_path = app_dir / plan["main"]
    main_path.write_text(body, "utf-8")

    err = _validate(kind, main_path)
    if err:
        # Don't leave broken software on disk.
        try:
            shutil.rmtree(app_dir, ignore_errors=True)
        except Exception:
            pass
        return {"ok": False, "error": f"the program has an error and was not saved: {err}"}

    if not sys.platform.startswith("win"):
        try:
            os.chmod(main_path, 0o755)
        except Exception:
            pass

    launcher = _write_launcher(app_dir, name, plan["run"]) if make_launcher else None
    (app_dir / "app.json").write_text(json.dumps(
        {"name": name, "kind": kind, "description": description, "main": plan["main"],
         "run": plan["run"], "created": int(time.time())}, indent=2), "utf-8")

    return {"ok": True, "name": name, "dir": str(app_dir), "main": str(main_path),
            "launcher": str(launcher) if launcher else "",
            "run_hint": f"Double-click {launcher.name if launcher else plan['main']}, or ask me "
                        f"to run '{name}'.",
            "message": f"Built '{name}' in your Ember Apps folder."}


def _manifest(name: str) -> dict:
    try:
        return json.loads((_app_dir(name) / "app.json").read_text("utf-8"))
    except Exception:
        return {}


def run_app(name: str = "") -> dict:
    """Launch a standalone app Ember built earlier."""
    name = _slug(name)
    app_dir = _app_dir(name)
    man = _manifest(name)
    run = man.get("run")
    if not app_dir.exists() or not run:
        return {"ok": False, "error": f"no built app named '{name}'"}
    try:
        if _RUNNER is not None:
            pid = _RUNNER(list(run), str(app_dir))
        else:
            # Detached so the app keeps running after this call (and survives Ember, for a
            # utility like keep-awake the user launches then walks away).
            kwargs = {"cwd": str(app_dir)}
            if hasattr(os, "setsid"):
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(run, **kwargs)
            pid = proc.pid
        return {"ok": True, "name": name, "pid": pid, "message": f"Launched '{name}'."}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"couldn't launch (missing interpreter?): {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_apps() -> dict:
    """List the standalone apps Ember has built."""
    apps = []
    for d in sorted(_apps_dir().iterdir() if _apps_dir().exists() else []):
        if d.is_dir() and (d / "app.json").exists():
            m = _manifest(d.name)
            apps.append({"name": m.get("name", d.name), "kind": m.get("kind", ""),
                         "description": m.get("description", ""), "dir": str(d)})
    return {"ok": True, "count": len(apps), "apps": apps, "folder": str(_apps_dir())}


def remove_app(name: str = "") -> dict:
    """Delete a standalone app Ember built (its folder and launcher)."""
    name = _slug(name)
    app_dir = _app_dir(name)
    if not app_dir.exists():
        return {"ok": False, "error": f"no built app named '{name}'"}
    shutil.rmtree(app_dir, ignore_errors=True)
    for suffix in (".command", ".bat", ".sh"):
        try:
            (_apps_dir() / f"{name}{suffix}").unlink()
        except Exception:
            pass
    return {"ok": True, "removed": name, "message": f"Deleted '{name}'."}


def open_apps_folder() -> dict:
    """Reveal the Ember Apps folder in the file manager."""
    d = str(_apps_dir())
    try:
        if _RUNNER is not None:
            _RUNNER(["__open__", d], d)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        elif sys.platform.startswith("win"):
            os.startfile(d)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", d])
        return {"ok": True, "folder": d}
    except Exception as e:
        return {"ok": False, "error": str(e), "folder": d}


# ---------------------------------------------------------------------------
# keep_awake — the built-in that inspired this
# ---------------------------------------------------------------------------
def _awake_argv(minutes: int, keep_display_on: bool) -> list:
    secs = int(minutes) * 60 if minutes and int(minutes) > 0 else 0
    if sys.platform == "darwin":
        argv = ["caffeinate", "-i", "-s"]                 # idle + system (on AC)
        if keep_display_on:
            argv.append("-d")
        if secs:
            argv += ["-t", str(secs)]
        return argv
    if sys.platform.startswith("win"):
        # A tiny PowerShell loop that asserts the "stay awake" execution state.
        flags = "0x80000000 -bor 0x00000001" + (" -bor 0x00000002" if keep_display_on else "")
        dur = f"Start-Sleep -Seconds {secs}" if secs else "while($true){Start-Sleep -Seconds 60}"
        ps = ("Add-Type -Name P -Namespace W -MemberDefinition "
              "'[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint e);';"
              f"[W.P]::SetThreadExecutionState({flags}); {dur}")
        return ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
    # Linux
    what = "idle:sleep"
    inner = f"sleep {secs}" if secs else "sleep infinity"
    return ["systemd-inhibit", f"--what={what}", "--why=Ember keep awake",
            "--mode=block", "bash", "-c", inner]


def keep_awake(minutes: int = 0, keep_display_on: bool = False) -> dict:
    """Stop the computer sleeping so audio/downloads keep going. minutes=0 = until you stop it
    (call stop_keep_awake). Uses the OS's own power API (macOS caffeinate / Windows execution
    state / Linux systemd-inhibit) — no third-party app needed."""
    global _awake
    if _awake is not None and getattr(_awake, "poll", lambda: 1)() is None:
        return {"ok": True, "already": True, "pid": getattr(_awake, "pid", None),
                "message": "Already keeping the computer awake."}
    argv = _awake_argv(minutes, keep_display_on)
    try:
        _awake = _SPAWN(argv) if _SPAWN is not None else subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"ok": False, "error": "the OS power tool isn't available on this system"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    dur = f"for {int(minutes)} min" if minutes and int(minutes) > 0 else "until you stop it"
    note = ""
    if sys.platform == "darwin":
        note = (" Note: closing the Mac's lid still sleeps it unless you're in clamshell mode "
                "(external display + power). Keep the lid open for this to hold.")
    return {"ok": True, "pid": getattr(_awake, "pid", None),
            "message": f"Keeping the computer awake {dur}.{note}"}


def stop_keep_awake() -> dict:
    """Let the computer sleep normally again (stop keep_awake)."""
    global _awake
    if _awake is None:
        return {"ok": True, "message": "wasn't keeping the computer awake"}
    try:
        _awake.terminate()
    except Exception:
        pass
    _awake = None
    return {"ok": True, "message": "The computer can sleep normally again."}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "build_app",
        "description": ("Build STANDALONE software the user can run on its own (outside Ember) — "
                        "a Python/shell/AppleScript program saved to their 'Ember Apps' folder "
                        "with a double-click launcher. Use this to actually MAKE the utility a "
                        "user needs instead of telling them to install a third-party app."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "app name (letters/digits/space/-/_)"},
                "code": {"type": "STRING", "description": "the program source"},
                "kind": {"type": "STRING", "description": "python | shell | applescript"},
                "description": {"type": "STRING", "description": "what it does"},
                "make_launcher": {"type": "BOOLEAN", "description": "double-click launcher (default true)"},
                "overwrite": {"type": "BOOLEAN", "description": "replace an existing app of this name"},
            },
            "required": ["name", "code"],
        },
    },
    {"name": "run_app",
     "description": "Launch a standalone app Ember built earlier.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "list_apps", "description": "List the standalone apps Ember has built.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "remove_app", "description": "Delete a standalone app Ember built.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}}, "required": ["name"]}},
    {"name": "open_apps_folder", "description": "Reveal the Ember Apps folder in the file manager.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {
        "name": "keep_awake",
        "description": ("Stop the computer from sleeping (so music/downloads keep going) using the "
                        "OS's own power tools — no third-party app. minutes=0 keeps it awake until "
                        "stop_keep_awake."),
        "parameters": {"type": "OBJECT",
                       "properties": {"minutes": {"type": "INTEGER", "description": "0 = indefinite"},
                                      "keep_display_on": {"type": "BOOLEAN",
                                                          "description": "also keep the screen on"}},
                       "required": []},
    },
    {"name": "stop_keep_awake", "description": "Let the computer sleep normally again.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "build_app": build_app,
    "run_app": run_app,
    "list_apps": list_apps,
    "remove_app": remove_app,
    "open_apps_folder": open_apps_folder,
    "keep_awake": keep_awake,
    "stop_keep_awake": stop_keep_awake,
}

READONLY_TOOLS = {"list_apps"}
INTERACTION_TOOLS = {"build_app", "run_app", "remove_app", "open_apps_folder",
                     "keep_awake", "stop_keep_awake"}
