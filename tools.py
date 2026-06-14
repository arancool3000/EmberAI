"""System control tools for Ember on macOS.

Same function signatures as the Windows version so agent/ui/browser/etc. work unchanged.
Uses AppleScript + shell + osascript so no Windows-only libraries are required.

Permissions Ember needs (System Settings -> Privacy & Security):
  - Screen Recording  (mss screenshots)
  - Accessibility     (mouse/keyboard control, find_ui_elements)
  - Input Monitoring  (global hotkey via pynput)
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import mss
import pyautogui
import pyperclip
from PIL import Image, ImageDraw, ImageFont

import browser as _browser_mod

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05
_psutil = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _data_dir() -> Path:
    """Writable runtime dir. In a frozen app, never write inside the bundle (it breaks the
    code signature -> slow relaunch / read-only /Applications); use the OS user-data dir."""
    if not getattr(sys, "frozen", False):
        return _base_dir()
    home = Path.home()
    if sys.platform == "darwin":
        d = home / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = home / "AppData" / "Roaming" / "Ember"
    else:
        d = home / ".ember"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


SCREENSHOT_DIR = _data_dir() / "screenshots"
try:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _ensure_screenshot_dir() -> Path:
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return SCREENSHOT_DIR


def _osa(script: str, timeout: int = 8) -> tuple[bool, str]:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _sh(cmd, timeout: int = 30) -> dict:
    try:
        if isinstance(cmd, str):
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "returncode": r.returncode,
                "stdout": (r.stdout or "")[:8000], "stderr": (r.stderr or "")[:4000]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _load_font(size: int):
    for name in ("Helvetica.ttc", "Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_grid(img, minor=50, major=250, label_step=100):
    w, h = img.size
    out = img.convert("RGB").copy()
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for x in range(minor, w, minor):
        d.line([(x, 0), (x, h)], fill=(0, 220, 255, 55), width=1)
    for y in range(minor, h, minor):
        d.line([(0, y), (w, y)], fill=(0, 220, 255, 55), width=1)
    for x in range(major, w, major):
        d.line([(x, 0), (x, h)], fill=(255, 220, 0, 130), width=2)
    for y in range(major, h, major):
        d.line([(0, y), (w, y)], fill=(255, 220, 0, 130), width=2)
    f = _load_font(11)
    for x in range(label_step, w, label_step):
        for y in range(label_step, h, label_step):
            text = f"{x},{y}"
            bbox = d.textbbox((x + 3, y + 3), text, font=f)
            d.rectangle(bbox, fill=(0, 0, 0, 170))
            d.text((x + 3, y + 3), text, fill=(180, 255, 180, 255), font=f)
    out.paste(overlay, (0, 0), overlay)
    return out


def _draw_cursor(img, x, y):
    out = img.convert("RGB").copy()
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for r, a in ((24, 90), (16, 140), (10, 220)):
        d.ellipse([(x - r, y - r), (x + r, y + r)], outline=(255, 40, 40, a), width=2)
    out.paste(overlay, (0, 0), overlay)
    return out


def take_screenshot(region=None, grid=True, show_cursor=True):
    cursor_x, cursor_y = pyautogui.position()
    with mss.mss() as sct:
        monitor = sct.monitors[1] if not region else {
            "left": int(region["x"]), "top": int(region["y"]),
            "width": int(region["width"]), "height": int(region["height"]),
        }
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    # mss grabs PHYSICAL pixels, but cursor_x/y and the coords the model clicks with are
    # LOGICAL points. On a Retina display they differ ~2x, which would draw the cursor ring
    # and every grid label at the wrong place. Downscale to logical points so 1px == 1
    # click-point. (No-op on non-Retina displays where the scale is 1.0.)
    if not region:
        try:
            import screen_vision
            sx, sy, _ = screen_vision._scale()
            if sx and sy and (abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01):
                img = img.resize((max(1, round(img.width / sx)), max(1, round(img.height / sy))),
                                 Image.BILINEAR)
        except Exception:
            pass
    original_w, original_h = img.size
    if show_cursor and not region:
        img = _draw_cursor(img, cursor_x, cursor_y)
    if grid:
        img = _draw_grid(img)
    max_dim = 640
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=65, optimize=False)
    data = buf.getvalue()
    path = _ensure_screenshot_dir() / f"shot_{int(time.time() * 1000)}.jpg"
    try:
        path.write_bytes(data)
    except (FileNotFoundError, OSError):
        import tempfile
        path = Path(tempfile.gettempdir()) / f"ember_shot_{int(time.time() * 1000)}.jpg"
        path.write_bytes(data)
    return {
        "ok": True, "width": img.size[0], "height": img.size[1],
        "original_width": original_w, "original_height": original_h,
        "cursor_x": cursor_x, "cursor_y": cursor_y, "grid": grid,
        "path": str(path), "size_kb": round(len(data) / 1024, 1),
        "image_b64": base64.b64encode(data).decode("ascii"),
        "mime_type": "image/jpeg",
    }


def zoom_screenshot(x, y, radius=150):
    # x,y,radius are LOGICAL points (the click coordinate space); mss grabs in PHYSICAL
    # pixels, so convert via the measured scale (identity on non-Retina displays).
    try:
        import screen_vision
        sx, sy, full = screen_vision._scale()
    except Exception:
        with mss.mss() as sct:
            full = sct.monitors[1]
        sx = sy = 1.0
    px = full["left"] + int(x) * sx
    py = full["top"] + int(y) * sy
    rpx, rpy = radius * sx, radius * sy
    x0 = max(full["left"], int(px - rpx))
    y0 = max(full["top"], int(py - rpy))
    w = min(int(2 * rpx), full["left"] + full["width"] - x0)
    h = min(int(2 * rpy), full["top"] + full["height"] - y0)
    with mss.mss() as sct:
        raw = sct.grab({"left": x0, "top": y0, "width": w, "height": h})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    img = img.resize((img.size[0] * 2, img.size[1] * 2), Image.LANCZOS)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx, cy = int(px - x0) * 2, int(py - y0) * 2
    for r in (24, 16, 10):
        od.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 40, 40, 220), width=2)
    img = img.convert("RGBA"); img.paste(overlay, (0, 0), overlay); img = img.convert("RGB")
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=75)
    data = buf.getvalue()
    path = _ensure_screenshot_dir() / f"zoom_{int(time.time() * 1000)}.jpg"
    try:
        path.write_bytes(data)
    except OSError:
        pass
    return {"ok": True, "x": x, "y": y, "radius": radius,
            "width": img.size[0], "height": img.size[1],
            "path": str(path), "image_b64": base64.b64encode(data).decode("ascii"),
            "mime_type": "image/jpeg"}


def click(x, y, button="left", double=False):
    try:
        pyautogui.moveTo(int(x), int(y), duration=0.08)
        (pyautogui.doubleClick if double else pyautogui.click)(button=button)
        return {"ok": True, "action": f"{'double-' if double else ''}{button}-click at ({x},{y})"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def move_mouse(x, y, duration=0.2):
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return {"ok": True, "x": x, "y": y}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def drag(from_x, from_y, to_x, to_y, button="left", duration=0.4):
    try:
        pyautogui.moveTo(from_x, from_y, duration=0.15)
        pyautogui.dragTo(to_x, to_y, duration=duration, button=button)
        return {"ok": True, "from": [from_x, from_y], "to": [to_x, to_y]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def type_text(text, interval=0.02):
    try:
        pyautogui.typewrite(text, interval=interval)
        return {"ok": True, "typed_chars": len(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def paste_text(text):
    try:
        prev = None
        try: prev = pyperclip.paste()
        except Exception: pass
        pyperclip.copy(text); time.sleep(0.05)
        pyautogui.hotkey("command", "v"); time.sleep(0.1)
        if prev is not None:
            try: pyperclip.copy(prev)
            except Exception: pass
        return {"ok": True, "pasted_chars": len(text)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_MAC_KEY_MAP = {"win": "command", "windows": "command", "ctrl": "control", "meta": "command"}


def press_key(keys):
    try:
        parts = [_MAC_KEY_MAP.get(k.strip().lower(), k.strip().lower()) for k in keys.split("+")]
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(parts[0])
        return {"ok": True, "pressed": keys}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def scroll(direction, amount=3, x=None, y=None):
    try:
        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=0.1)
        clicks = amount * (1 if direction == "up" else -1)
        pyautogui.scroll(clicks * 100)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def mouse_position():
    x, y = pyautogui.position()
    return {"ok": True, "x": x, "y": y}


def get_screen_size():
    w, h = pyautogui.size()
    return {"ok": True, "width": w, "height": h}


def wait(seconds):
    time.sleep(max(0.0, min(30.0, float(seconds))))
    return {"ok": True, "waited": seconds}


def wait_for_screen_change(timeout=8.0, sample_interval=0.4, sensitivity=1.5):
    import hashlib
    def fp():
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[1])
            im = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            im.thumbnail((96, 54), Image.BILINEAR)
            return hashlib.md5(im.tobytes()).hexdigest()
    deadline = time.time() + min(60.0, max(0.5, timeout))
    initial = fp()
    while time.time() < deadline:
        time.sleep(sample_interval)
        if fp() != initial:
            return {"ok": True, "changed": True}
    return {"ok": True, "changed": False}


# ---------------------------------------------------------------------------
# Windows window / UI-automation backends — parity with the macOS AppleScript paths.
# Each is fully guarded: on any failure it returns {"ok": False, ...} so callers degrade to
# OCR-based clicking (which already works cross-platform). NOTE: written against the standard
# pywin32 / uiautomation APIs but only runnable on Windows — verify there.
# ---------------------------------------------------------------------------
def _win_list_windows() -> dict:
    try:
        import win32gui, win32process
    except Exception as e:
        return {"ok": False, "error": f"pywin32 not available: {e}"}
    windows = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w <= 1 or h <= 1:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            windows.append({"title": title[:120], "class": win32gui.GetClassName(hwnd),
                            "x": l, "y": t, "w": w, "h": h, "process_id": pid, "hwnd": hwnd})
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "window_count": len(windows), "windows": windows}


def _win_focus_window(title_contains: str) -> dict:
    try:
        import win32gui, win32con
    except Exception as e:
        return {"ok": False, "error": f"pywin32 not available: {e}"}
    t = title_contains.lower()
    for w in _win_list_windows().get("windows", []):
        if t in w["title"].lower():
            hwnd = w.get("hwnd")
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                try:
                    win32gui.BringWindowToTop(hwnd)
                except Exception:
                    pass
            return {"ok": True, "focused": w["title"]}
    return {"ok": False, "error": f"no window matching '{title_contains}'"}


def _win_collect_ui_elements(scope: str) -> dict:
    """Windows: walk the foreground window's UI Automation tree."""
    try:
        import uiautomation as auto
    except Exception as e:
        return {"ok": False, "error": f"uiautomation not available: {e}"}
    elements = []
    try:
        root = auto.GetForegroundControl() if scope == "foreground" else auto.GetRootControl()
        if root is None:
            return {"ok": False, "error": "no foreground window"}
        stack = [(root, 0)]
        visited = 0
        while stack and visited < 1500:
            ctrl, depth = stack.pop()
            visited += 1
            try:
                rect = ctrl.BoundingRectangle
                w, h = rect.width(), rect.height()
                name = ctrl.Name or ""
                ctype = ctrl.ControlTypeName or ""
                if w > 1 and h > 1 and (name or ctype):
                    elements.append({"name": name[:120], "type": ctype,
                                     "class": ctrl.ClassName or ctype,
                                     "x": rect.left + w // 2, "y": rect.top + h // 2,
                                     "w": w, "h": h})
            except Exception:
                pass
            if depth < 14:
                try:
                    for child in ctrl.GetChildren():
                        stack.append((child, depth + 1))
                except Exception:
                    pass
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "elements": elements}


def _rank_ui_elements(elements, filter_text, scope, max_results):
    """Shared fuzzy filter/rank for the macOS + Windows element collectors."""
    if filter_text:
        try:
            from rapidfuzz import fuzz
            ft = filter_text.lower()
            elements = sorted(
                [e for e in elements if fuzz.partial_ratio(ft, e["name"].lower()) >= 50
                 or fuzz.partial_ratio(ft, e["type"].lower()) >= 50],
                key=lambda e: -fuzz.partial_ratio(ft, e["name"].lower()),
            )
        except ImportError:
            ft = filter_text.lower()
            elements = [e for e in elements if ft in e["name"].lower() or ft in e["type"].lower()]
    return {"ok": True, "scope": scope, "element_count": len(elements), "elements": elements[:max_results]}


def _mac_collect_ui_elements(scope: str) -> dict:
    """macOS: enumerate the front window's accessibility tree via System Events."""
    script = '''
    set out to ""
    tell application "System Events"
        try
            set frontApp to first application process whose frontmost is true
            tell frontApp
                tell front window
                    set uiElems to entire contents
                    repeat with el in uiElems
                        try
                            set elRole to role of el as text
                            set elName to ""
                            try
                                set elName to (name of el as text)
                            end try
                            if elName is "" then
                                try
                                    set elName to (description of el as text)
                                end try
                            end if
                            set elPos to position of el
                            set elSize to size of el
                            set out to out & elRole & "|" & elName & "|" & (item 1 of elPos as text) & "|" & (item 2 of elPos as text) & "|" & (item 1 of elSize as text) & "|" & (item 2 of elSize as text) & linefeed
                        end try
                    end repeat
                end tell
            end tell
        end try
    end tell
    return out
    '''
    ok, output = _osa(script, timeout=8)
    if not ok or not output:
        return {"ok": False, "error": "could not query UI (grant Accessibility permission)"}
    elements = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) >= 6:
            try:
                role, name = parts[0], parts[1]
                px, py = int(parts[2]), int(parts[3])
                sw, sh = int(parts[4]), int(parts[5])
                if sw > 1 and sh > 1 and (name or role):
                    elements.append({
                        "name": name[:120], "type": role, "class": role,
                        "x": px + sw // 2, "y": py + sh // 2, "w": sw, "h": sh,
                    })
            except ValueError:
                continue
    return {"ok": True, "elements": elements}


def find_ui_elements(filter_text="", scope="foreground", max_results=80):
    """List clickable UI elements: accessibility tree on macOS, UI Automation on Windows."""
    if sys.platform.startswith("win"):
        res = _win_collect_ui_elements(scope)
    else:
        res = _mac_collect_ui_elements(scope)
    if not res.get("ok"):
        return res
    return _rank_ui_elements(res["elements"], filter_text, scope, max_results)


def click_element_by_text(text, scope="foreground", double=False, button="left"):
    for attempt in range(3):
        found = find_ui_elements(filter_text=text, scope=scope, max_results=5)
        els = (found.get("elements") or [])
        if els:
            best = els[0]
            res = click(best["x"], best["y"], double=double, button=button)
            res["matched"] = {"name": best["name"], "type": best["type"]}
            return res
        time.sleep(0.4)
    return {"ok": False, "error": f"no UI element matching '{text}' after 3 attempts"}


def right_click_element_by_text(text, scope="foreground"):
    res = click_element_by_text(text, scope=scope, button="right")
    if res.get("ok"):
        time.sleep(0.3)
    return res


def list_windows():
    if sys.platform.startswith("win"):
        return _win_list_windows()
    script = '''
    set out to ""
    tell application "System Events"
        repeat with proc in (every application process whose visible is true)
            try
                set procName to name of proc
                repeat with w in (every window of proc)
                    try
                        set wn to name of w
                        set wp to position of w
                        set ws to size of w
                        set out to out & procName & "|" & wn & "|" & (item 1 of wp as text) & "|" & (item 2 of wp as text) & "|" & (item 1 of ws as text) & "|" & (item 2 of ws as text) & "|" & (unix id of proc as text) & linefeed
                    end try
                end repeat
            end try
        end repeat
    end tell
    return out
    '''
    ok, output = _osa(script, timeout=8)
    if not ok:
        return {"ok": False, "error": output}
    windows = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) >= 7:
            try:
                windows.append({
                    "title": parts[1][:120], "class": parts[0],
                    "x": int(parts[2]), "y": int(parts[3]),
                    "w": int(parts[4]), "h": int(parts[5]),
                    "process_id": int(parts[6]),
                })
            except ValueError:
                continue
    return {"ok": True, "window_count": len(windows), "windows": windows}


def focus_window(title_contains):
    if sys.platform.startswith("win"):
        return _win_focus_window(title_contains)
    t = title_contains.lower()
    for w in list_windows().get("windows", []):
        if t in w["title"].lower():
            _osa(f'tell application "{w["class"]}" to activate', timeout=3)
            return {"ok": True, "focused": w["title"]}
    return {"ok": False, "error": f"no window matching '{title_contains}'"}


def capture_window(title_contains, grid=True):
    t = title_contains.lower()
    for w in list_windows().get("windows", []):
        if t in w["title"].lower():
            return take_screenshot(region={"x": w["x"], "y": w["y"], "width": w["w"], "height": w["h"]},
                                   grid=grid, show_cursor=False)
    return {"ok": False, "error": f"no window matching '{title_contains}'"}


def run_powershell(command, timeout=60):
    """OS-aware shell: PowerShell on Windows, /bin/zsh on macOS."""
    if sys.platform.startswith("win"):
        return _sh(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], timeout=timeout)
    return _sh(["/bin/zsh", "-c", command], timeout=timeout)


def run_cmd(command, timeout=60):
    return _sh(command, timeout=timeout)


def read_file(path, max_bytes=200_000):
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return {"ok": False, "error": "not found"}
        if p.stat().st_size > max_bytes:
            return {"ok": True, "content": p.read_bytes()[:max_bytes].decode("utf-8", errors="replace"),
                    "truncated": True, "size": p.stat().st_size}
        return {"ok": True, "content": p.read_text(encoding="utf-8", errors="replace"), "truncated": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def write_file(path, content):
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "bytes_written": len(content), "path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_directory(path, pattern="*"):
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return {"ok": False, "error": "not found"}
        items = list(p.glob(pattern)) if pattern != "*" else list(p.iterdir())
        return {"ok": True, "path": str(p),
                "entries": [{"name": x.name, "type": "dir" if x.is_dir() else "file",
                              "size": x.stat().st_size if x.is_file() else None}
                             for x in sorted(items)[:300]]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _overlooked_search_dirs(root_expanded: str) -> list[Path]:
    home = Path.home()
    root_path = Path(root_expanded).expanduser()
    dirs = []
    try:
        broad_home = root_path.resolve() in {home.resolve(), Path(str(home)).resolve()}
    except Exception:
        broad_home = str(root_path) in {"~", str(home)}
    if broad_home:
        dirs += [
            home / "Desktop",
            home / "Downloads",
            home / "Documents",
            home / "Pictures",
            home / "Movies",
            home / "Music",
            home / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
            home / ".Trash",
            Path("/Users/Shared"),
        ]
        for name in ("OneDrive", "Dropbox", "Google Drive"):
            d = home / name
            if d.exists():
                dirs.append(d)
    else:
        dirs.append(root_path)
    seen = set()
    out = []
    for d in dirs:
        try:
            key = str(d.expanduser().resolve())
        except Exception:
            key = str(d)
        if key not in seen and d.exists() and d.is_dir():
            seen.add(key)
            out.append(d)
    return out


def _manual_file_name_search(query: str, roots: list[Path], max_results: int, existing: set[str]) -> tuple[list[dict], list[str]]:
    q = query.lower().strip("* ")
    matches = []
    checked = []
    visited = 0
    for root in roots:
        checked.append(str(root))
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Keep this fallback responsive; Spotlight already handled the broad indexed search.
                visited += len(filenames)
                if visited > 45000 or len(matches) >= max_results:
                    return matches, checked
                # Skip huge caches unless they are the Trash itself.
                if ".Trash" not in str(root):
                    dirnames[:] = [d for d in dirnames if d not in {"Library", "node_modules", ".git", "__pycache__"}]
                for name in filenames:
                    if q and q not in name.lower():
                        continue
                    path = str(Path(dirpath) / name)
                    if path in existing:
                        continue
                    existing.add(path)
                    pp = Path(path)
                    matches.append({
                        "FullName": path,
                        "Type": "file",
                        "SizeKB": round(pp.stat().st_size / 1024, 1) if pp.exists() else None,
                        "OverlookedPlace": str(root),
                    })
                    if len(matches) >= max_results:
                        return matches, checked
        except Exception:
            continue
    return matches, checked


def search_files(query, root="~", max_results=30, include_overlooked=True):
    """Use Spotlight first, then check common overlooked places like Trash/iCloud."""
    root_expanded = os.path.expanduser(os.path.expandvars(root))
    r = _sh(["mdfind", "-name", query, "-onlyin", root_expanded], timeout=20)
    if not r["ok"]:
        paths = []
    else:
        paths = (r["stdout"] or "").splitlines()[:max_results]
    matches = []
    seen = set()
    for p_str in paths:
        try:
            pp = Path(p_str)
            if pp.exists():
                seen.add(str(pp))
                matches.append({"FullName": str(pp), "Type": "dir" if pp.is_dir() else "file",
                                 "SizeKB": round(pp.stat().st_size / 1024, 1) if pp.is_file() else None})
        except Exception:
            continue
    checked = []
    if include_overlooked and len(matches) < max_results:
        more, checked = _manual_file_name_search(
            query,
            _overlooked_search_dirs(root_expanded),
            max_results - len(matches),
            seen,
        )
        matches.extend(more)
    return {
        "ok": True,
        "match_count": len(matches),
        "matches": matches[:max_results],
        "overlooked_checked": checked,
    }


def open_url(url):
    try: subprocess.Popen(["open", url]); return {"ok": True, "url": url}
    except Exception as e: return {"ok": False, "error": str(e)}


def open_app(name):
    try: subprocess.Popen(["open", "-a", name]); return {"ok": True, "launched": name}
    except Exception as e: return {"ok": False, "error": str(e)}


def open_path(path):
    try:
        subprocess.Popen(["open", os.path.expanduser(os.path.expandvars(path))])
        return {"ok": True, "opened": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_event_logs(log_name="System", hours=24, level="Error,Critical"):
    levels = [l.strip() for l in level.split(",") if l.strip()]
    pred = ('(messageType == "error" OR messageType == "fault")'
            if ("Error" in levels or "Critical" in levels)
            else 'messageType == "default"')
    r = _sh(["log", "show", "--style", "compact", "--last", f"{int(hours)}h",
             "--predicate", pred], timeout=45)
    if not r["ok"]: return r
    lines = [l for l in (r["stdout"] or "").splitlines() if l.strip()][:80]
    return {"ok": True, "event_count": len(lines), "events": [{"Message": l[:400]} for l in lines]}


def get_reliability_events(days=7):
    out = []
    for d in (Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")):
        if d.exists():
            cutoff = time.time() - days * 86400
            for f in sorted(d.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
                try:
                    if f.stat().st_mtime > cutoff and f.suffix in (".crash", ".ips", ".panic", ".diag", ".spin"):
                        out.append({
                            "TimeGenerated": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
                            "SourceName": f.name.split("-")[0],
                            "ProductName": f.stem, "Path": str(f),
                        })
                except Exception:
                    continue
    return {"ok": True, "event_count": len(out), "events": out}


def get_minidumps():
    out = []
    for d in (Path.home() / "Library/Logs/DiagnosticReports", Path("/Library/Logs/DiagnosticReports")):
        if d.exists():
            for f in sorted(d.glob("*.ips"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
                out.append({"path": str(f), "size_kb": round(f.stat().st_size / 1024, 1),
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))})
    return {"ok": True, "dump_count": len(out), "dumps": out}


def get_system_info():
    try:
        info = {}
        sw = _sh(["sw_vers"], timeout=5)
        if sw["ok"]:
            for line in (sw["stdout"] or "").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
        hw = _sh(["system_profiler", "SPHardwareDataType", "-json"], timeout=15)
        if hw["ok"]:
            try:
                data = json.loads(hw["stdout"])
                items = data.get("SPHardwareDataType", [{}])
                if items:
                    hd = items[0]
                    info["Chip"] = hd.get("chip_type") or hd.get("cpu_type")
                    info["Cores"] = hd.get("number_processors")
                    info["RAM"] = hd.get("physical_memory")
                    info["Model"] = hd.get("machine_model")
            except json.JSONDecodeError:
                pass
        up = _sh(["uptime"], timeout=3)
        if up["ok"]:
            info["Uptime"] = (up["stdout"] or "").strip()
        return {"ok": True, "info": info}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_installed_drivers(filter_text=""):
    drivers = []
    r = _sh(["kextstat", "-l"], timeout=10)
    if r["ok"]:
        for line in (r["stdout"] or "").splitlines()[:200]:
            parts = line.split()
            if len(parts) >= 6:
                drivers.append({"DeviceName": parts[-2], "DriverProviderName": "kext",
                                 "DriverVersion": parts[-1].strip("()")})
    if filter_text:
        ft = filter_text.lower()
        drivers = [d for d in drivers if ft in d.get("DeviceName", "").lower()]
    return {"ok": True, "driver_count": len(drivers), "drivers": drivers[:100]}


def get_running_processes(filter_text=""):
    global _psutil
    try:
        if _psutil is None:
            import psutil; _psutil = psutil
        out = []
        for p in _psutil.process_iter(attrs=["pid", "name", "memory_info"]):
            try:
                ram = p.info["memory_info"].rss / (1024 * 1024)
                if ram > 50:
                    out.append({"ProcessName": p.info["name"], "Id": p.info["pid"], "RAM_MB": round(ram, 1)})
            except Exception:
                continue
        out.sort(key=lambda x: -x["RAM_MB"])
        if filter_text:
            ft = filter_text.lower()
            out = [p for p in out if ft in p["ProcessName"].lower()]
        return {"ok": True, "process_count": len(out), "processes": out[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_performance():
    global _psutil
    try:
        if _psutil is None:
            import psutil; _psutil = psutil
        ps = _psutil
        cpu = ps.cpu_percent(interval=0.4)
        vm = ps.virtual_memory()
        disks = []
        for d in ps.disk_partitions(all=False):
            try:
                u = ps.disk_usage(d.mountpoint)
                disks.append({"drive": d.device, "free_gb": round(u.free / 1e9, 1),
                               "total_gb": round(u.total / 1e9, 1), "pct_used": u.percent})
            except Exception:
                continue
        net = ps.net_io_counters()
        return {"ok": True, "cpu_pct": cpu, "ram_pct": vm.percent,
                "ram_used_gb": round(vm.used / 1e9, 1), "ram_total_gb": round(vm.total / 1e9, 1),
                "disks": disks,
                "net_sent_mb": round(net.bytes_sent / 1e6, 1),
                "net_recv_mb": round(net.bytes_recv / 1e6, 1),
                "boot_time": time.strftime("%Y-%m-%d %H:%M", time.localtime(ps.boot_time()))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_windows_updates(days=30):
    r = _sh(["softwareupdate", "-l"], timeout=60)
    if not r["ok"]: return r
    updates = []
    for line in (r["stdout"] or "").splitlines():
        line = line.strip()
        if line.startswith("* Label:"):
            updates.append({"Title": line.replace("* Label:", "").strip()})
        elif line.startswith("Title:"):
            updates.append({"Title": line.replace("Title:", "").split(",")[0].strip()})
    return {"ok": True, "update_count": len(updates), "updates": updates}


QUICK_FIXES = {
    "flush_dns": ("sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder", "Flush DNS cache"),
    "restart_finder": ("killall Finder", "Restart Finder"),
    "restart_dock": ("killall Dock", "Restart the Dock"),
    "restart_menubar": ("killall SystemUIServer", "Restart the menu-bar process"),
    "empty_trash": ('osascript -e \'tell application "Finder" to empty trash\'', "Empty the Trash"),
    "verify_disk": ("diskutil verifyVolume /", "Verify the boot volume"),
    "release_renew_ip": ("sudo ipconfig set en0 BOOTP && sudo ipconfig set en0 DHCP", "Release/renew DHCP"),
    "show_startup": ('osascript -e \'tell application "System Events" to get the name of every login item\'', "List login items"),
    "show_services": ("launchctl list | head -50", "List loaded launch services"),
}


def quick_fix(name):
    if name not in QUICK_FIXES:
        return {"ok": False, "error": f"unknown fix '{name}'", "available": list(QUICK_FIXES.keys())}
    cmd, desc = QUICK_FIXES[name]
    res = _sh(cmd, timeout=120)
    res["fix_name"] = name; res["description"] = desc
    return res


def list_quick_fixes():
    return {"ok": True, "fixes": [{"name": k, "description": v[1]} for k, v in QUICK_FIXES.items()]}


# ---------- Browser tools (cross-platform CDP - same browser.py) ----------
def _br(): return _browser_mod.get_browser()
def browser_open(url="about:blank"):
    br = _br(); a = br.attach()
    if not a.get("ok"): return a
    if url and url != "about:blank":
        n = br.navigate(url)
        if not n.get("ok"): return n
        br.wait_for_load(timeout=10)
    return {"ok": True, "url": br.get_url(), "title": br.get_title()}
def browser_get_page(visible_only=True, max_items=80):
    return _br().get_dom_summary(max_items=max_items, visible_only=visible_only)
def browser_click_text(text, mode="left"): return _br().click_by_text(text, mode=mode)
def browser_click_selector(selector, mode="left"): return _br().click_selector(selector, mode=mode)
def browser_fill(selector, value): return _br().fill(selector, value)
def browser_navigate(url, wait=True):
    br = _br(); r = br.navigate(url)
    if wait: br.wait_for_load(timeout=10)
    return {**r, "url": br.get_url(), "title": br.get_title()}
def browser_scroll(direction="down", pixels=800): return _br().scroll(direction=direction, pixels=pixels)
def browser_back(): return _br().go_back()
def browser_forward(): return _br().go_forward()
def browser_reload(): return _br().reload()
def browser_dismiss_cookies(mode="accept"): return _br().dismiss_cookies(mode=mode)
def browser_check_captcha(): return _br().check_captcha()
def browser_new_tab(url="about:blank"): return _br().new_tab(url)
def browser_switch_tab(tab_id): return _br().switch_tab(tab_id)
def browser_list_tabs():
    try:
        tabs = _br().list_tabs()
        return {"ok": True, "tab_count": len(tabs),
                "tabs": [{"id": t["id"], "url": t.get("url"), "title": t.get("title")} for t in tabs]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
def browser_close_tab(tab_id=None): return _br().close_tab(tab_id=tab_id)
def browser_screenshot(): return _br().screenshot()
def browser_evaluate(expression):
    try:
        return {"ok": True, "value": _br().evaluate(expression)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
def browser_current():
    br = _br(); return {"ok": True, "url": br.get_url(), "title": br.get_title()}


# ---------- Sequence batching ----------
def _to_plain(obj):
    if isinstance(obj, (str, int, float, bool, type(None))): return obj
    if isinstance(obj, dict): return {k: _to_plain(v) for k, v in obj.items()}
    if hasattr(obj, "items") and callable(getattr(obj, "items", None)):
        try: return {str(k): _to_plain(v) for k, v in obj.items()}
        except Exception: pass
    if isinstance(obj, (list, tuple)): return [_to_plain(x) for x in obj]
    if hasattr(obj, "__iter__") and not isinstance(obj, (bytes, bytearray)):
        try: return [_to_plain(x) for x in obj]
        except TypeError: pass
    return obj


def do_sequence(actions=None):
    import json as _json
    results = []
    from agent import TOOL_DISPATCH
    actions = _to_plain(actions)
    if isinstance(actions, str):
        try: actions = _json.loads(actions)
        except Exception: return {"ok": False, "error": "actions string wasn't valid JSON"}
    if not isinstance(actions, list):
        return {"ok": False, "error": f"actions must be a list"}
    for i, action in enumerate(actions):
        action = _to_plain(action)
        if not isinstance(action, dict):
            results.append({"step": i, "ok": False, "error": "action must be an object"}); continue
        tool_name = action.get("tool")
        args = _to_plain(action.get("args", {})) or {}
        if not isinstance(args, dict): args = {}
        cont = bool(action.get("continue_on_error", False))
        if tool_name == "do_sequence":
            # Refuse self-nesting — TOOL_DISPATCH contains do_sequence, so a self-referential
            # payload would recurse without bound (and leak nested screenshots).
            results.append({"step": i, "tool": tool_name, "ok": False,
                            "error": "do_sequence cannot be nested"})
            if not cont: break
            continue
        fn = TOOL_DISPATCH.get(tool_name)
        if not fn:
            results.append({"step": i, "tool": tool_name, "ok": False, "error": f"unknown tool"})
            if not cont: break
            continue
        try: r = fn(**args)
        except TypeError as e: r = {"ok": False, "error": f"bad args: {e}"}
        except Exception as e: r = {"ok": False, "error": str(e)}
        compact = {k: v for k, v in r.items() if k != "image_b64"}
        results.append({"step": i, "tool": tool_name, **compact})
        if not r.get("ok") and not cont: break
    return {"ok": all(r.get("ok") for r in results), "step_count": len(results), "steps": results}
