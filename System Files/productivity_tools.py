"""Productivity & utility tools for Ember.

A batch of small, high-value tools:
  - Snippet expander (pure, JSON-persisted): save short keywords that expand into longer text.
  - Email breach monitor: check whether an email appears in known data breaches, using the
    FREE, no-API-key XposedOrNot service (https://xposedornot.com).
  - Screen recorder: background-thread capture of the screen to mp4 (imageio-ffmpeg) or gif (Pillow).
  - Screen color picker: read the color of any on-screen pixel from a fresh full-screen capture.
  - Multi-monitor screenshot: capture a specific physical monitor by index.

Hard dependency: Python standard library + `requests`. The screen tools depend on the OPTIONAL
packages `mss` and `Pillow` (and, for mp4, `imageio` / `imageio-ffmpeg`); these are imported lazily
inside the relevant functions so the module imports cleanly even when they are absent.

Every tool returns {"ok": True, ...} or {"ok": False, "error": "..."} and never raises.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests


# ---------------------------------------------------------------------------
# Data dir helper (copied verbatim per the spec)
# ---------------------------------------------------------------------------
def _data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).parent
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


# Module-level so tests can monkeypatch it to a tmp path.
SNIPPETS_FILE = _data_dir() / "snippets.json"

_PREVIEW_LEN = 60


# ===========================================================================
# 1) SNIPPET EXPANDER  (pure, JSON-persisted)
# ===========================================================================
def _load_snippets() -> dict:
    try:
        p = Path(SNIPPETS_FILE)
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_snippets(snips: dict) -> None:
    p = Path(SNIPPETS_FILE)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    p.write_text(json.dumps(snips, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_keyword(keyword: str) -> str:
    # Allow callers to pass ";addr" or "addr"; store without the leading semicolon.
    return (keyword or "").strip().lstrip(";").strip()


def snippet_save(keyword: str, text: str) -> dict:
    """Save a reusable text snippet under a keyword (expand later with ;keyword)."""
    kw = _clean_keyword(keyword)
    if not kw:
        return {"ok": False, "error": "keyword is required"}
    if text is None or text == "":
        return {"ok": False, "error": "text is required"}
    try:
        snips = _load_snippets()
        snips[kw] = str(text)
        _save_snippets(snips)
        return {"ok": True, "keyword": kw}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snippet_list() -> dict:
    """List saved snippet keywords with a short preview of each body."""
    try:
        snips = _load_snippets()
        previews = {}
        for kw, body in snips.items():
            body = str(body)
            preview = body[:_PREVIEW_LEN] + ("..." if len(body) > _PREVIEW_LEN else "")
            previews[kw] = preview
        return {"ok": True, "snippets": previews, "count": len(previews)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snippet_get(keyword: str) -> dict:
    """Get the full text of a saved snippet by keyword."""
    kw = _clean_keyword(keyword)
    if not kw:
        return {"ok": False, "error": "keyword is required"}
    try:
        snips = _load_snippets()
        if kw not in snips:
            return {"ok": False, "error": f"no snippet named '{kw}'"}
        return {"ok": True, "keyword": kw, "text": str(snips[kw])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snippet_delete(keyword: str) -> dict:
    """Delete a saved snippet by keyword."""
    kw = _clean_keyword(keyword)
    if not kw:
        return {"ok": False, "error": "keyword is required"}
    try:
        snips = _load_snippets()
        existed = kw in snips
        if existed:
            del snips[kw]
            _save_snippets(snips)
        return {"ok": True, "keyword": kw, "deleted": existed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def snippet_expand(text: str) -> dict:
    """Expand every ;keyword token in the text into its saved snippet body."""
    src = text or ""
    try:
        snips = _load_snippets()
        if not snips:
            return {"ok": True, "result": src, "expansions": 0}
        count = 0
        # Match the longest keywords first so ;addr2 isn't eaten by ;addr.
        result = src
        for kw in sorted(snips.keys(), key=len, reverse=True):
            token = ";" + kw
            occurrences = result.count(token)
            if occurrences:
                result = result.replace(token, str(snips[kw]))
                count += occurrences
        return {"ok": True, "result": result, "expansions": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===========================================================================
# 2) EMAIL BREACH MONITOR  (free, no-key XposedOrNot API)
# ===========================================================================
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_breach_check(email: str) -> dict:
    """Check whether an email appears in known data breaches.

    Uses the FREE, no-API-key XposedOrNot service
    (GET https://api.xposedornot.com/v1/check-email/<email>). An HTTP 404 from that
    endpoint means the address was not found in any tracked breach.
    """
    addr = (email or "").strip()
    if not addr or not _EMAIL_RE.match(addr):
        return {"ok": False, "error": "invalid email address"}
    try:
        r = requests.get(
            "https://api.xposedornot.com/v1/check-email/" + quote(addr),
            timeout=10,
            headers={"User-Agent": "Ember/1.0"},
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # 404 => clean (not found in any breach).
    if r.status_code == 404:
        return {"ok": True, "email": addr, "breached": False, "breaches": [], "count": 0}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}"}

    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": "bad response from breach service"}

    # XposedOrNot returns {"breaches": [["BreachA", "BreachB", ...]]} on a hit,
    # or {"Error": "Not found"} for clean addresses.
    if isinstance(data, dict) and str(data.get("Error", "")).lower().startswith("not found"):
        return {"ok": True, "email": addr, "breached": False, "breaches": [], "count": 0}

    names: list[str] = []
    raw = data.get("breaches") if isinstance(data, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, list):
                names.extend(str(x) for x in item)
            elif isinstance(item, str):
                names.append(item)
    names = sorted(set(n for n in names if n))
    return {"ok": True, "email": addr, "breached": bool(names),
            "breaches": names, "count": len(names)}


# ===========================================================================
# 3) SCREEN RECORDER  (lazy mss + imageio/Pillow; background thread)
# ===========================================================================
_MAX_SECONDS = 120
_MAX_FRAMES = 2000  # hard cap to avoid runaway memory

_rec_lock = threading.Lock()
_rec_state: dict = {
    "recording": False,
    "thread": None,
    "stop": None,
    "frames": 0,
    "output": "",
    "started": 0.0,
    "message": "",
}


def _rec_default_output(use_gif: bool) -> Path:
    folder = _data_dir() / "recordings"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    ext = "gif" if use_gif else "mp4"
    return folder / f"recording_{int(time.time())}.{ext}"


def _record_loop(seconds: int, fps: int, output: Path, use_gif: bool,
                 stop_event: threading.Event, mss_mod, writer, pil_image):
    """Background capture loop. `writer` is an imageio writer (mp4) or None (gif via Pillow)."""
    interval = 1.0 / max(1, fps)
    deadline = time.time() + seconds
    gif_frames = []  # only used in the Pillow/gif path
    try:
        with mss_mod.mss() as sct:
            monitor = sct.monitors[0]  # full virtual screen
            while not stop_event.is_set() and time.time() < deadline:
                frame_start = time.time()
                shot = sct.grab(monitor)
                if use_gif:
                    img = pil_image.frombytes("RGB", shot.size, shot.rgb)
                    gif_frames.append(img)
                else:
                    # imageio expects an (H, W, 3) array; build via Pillow if available,
                    # otherwise hand imageio the raw RGB bytes through numpy-free Pillow.
                    img = pil_image.frombytes("RGB", shot.size, shot.rgb)
                    writer.append_data(_pil_to_array(img))
                with _rec_lock:
                    _rec_state["frames"] += 1
                if _rec_state["frames"] >= _MAX_FRAMES:
                    break
                # Pace to fps.
                sleep_for = interval - (time.time() - frame_start)
                if sleep_for > 0:
                    stop_event.wait(sleep_for)
        if use_gif:
            if gif_frames:
                ms = int(1000.0 / max(1, fps))
                gif_frames[0].save(
                    str(output), save_all=True, append_images=gif_frames[1:],
                    duration=ms, loop=0,
                )
            with _rec_lock:
                _rec_state["message"] = "saved gif"
        else:
            writer.close()
            with _rec_lock:
                _rec_state["message"] = "saved mp4"
    except Exception as e:
        with _rec_lock:
            _rec_state["message"] = f"recording error: {e}"
    finally:
        with _rec_lock:
            _rec_state["recording"] = False


def _pil_to_array(img):
    """Convert a Pillow image to something imageio.append_data accepts.

    Prefer numpy if present (imageio's native path); fall back to imageio's own helper.
    """
    try:
        import numpy as _np
        return _np.asarray(img)
    except Exception:
        # imageio can accept a Pillow image directly via its core util in many versions.
        return img


def screen_record_start(seconds: int = 10, fps: int = 8, output_path: str = "") -> dict:
    """Start recording the screen in the background to mp4 (or gif fallback)."""
    try:
        seconds = max(1, min(_MAX_SECONDS, int(seconds)))
    except Exception:
        seconds = 10
    try:
        fps = max(1, min(30, int(fps)))
    except Exception:
        fps = 8

    with _rec_lock:
        if _rec_state["recording"]:
            return {"ok": False, "error": "already recording — call screen_record_stop first"}

    # Lazy import mss.
    try:
        import mss as mss_mod
    except Exception:
        return {"ok": False, "error": "mss not installed — pip install mss"}

    # Lazy import Pillow (needed for both paths).
    try:
        from PIL import Image as pil_image
    except Exception:
        return {"ok": False, "error": "Pillow not installed — pip install Pillow"}

    # Decide output format: try mp4 via imageio, else gif via Pillow.
    use_gif = False
    writer = None
    requested_gif = bool(output_path) and str(output_path).lower().endswith(".gif")

    if requested_gif:
        use_gif = True
        output = Path(output_path).expanduser()
    else:
        try:
            import imageio
            try:
                import imageio_ffmpeg  # noqa: F401  (ensures the ffmpeg plugin is available)
            except Exception:
                pass
            output = (Path(output_path).expanduser() if output_path
                      else _rec_default_output(use_gif=False))
            try:
                writer = imageio.get_writer(str(output), fps=fps)
            except Exception:
                writer = None
                use_gif = True
        except Exception:
            use_gif = True
            output = (Path(output_path).expanduser() if output_path
                      else _rec_default_output(use_gif=True))

    if use_gif and writer is None and not requested_gif:
        # mp4 path failed; switch the output extension to .gif.
        if output_path:
            output = Path(output_path).expanduser().with_suffix(".gif")
        else:
            output = _rec_default_output(use_gif=True)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    stop_event = threading.Event()
    t = threading.Thread(
        target=_record_loop,
        args=(seconds, fps, output, use_gif, stop_event, mss_mod, writer, pil_image),
        daemon=True,
    )
    with _rec_lock:
        _rec_state.update({
            "recording": True,
            "thread": t,
            "stop": stop_event,
            "frames": 0,
            "output": str(output),
            "started": time.time(),
            "message": "",
        })
    t.start()
    fmt = "gif" if use_gif else "mp4"
    return {"ok": True, "recording": True, "output": str(output),
            "message": f"recording up to {seconds}s at {fps} fps -> {fmt}"}


def screen_record_stop() -> dict:
    """Stop the in-progress screen recording and finalize the file."""
    with _rec_lock:
        if not _rec_state["recording"] and not _rec_state["thread"]:
            return {"ok": False, "error": "not recording"}
        stop_event = _rec_state["stop"]
        thread = _rec_state["thread"]
    if stop_event:
        stop_event.set()
    if thread:
        thread.join(timeout=15)
    with _rec_lock:
        return {"ok": True, "output": _rec_state["output"], "frames": _rec_state["frames"],
                "message": _rec_state["message"] or "stopped"}


def screen_record_status() -> dict:
    """Report whether a recording is in progress, frame count and output path."""
    with _rec_lock:
        elapsed = round(time.time() - _rec_state["started"], 1) if _rec_state["started"] else 0
        return {"ok": True, "recording": bool(_rec_state["recording"]),
                "frames": _rec_state["frames"], "output": _rec_state["output"],
                "elapsed": elapsed}


# ===========================================================================
# 4) SCREEN COLOR PICKER  (lazy mss + PIL; fresh full-screen capture)
# ===========================================================================
def pick_screen_color(x: int, y: int) -> dict:
    """Read the color of the pixel at (x, y) from a fresh full-screen capture."""
    try:
        x = int(x)
        y = int(y)
    except Exception:
        return {"ok": False, "error": "x and y must be integers"}

    try:
        import mss as mss_mod
    except Exception:
        return {"ok": False, "error": "mss not installed — pip install mss"}
    try:
        from PIL import Image as pil_image
    except Exception:
        return {"ok": False, "error": "Pillow not installed — pip install Pillow"}

    try:
        with mss_mod.mss() as sct:
            monitor = sct.monitors[0]  # full virtual screen
            shot = sct.grab(monitor)
            img = pil_image.frombytes("RGB", shot.size, shot.rgb)
            w, h = img.size
            # Translate absolute screen coords into the virtual-screen image space.
            px = x - monitor.get("left", 0)
            py = y - monitor.get("top", 0)
            if not (0 <= px < w and 0 <= py < h):
                return {"ok": False, "error": f"({x}, {y}) is outside the screen ({w}x{h})"}
            r, g, b = img.getpixel((px, py))[:3]
        return {"ok": True, "x": x, "y": y,
                "hex": "#%02x%02x%02x" % (r, g, b), "rgb": [r, g, b]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===========================================================================
# 5) MULTI-MONITOR SCREENSHOT  (lazy mss)
# ===========================================================================
def screenshot_monitor(index: int = 1, output_path: str = "") -> dict:
    """Capture a specific monitor (1-based) to a PNG. Index 0 is the all-screens virtual one."""
    try:
        index = int(index)
    except Exception:
        return {"ok": False, "error": "index must be an integer"}

    try:
        import mss as mss_mod
        import mss.tools as mss_tools
    except Exception:
        return {"ok": False, "error": "mss not installed — pip install mss"}

    try:
        with mss_mod.mss() as sct:
            monitors = sct.monitors
            # monitors[0] is virtual all-screens; 1+ are physical.
            available = len(monitors) - 1
            if index < 0 or index >= len(monitors):
                return {"ok": False,
                        "error": f"monitor index {index} out of range — "
                                 f"{available} physical monitor(s) available (use 1..{available})"}
            monitor = monitors[index]
            if output_path:
                out = Path(output_path).expanduser()
            else:
                folder = _data_dir() / "screenshots"
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
                out = folder / f"monitor{index}_{int(time.time())}.png"
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            shot = sct.grab(monitor)
            mss_tools.to_png(shot.rgb, shot.size, output=str(out))
            return {"ok": True, "index": index, "output": str(out),
                    "width": shot.size[0], "height": shot.size[1]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===========================================================================
# Wiring exports
# ===========================================================================
TOOL_DECLARATIONS = [
    # ---- Snippet expander ----
    {"name": "snippet_save",
     "description": "Save a reusable text snippet under a keyword (expand later with ;keyword).",
     "parameters": {"type": "OBJECT",
       "properties": {"keyword": {"type": "STRING"}, "text": {"type": "STRING"}},
       "required": ["keyword", "text"]}},
    {"name": "snippet_list",
     "description": "List saved snippet keywords with a short preview of each.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "snippet_get",
     "description": "Get the full text of a saved snippet by keyword.",
     "parameters": {"type": "OBJECT",
       "properties": {"keyword": {"type": "STRING"}}, "required": ["keyword"]}},
    {"name": "snippet_delete",
     "description": "Delete a saved snippet by keyword.",
     "parameters": {"type": "OBJECT",
       "properties": {"keyword": {"type": "STRING"}}, "required": ["keyword"]}},
    {"name": "snippet_expand",
     "description": "Expand every ;keyword token in the given text into its saved snippet body.",
     "parameters": {"type": "OBJECT",
       "properties": {"text": {"type": "STRING"}}, "required": ["text"]}},
    # ---- Email breach monitor ----
    {"name": "email_breach_check",
     "description": "Check whether an email appears in known data breaches (free XposedOrNot service).",
     "parameters": {"type": "OBJECT",
       "properties": {"email": {"type": "STRING"}}, "required": ["email"]}},
    # ---- Screen recorder ----
    {"name": "screen_record_start",
     "description": "Start recording the screen in the background to mp4 (or gif fallback).",
     "parameters": {"type": "OBJECT",
       "properties": {"seconds": {"type": "INTEGER"}, "fps": {"type": "INTEGER"},
                      "output_path": {"type": "STRING"}},
       "required": []}},
    {"name": "screen_record_stop",
     "description": "Stop the in-progress screen recording and finalize the file.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "screen_record_status",
     "description": "Report whether a screen recording is running, frame count and output path.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    # ---- Screen color picker ----
    {"name": "pick_screen_color",
     "description": "Read the color of the on-screen pixel at (x, y) from a fresh full-screen capture.",
     "parameters": {"type": "OBJECT",
       "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}},
       "required": ["x", "y"]}},
    # ---- Multi-monitor screenshot ----
    {"name": "screenshot_monitor",
     "description": "Capture a specific monitor (1-based) to a PNG; index 0 is the all-screens virtual monitor.",
     "parameters": {"type": "OBJECT",
       "properties": {"index": {"type": "INTEGER"}, "output_path": {"type": "STRING"}},
       "required": []}},
]

TOOL_DISPATCH = {
    "snippet_save": snippet_save,
    "snippet_list": snippet_list,
    "snippet_get": snippet_get,
    "snippet_delete": snippet_delete,
    "snippet_expand": snippet_expand,
    "email_breach_check": email_breach_check,
    "screen_record_start": screen_record_start,
    "screen_record_stop": screen_record_stop,
    "screen_record_status": screen_record_status,
    "pick_screen_color": pick_screen_color,
    "screenshot_monitor": screenshot_monitor,
}

READONLY_TOOLS = {
    "snippet_list", "snippet_get", "snippet_expand", "email_breach_check",
    "screen_record_status", "pick_screen_color", "screenshot_monitor",
}

INTERACTION_TOOLS = {
    "snippet_save", "snippet_delete", "screen_record_start", "screen_record_stop",
}
