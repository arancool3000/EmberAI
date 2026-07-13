"""Bonus capabilities pile #2 - things a power user expects an AI agent to do:
window management, OCR, media keys, notifications, process control, dictionary,
currency, regex search across files, watch folders, screen recording, etc."""
from __future__ import annotations

import ctypes
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path
from typing import Any

import requests

_IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Window management (Windows-only Win32 API)
# ---------------------------------------------------------------------------
def _get_active_hwnd() -> int:
    user32 = ctypes.windll.user32
    return int(user32.GetForegroundWindow())


def snap_window(direction: str = "left") -> dict:
    """Snap the currently focused window left/right/maximize/restore (Win+arrow)."""
    if _IS_MAC:
        return {"ok": False, "error": "snap_window is not supported on macOS "
                "(no built-in window-snap shortcut); use move_window or a tiling app"}
    import pyautogui
    mapping = {
        "left": ("win", "left"),
        "right": ("win", "right"),
        "maximize": ("win", "up"),
        "up": ("win", "up"),
        "restore": ("win", "down"),
        "down": ("win", "down"),
    }
    keys = mapping.get(direction.lower())
    if not keys:
        return {"ok": False, "error": f"unknown direction; pick from {list(mapping)}"}
    pyautogui.hotkey(*keys)
    return {"ok": True, "direction": direction}


def move_window(title_contains: str, x: int, y: int, w: int = -1, h: int = -1) -> dict:
    """Move (and optionally resize) the first visible window matching title_contains."""
    try:
        import uiautomation as ua
        root = ua.GetRootControl()
        t = title_contains.lower()
        for win in root.GetChildren():
            try:
                if t in (win.Name or "").lower():
                    rect = win.BoundingRectangle
                    new_w = w if w > 0 else rect.width()
                    new_h = h if h > 0 else rect.height()
                    ctypes.windll.user32.MoveWindow(
                        win.NativeWindowHandle, int(x), int(y), int(new_w), int(new_h), True,
                    )
                    return {"ok": True, "moved": win.Name, "to": [x, y], "size": [new_w, new_h]}
            except Exception:
                continue
        return {"ok": False, "error": f"no window matching '{title_contains}'"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def minimize_all_other_windows() -> dict:
    """Hide every app except the foreground one (Win+Home on Windows, Cmd+Opt+H on macOS)."""
    import pyautogui
    if _IS_MAC:
        pyautogui.hotkey("command", "option", "h")  # macOS "Hide Others"
        return {"ok": True, "action": "hide others"}
    pyautogui.hotkey("win", "home")
    return {"ok": True}


def list_monitors() -> dict:
    """Multi-monitor enumeration."""
    try:
        import mss
        with mss.mss() as sct:
            mons = []
            for i, m in enumerate(sct.monitors):
                # monitors[0] is the "all-screens" virtual monitor; 1+ are real screens
                mons.append({
                    "index": i,
                    "left": m["left"], "top": m["top"],
                    "width": m["width"], "height": m["height"],
                    "is_virtual": i == 0,
                })
        return {"ok": True, "monitor_count": len(mons) - 1, "monitors": mons}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def show_desktop() -> dict:
    """Show the desktop (Win+D on Windows; macOS uses Mission Control's F11 / hot corner,
    which isn't reliably scriptable, so we report not-supported rather than mis-fire)."""
    if _IS_MAC:
        return {"ok": False, "error": "show_desktop is not reliably scriptable on macOS; "
                "use Mission Control (F11) or hide apps with minimize_all_other_windows"}
    import pyautogui
    pyautogui.hotkey("win", "d")
    return {"ok": True}


def switch_window() -> dict:
    """Switch to the previous window/app (Alt+Tab on Windows, Cmd+Tab on macOS)."""
    import pyautogui
    if _IS_MAC:
        pyautogui.hotkey("command", "tab")
        return {"ok": True, "action": "cmd+tab"}
    pyautogui.hotkey("alt", "tab")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Media keys (Spotify, Chrome media, YouTube etc.)
# ---------------------------------------------------------------------------
def media_keys(action: str) -> dict:
    """Send media-key virtual keystrokes. action = play_pause | next | prev | stop | vol_up | vol_down | mute"""
    VK = {
        "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
        "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD,
    }
    if action not in VK:
        return {"ok": False, "error": f"unknown media action; pick from {list(VK)}"}
    if _IS_MAC:
        # System-wide media keys aren't directly injectable on macOS. Volume actions map
        # cleanly to AppleScript; transport keys are best handled per-app (e.g. Spotify).
        import more_tools
        if action == "mute":
            return more_tools.toggle_mute()
        if action in ("vol_up", "vol_down"):
            cur = more_tools.get_volume().get("percent", 50) or 50
            return more_tools.set_volume(cur + (10 if action == "vol_up" else -10))
        return {"ok": False, "error": f"'{action}' transport key not supported on macOS; "
                "control the media app directly (e.g. tell Spotify to playpause)"}
    code = VK[action]
    try:
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        return {"ok": True, "action": action}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Toast notifications
# ---------------------------------------------------------------------------
def show_notification(title: str, body: str = "") -> dict:
    """Native desktop notification (Windows toast / macOS Notification Center)."""
    if _IS_MAC:
        try:
            # Escape double quotes for the AppleScript string literals.
            t = (title or "").replace('"', '\\"')
            b = (body or "").replace('"', '\\"')
            subprocess.run(["osascript", "-e",
                            f'display notification "{b}" with title "{t}"'],
                           capture_output=True, timeout=8)
            return {"ok": True, "title": title}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    try:
        # Use PowerShell + BurntToast-style XML if available, else fallback to message box.
        ps = f'''
$ErrorActionPreference="SilentlyContinue"
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$xml = $template.GetXml()
$nodes = $xml.GetElementsByTagName("text")
$nodes.Item(0).AppendChild($xml.CreateTextNode({title!r})) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode({body!r})) | Out-Null
$xml2 = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml2.LoadXml($xml.OuterXml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml2
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Ember").Show($toast)
'''
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=8)
        return {"ok": True, "title": title}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------
def kill_process(name_or_pid: str) -> dict:
    """Kill a process by name (e.g. 'chrome.exe') or PID."""
    try:
        import psutil
        killed = []
        if name_or_pid.isdigit():
            p = psutil.Process(int(name_or_pid))
            p.kill()
            killed.append({"pid": p.pid, "name": p.name()})
        else:
            target = name_or_pid.lower()
            for p in psutil.process_iter(["name", "pid"]):
                try:
                    if (p.info["name"] or "").lower() == target:
                        p.kill()
                        killed.append({"pid": p.info["pid"], "name": p.info["name"]})
                except Exception:
                    continue
        return {"ok": True, "killed_count": len(killed), "killed": killed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def service_action(name: str, action: str = "status") -> dict:
    """Windows service: action = status | start | stop | restart"""
    if action not in ("status", "start", "stop", "restart"):
        return {"ok": False, "error": "action must be status/start/stop/restart"}
    actions = {"status": "Get-Service", "start": "Start-Service",
               "stop": "Stop-Service", "restart": "Restart-Service"}
    cmd = f"{actions[action]} -Name '{name}' -ErrorAction Stop | Select-Object Name,Status,DisplayName | ConvertTo-Json -Compress"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=20)
        return {"ok": r.returncode == 0, "output": (r.stdout or r.stderr)[:2000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Search file contents (grep)
# ---------------------------------------------------------------------------
def grep_files(folder: str, pattern: str, file_glob: str = "*",
               regex: bool = False, max_results: int = 100, max_bytes_per_file: int = 1_000_000) -> dict:
    """Search file contents for a pattern. Returns matches with file path + line."""
    try:
        p = Path(folder).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": "folder not found"}
        matches = []
        compiled = re.compile(pattern) if regex else None
        for file in p.rglob(file_glob):
            if not file.is_file():
                continue
            try:
                if file.stat().st_size > max_bytes_per_file:
                    continue
                with file.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        hit = compiled.search(line) if compiled else (pattern in line)
                        if hit:
                            matches.append({
                                "file": str(file),
                                "line": lineno,
                                "text": line.strip()[:200],
                            })
                            if len(matches) >= max_results:
                                return {"ok": True, "match_count": len(matches),
                                        "matches": matches, "truncated": True}
            except Exception:
                continue
        return {"ok": True, "match_count": len(matches), "matches": matches}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def diff_files(path1: str, path2: str, max_lines: int = 200) -> dict:
    """Unified diff between two text files."""
    try:
        import difflib
        a = Path(path1).expanduser().read_text(encoding="utf-8", errors="replace").splitlines()
        b = Path(path2).expanduser().read_text(encoding="utf-8", errors="replace").splitlines()
        diff = list(difflib.unified_diff(a, b, fromfile=path1, tofile=path2, lineterm=""))
        return {"ok": True, "line_count": len(diff),
                "diff": "\n".join(diff[:max_lines])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def count_lines(path: str) -> dict:
    """Lines, words, characters of a text file."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "lines": text.count("\n") + 1,
                "words": len(text.split()), "characters": len(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Web extras
# ---------------------------------------------------------------------------
def speed_test() -> dict:
    """Quick HTTP-based download speed measurement (~10 MB sample from CloudFlare)."""
    try:
        url = "https://speed.cloudflare.com/__down?bytes=10000000"
        start = time.time()
        r = requests.get(url, timeout=30)
        elapsed = time.time() - start
        bytes_ = len(r.content)
        mbps = (bytes_ * 8) / elapsed / 1_000_000
        return {"ok": True, "downloaded_mb": round(bytes_ / 1_000_000, 2),
                "seconds": round(elapsed, 2), "mbps_down": round(mbps, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def define_word(word: str) -> dict:
    """Free dictionaryapi.dev lookup."""
    try:
        r = requests.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}",
            timeout=8,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        data = r.json()
        if not isinstance(data, list) or not data:
            return {"ok": False, "error": "not found"}
        first = data[0]
        meanings = []
        for m in first.get("meanings", [])[:3]:
            defs = []
            for d in m.get("definitions", [])[:2]:
                defs.append(d.get("definition"))
            meanings.append({"part_of_speech": m.get("partOfSpeech"), "definitions": defs})
        return {"ok": True, "word": first.get("word"), "phonetic": first.get("phonetic"),
                "meanings": meanings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def currency_convert(amount: float, from_currency: str = "USD", to_currency: str = "EUR") -> dict:
    """Free Frankfurter.app exchange rate (no API key)."""
    try:
        r = requests.get(
            f"https://api.frankfurter.app/latest",
            params={"amount": amount, "from": from_currency.upper(), "to": to_currency.upper()},
            timeout=8,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        d = r.json()
        rate = list(d.get("rates", {}).values())[0] if d.get("rates") else None
        return {"ok": True, "amount": amount, "from": from_currency, "to": to_currency,
                "converted": rate, "date": d.get("date")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stock_quote(symbol: str) -> dict:
    """Quick stock quote via Yahoo Finance public endpoint."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        data = r.json()
        chart = (data.get("chart") or {}).get("result") or []
        if not chart:
            return {"ok": False, "error": "symbol not found"}
        meta = chart[0].get("meta") or {}
        return {
            "ok": True, "symbol": meta.get("symbol"),
            "price": meta.get("regularMarketPrice"),
            "currency": meta.get("currency"),
            "previous_close": meta.get("previousClose"),
            "day_range": [meta.get("regularMarketDayLow"), meta.get("regularMarketDayHigh")],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def github_search_repos(query: str, max_results: int = 5) -> dict:
    """Search GitHub repos (public, no auth)."""
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "per_page": max_results},
            timeout=10,
        )
        data = r.json()
        items = []
        for it in (data.get("items") or [])[:max_results]:
            items.append({
                "name": it.get("full_name"),
                "stars": it.get("stargazers_count"),
                "url": it.get("html_url"),
                "description": (it.get("description") or "")[:200],
            })
        return {"ok": True, "result_count": len(items), "results": items}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Random / pick
# ---------------------------------------------------------------------------
def random_number(low: int = 1, high: int = 100) -> dict:
    return {"ok": True, "value": random.randint(low, high)}


def random_choice(options: list) -> dict:
    if not options:
        return {"ok": False, "error": "empty options list"}
    return {"ok": True, "picked": random.choice(options)}


def dice_roll(sides: int = 6, count: int = 1) -> dict:
    rolls = [random.randint(1, max(2, sides)) for _ in range(max(1, min(20, count)))]
    return {"ok": True, "rolls": rolls, "total": sum(rolls), "sides": sides}


def flip_coin(count: int = 1) -> dict:
    flips = [random.choice(("heads", "tails")) for _ in range(max(1, min(50, count)))]
    return {"ok": True, "flips": flips, "heads": flips.count("heads"), "tails": flips.count("tails")}


# ---------------------------------------------------------------------------
# OCR (uses Windows.Media.Ocr via PowerShell - built into Windows 10+)
# ---------------------------------------------------------------------------
def ocr_image(path: str, language: str = "en") -> dict:
    """Extract text from an image file. Cross-platform: Apple Vision on macOS,
    Windows.Media.Ocr on Windows. Delegates to screen_vision (single source of truth)."""
    try:
        import screen_vision
        langs = [language] if language and language != "en" else None
        return screen_vision.ocr_image_file(path, languages=langs)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ocr_screen(region: dict | None = None) -> dict:
    """Screenshot (optionally a logical-point region) and OCR it to plain text.
    For text WITH clickable coordinates, prefer read_screen_text."""
    try:
        import screen_vision
        return screen_vision.ocr_screen_region(region)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Audio: TTS-as-tool + simple recording
# ---------------------------------------------------------------------------
def say_text(text: str) -> dict:
    """Speak text aloud (independent of the 'speak responses' setting)."""
    try:
        import voice
        voice.speak(text)
        return {"ok": True, "spoken": text[:120]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def measure_mic_level(seconds: float = 1.5) -> dict:
    """How loud is the microphone right now? Records a short clip and reports a 0-100 level, RMS,
    peak, and dBFS — no ffmpeg needed. Honest about a missing device / permission."""
    try:
        import audio_level
        return audio_level.measure_level(seconds)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def record_audio(seconds: float = 5.0, path: str | None = None) -> dict:
    """Record mic input to a WAV file, using Ember's portable microphone backend
    (sounddevice by default; PyAudio if it's installed)."""
    try:
        import wave
        import audio_level
        RATE = audio_level.RATE
        CHUNK = audio_level.CHUNK
        WIDTH = audio_level.WIDTH
        dst = Path(path).expanduser() if path else Path.home() / "Downloads" / f"ember_recording_{int(time.time())}.wav"
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            # open_input_stream tries PyAudio first, then the sounddevice wheels that ship with
            # Ember — so recording works out of the box without a manual PyAudio/PortAudio build.
            stream = audio_level.open_input_stream()
        except Exception as e:
            msg = str(e).lower()
            py = sys.executable
            if "permission" in msg or "denied" in msg or "-50" in msg:
                return {"ok": False, "error": "microphone permission denied",
                        "fix": "Grant Ember microphone access in your OS privacy settings, then retry."}
            fix = f"\"{py}\" -m pip install sounddevice"
            if sys.platform == "darwin":
                fix += "  (or, for PyAudio: brew install portaudio && \"" + py + "\" -m pip install pyaudio)"
            elif sys.platform.startswith("linux"):
                fix += "  (or, for PyAudio: sudo apt-get install -y portaudio19-dev && \"" + py + "\" -m pip install pyaudio)"
            return {"ok": False, "error": "no microphone backend is installed in Ember's Python",
                    "interpreter": py, "fix": fix,
                    "note": "Install into THIS interpreter (sys.executable), not a different python."}
        frames = []
        try:
            for _ in range(int(RATE / CHUNK * max(0.5, min(60.0, float(seconds))))):
                frame = stream.read(CHUNK)
                if not frame:
                    break
                frames.append(frame)
        finally:
            try:
                stream.close()
            except Exception:
                pass
        with wave.open(str(dst), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(WIDTH)
            wf.setframerate(RATE)
            wf.writeframes(b"".join(frames))
        return {"ok": True, "path": str(dst), "seconds": seconds,
                "size_kb": round(dst.stat().st_size / 1024, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Browser additions
# ---------------------------------------------------------------------------
def browser_wait_for_element(selector: str, timeout: float = 8.0) -> dict:
    """Block until a CSS selector exists in the DOM (or timeout)."""
    try:
        import browser as _b
        br = _b.get_browser()
        deadline = time.time() + max(0.5, min(60.0, float(timeout)))
        while time.time() < deadline:
            val = br.evaluate(f"!!document.querySelector({sel_lit(selector)})")
            if val:
                return {"ok": True, "selector": selector,
                        "waited": round(time.time() - (deadline - timeout), 2)}
            time.sleep(0.25)
        return {"ok": False, "error": f"timeout waiting for {selector}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_get_text(selector: str) -> dict:
    """Read innerText from the first element matching the selector."""
    try:
        import browser as _b
        br = _b.get_browser()
        text = br.evaluate(
            f"(document.querySelector({sel_lit(selector)}) || {{}}).innerText || ''"
        )
        return {"ok": True, "selector": selector, "text": (text or "")[:5000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sel_lit(s: str) -> str:
    """JSON-string-literal escape for a CSS selector."""
    import json as _j
    return _j.dumps(s)


# ---------------------------------------------------------------------------
# Quality of life
# ---------------------------------------------------------------------------
def calculate_text_stats(text: str) -> dict:
    """Word/char/sentence count + reading time estimate."""
    chars = len(text)
    words = len(text.split())
    sentences = max(1, len(re.findall(r"[.!?]+\s", text + " ")))
    reading_min = round(words / 200.0, 1)
    return {"ok": True, "characters": chars, "words": words, "sentences": sentences,
            "estimated_reading_minutes": reading_min}


def text_summary_via_search(text: str) -> dict:
    """Hint - use translate_text or wikipedia_summary upstream instead. This is a stub
    that returns the first 2 sentences. Better summarization lives in the LLM."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return {"ok": True, "summary": " ".join(sentences[:2])[:500]}


# ---------------------------------------------------------------------------
# Find by content + watch folder
# ---------------------------------------------------------------------------
_folder_watchers: dict[str, dict] = {}


def watch_folder_start(path: str) -> dict:
    """Start watching a folder for new/changed files in the background."""
    p = str(Path(path).expanduser().resolve())
    if p in _folder_watchers:
        return {"ok": True, "already_watching": True, "path": p}
    state = {"baseline": {}, "events": [], "stop": threading.Event()}
    try:
        for item in Path(p).rglob("*"):
            if item.is_file():
                state["baseline"][str(item)] = item.stat().st_mtime
    except Exception as e:
        return {"ok": False, "error": str(e)}

    def _loop():
        while not state["stop"].is_set():
            try:
                seen = {}
                for item in Path(p).rglob("*"):
                    if item.is_file():
                        seen[str(item)] = item.stat().st_mtime
                # additions
                for k, v in seen.items():
                    if k not in state["baseline"]:
                        state["events"].append({"t": int(time.time()), "type": "added", "path": k})
                    elif state["baseline"][k] != v:
                        state["events"].append({"t": int(time.time()), "type": "modified", "path": k})
                # removals
                for k in state["baseline"]:
                    if k not in seen:
                        state["events"].append({"t": int(time.time()), "type": "removed", "path": k})
                state["baseline"] = seen
                state["events"] = state["events"][-200:]
            except Exception:
                pass
            state["stop"].wait(2.0)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    state["thread"] = t
    _folder_watchers[p] = state
    return {"ok": True, "watching": p, "baseline_files": len(state["baseline"])}


def watch_folder_events(path: str, clear: bool = False) -> dict:
    """Get events since last call (and optionally clear them)."""
    p = str(Path(path).expanduser().resolve())
    st = _folder_watchers.get(p)
    if not st:
        return {"ok": False, "error": "not watching that folder"}
    events = list(st["events"])
    if clear:
        st["events"] = []
    return {"ok": True, "event_count": len(events), "events": events}


def list_desktop_items() -> dict:
    """List icons / shortcuts / files sitting on the user's Desktop.
    Checks both the user's Desktop AND the public/all-users Desktop (where many app installers
    drop their shortcuts), plus the OneDrive\\Desktop if the user uses OneDrive's desktop sync."""
    items = []
    # Always include the real home Desktop (this is what made it return EMPTY on macOS —
    # it only looked at Windows %USERPROFILE%/%PUBLIC% env vars). Windows extras are gated.
    candidate_dirs = [Path.home() / "Desktop"]
    if sys.platform.startswith("win"):
        candidate_dirs.append(Path(os.path.expandvars("%PUBLIC%\\Desktop")))
        onedrive = os.environ.get("ONEDRIVE") or os.environ.get("OneDrive")
        if onedrive:
            candidate_dirs.append(Path(onedrive) / "Desktop")
    seen = set()
    for d in candidate_dirs:
        try:
            if not d.exists() or not d.is_dir():
                continue
            for item in sorted(d.iterdir()):
                key = item.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    suf = item.suffix.lower()
                    kind = ("shortcut" if suf == ".lnk"
                            else "url" if suf == ".url"
                            else "dir" if item.is_dir()
                            else "file")
                    rec = {
                        "name": item.name,
                        "type": kind,
                        "path": str(item),
                        "from": str(d),
                    }
                    if item.is_file():
                        try:
                            rec["size_kb"] = round(item.stat().st_size / 1024, 1)
                        except OSError:
                            pass
                    items.append(rec)
                except Exception:
                    continue
        except Exception:
            continue
    return {"ok": True, "item_count": len(items), "items": items,
            "searched_dirs": [str(d) for d in candidate_dirs if d.exists()]}


def desktop_overview(include_screenshot: bool = False) -> dict:
    """One-shot snapshot of the user's desktop state:
      - which apps / windows are open and where
      - the foreground (active) window
      - icons/shortcuts on the Desktop folder
      - screen resolution and how many monitors
    Optionally captures a screenshot too."""
    import tools
    overview: dict = {"ok": True}
    try:
        wins = tools.list_windows()
        overview["windows"] = wins.get("windows", [])
        overview["window_count"] = wins.get("window_count", 0)
    except Exception as e:
        overview["windows_error"] = str(e)
    try:
        import uiautomation as ua
        fg = ua.GetForegroundControl()
        if fg:
            overview["foreground_window"] = {
                "title": (fg.Name or "")[:120],
                "class": fg.ClassName or "",
                "type": fg.ControlTypeName or "",
            }
    except Exception:
        pass
    try:
        overview["screen_size"] = tools.get_screen_size()
    except Exception:
        pass
    try:
        mons = list_monitors()
        if mons.get("ok"):
            overview["monitor_count"] = mons.get("monitor_count", 1)
            overview["monitors"] = mons.get("monitors", [])
    except Exception:
        pass
    try:
        desk = list_desktop_items()
        if desk.get("ok"):
            overview["desktop_items"] = desk.get("items", [])
            overview["desktop_item_count"] = desk.get("item_count", 0)
    except Exception:
        pass
    if include_screenshot:
        try:
            shot = tools.take_screenshot()
            if shot.get("ok"):
                overview["screenshot_path"] = shot.get("path")
                overview["screen_w"] = shot.get("original_width")
                overview["screen_h"] = shot.get("original_height")
        except Exception:
            pass
    return overview


def watch_folder_stop(path: str) -> dict:
    p = str(Path(path).expanduser().resolve())
    st = _folder_watchers.pop(p, None)
    if not st:
        return {"ok": False, "error": "not watching"}
    st["stop"].set()
    return {"ok": True, "stopped": p}
