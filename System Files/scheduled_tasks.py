"""Persistent scheduled tasks for Ember.

Ember can use these when the user asks for a future action. On macOS the task is
stored as a per-user LaunchAgent; on Windows it is created in Task Scheduler.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        d = home / "Library" / "Application Support" / "Ember" / "Scheduled Tasks"
    elif sys.platform.startswith("win"):
        d = home / "AppData" / "Roaming" / "Ember" / "Scheduled Tasks"
    else:
        d = home / ".ember" / "scheduled_tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_when(run_at: str) -> datetime:
    text = (run_at or "").strip()
    if not text:
        raise ValueError("run_at is required")
    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("run_at must look like '2026-06-12 21:30'")


def _task_id(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "ember-task").strip().lower()).strip("-")
    if not base:
        base = "ember-task"
    return f"{base[:38]}-{int(time.time())}"


def _meta_path(task_id: str) -> Path:
    return _data_dir() / f"{task_id}.json"


def _write_meta(task_id: str, meta: dict):
    _meta_path(task_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _mac_interval(dt: datetime, repeat: str) -> dict:
    repeat = (repeat or "once").lower()
    if repeat == "hourly":
        return {"Minute": dt.minute}
    if repeat == "daily":
        return {"Hour": dt.hour, "Minute": dt.minute}
    if repeat == "weekly":
        # launchd uses Sunday=0; Python uses Monday=0.
        weekday = (dt.weekday() + 1) % 7
        return {"Weekday": weekday, "Hour": dt.hour, "Minute": dt.minute}
    return {"Month": dt.month, "Day": dt.day, "Hour": dt.hour, "Minute": dt.minute}


def _schedule_macos(task_id: str, name: str, command: str, dt: datetime,
                    repeat: str, working_directory: str | None) -> dict:
    label = f"com.ember.task.{task_id}"
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{label}.plist"
    script_path = _data_dir() / f"{task_id}.zsh"
    log_path = _data_dir() / f"{task_id}.log"
    err_path = _data_dir() / f"{task_id}.err"
    wd = Path(working_directory).expanduser() if working_directory else Path.home()

    pre_cleanup = ""
    post_cleanup = ""
    if (repeat or "once").lower() == "once":
        # launchd's StartCalendarInterval has no Year field, so a "once" task otherwise
        # re-fires every year. Delete the plist UP FRONT (before running the command) so it
        # can never reload at the next login/year, even if the command errors or the Mac was
        # asleep at fire time. (We remove the plist — not the running script — here, since
        # zsh reads its own script file incrementally and rm-ing it mid-run can break it.)
        pre_cleanup = f"/bin/rm -f {shlex.quote(str(plist_path))} 2>/dev/null || true\n"
        post_cleanup = (
            f"/bin/rm -f {shlex.quote(str(script_path))} 2>/dev/null || true\n"
            f"/bin/launchctl bootout gui/$(/usr/bin/id -u) {shlex.quote(label)} >/dev/null 2>&1 || true\n"
        )
    script_path.write_text(
        "#!/bin/zsh\n"
        "set -o pipefail\n"
        f"cd {shlex.quote(str(wd))}\n"
        f"{pre_cleanup}"
        f"{command}\n"
        "status=$?\n"
        f"{post_cleanup}"
        "exit $status\n",
        encoding="utf-8",
    )
    script_path.chmod(0o700)

    plist = {
        "Label": label,
        "ProgramArguments": [str(script_path)],
        "StartCalendarInterval": _mac_interval(dt, repeat),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(err_path),
        "WorkingDirectory": str(wd),
    }
    plist_path.write_bytes(plistlib.dumps(plist))
    uid = str(os.getuid())
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
                   capture_output=True, timeout=10)
    boot = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
                          capture_output=True, text=True, timeout=10)
    return {
        "ok": boot.returncode == 0,
        "task_id": task_id,
        "label": label,
        "plist": str(plist_path),
        "script": str(script_path),
        "stdout_log": str(log_path),
        "stderr_log": str(err_path),
        "error": (boot.stderr or boot.stdout or "").strip() if boot.returncode else "",
    }


def _schedule_windows(task_id: str, name: str, command: str, dt: datetime,
                      repeat: str, working_directory: str | None) -> dict:
    task_name = rf"Ember\{task_id}"
    script_path = _data_dir() / f"{task_id}.bat"
    wd = Path(working_directory).expanduser() if working_directory else Path.home()
    script_path.write_text(f"@echo off\r\ncd /d \"{wd}\"\r\n{command}\r\n", encoding="utf-8")
    sc = {"once": "ONCE", "daily": "DAILY", "weekly": "WEEKLY", "hourly": "HOURLY"}.get(
        (repeat or "once").lower(), "ONCE"
    )
    args = [
        "schtasks", "/Create", "/F", "/TN", task_name,
        "/TR", str(script_path), "/SC", sc,
        "/ST", dt.strftime("%H:%M"),
    ]
    if sc == "ONCE":
        args += ["/SD", dt.strftime("%m/%d/%Y")]
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    return {
        "ok": r.returncode == 0,
        "task_id": task_id,
        "task_name": task_name,
        "script": str(script_path),
        "error": (r.stderr or r.stdout or "").strip() if r.returncode else "",
    }


def schedule_shell_command(name: str, command: str, run_at: str,
                           repeat: str = "once",
                           working_directory: str | None = None) -> dict:
    """Schedule a shell command for later.

    repeat: once, hourly, daily, weekly. run_at is local time, e.g. 2026-06-12 21:30.
    """
    try:
        dt = _parse_when(run_at)
        repeat = (repeat or "once").lower()
        if repeat not in {"once", "hourly", "daily", "weekly"}:
            return {"ok": False, "error": "repeat must be once, hourly, daily, or weekly"}
        if repeat == "once" and dt.timestamp() < time.time() - 60:
            return {"ok": False, "error": "run_at is in the past"}
        task_id = _task_id(name)
        if sys.platform == "darwin":
            result = _schedule_macos(task_id, name, command, dt, repeat, working_directory)
        elif sys.platform.startswith("win"):
            result = _schedule_windows(task_id, name, command, dt, repeat, working_directory)
        else:
            return {"ok": False, "error": "persistent scheduling is supported on macOS and Windows"}
        meta = {
            "task_id": task_id,
            "name": name,
            "command": command,
            "run_at": run_at,
            "repeat": repeat,
            "working_directory": working_directory,
            "platform_result": result,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_meta(task_id, meta)
        result.update({"name": name, "run_at": run_at, "repeat": repeat})
        return result
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def list_scheduled_tasks() -> dict:
    tasks = []
    for p in sorted(_data_dir().glob("*.json")):
        try:
            tasks.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


def cancel_scheduled_task(task_id: str) -> dict:
    task_id = (task_id or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id is required"}
    meta = {}
    try:
        p = _meta_path(task_id)
        if p.exists():
            meta = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    errors = []
    if sys.platform == "darwin":
        label = ((meta.get("platform_result") or {}).get("label") or f"com.ember.task.{task_id}")
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
                       capture_output=True, timeout=10)
        for p in (plist_path, _data_dir() / f"{task_id}.zsh", _meta_path(task_id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append(str(e))
    elif sys.platform.startswith("win"):
        task_name = ((meta.get("platform_result") or {}).get("task_name") or rf"Ember\{task_id}")
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", task_name],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            errors.append((r.stderr or r.stdout or "").strip())
        for p in (_data_dir() / f"{task_id}.bat", _meta_path(task_id)):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    else:
        return {"ok": False, "error": "persistent scheduling is supported on macOS and Windows"}
    return {"ok": not errors, "task_id": task_id, "errors": errors}
