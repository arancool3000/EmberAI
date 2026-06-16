"""Screen vision for Ember: exact on-screen text location + accurate clicking.

This module removes the "blind guessing" of pixel coordinates. Instead of reading
numbers off a grid overlay, Ember locates targets by their real on-screen geometry:

  1. Accessibility tree (macOS System Events / Windows UIA) -> exact element bounds.
  2. Apple Vision OCR (macOS) / Windows.Media.Ocr (Windows) -> exact text bounds,
     so any visible word becomes a precise, clickable point.

Public tools (registered in agent.py):
  read_screen_text(region, query)   -> every visible text fragment + exact center px
  select_screen_text(text, ...)     -> drag-selects on-screen text (the "select text" tool)
  smart_click(target, ...)          -> AX-tree-first, OCR-second click. No grid guessing.
  locate_text(text, ...)            -> just the coordinates, no clicking (for planning)

All coordinates returned/consumed are in the SAME logical-point space that click(x, y)
uses, so the agent can pass a returned center straight into click(). Retina scaling is
handled internally (mss grabs physical pixels; pyautogui clicks logical points).
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from typing import Any

import mss
import pyautogui
from PIL import Image

try:
    from rapidfuzz import fuzz as _fuzz
except Exception:  # pragma: no cover - optional
    _fuzz = None

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform.startswith("win")

# Cached Apple Vision availability flag (lazy import; pyobjc is mac-only).
_VISION = None  # None = unknown, False = unavailable, module-ish object = available


# ---------------------------------------------------------------------------
# Coordinate model
# ---------------------------------------------------------------------------
_SCALE_CACHE: tuple | None = None  # (logical_size, sx, sy, mon)


def refresh_scale() -> None:
    """Drop the cached display scale (call if the resolution changes out-of-band)."""
    global _SCALE_CACHE
    _SCALE_CACHE = None


def _scale() -> tuple[float, float, dict]:
    """Return (scale_x, scale_y, primary_monitor) mapping physical px -> logical pts.

    mss reports physical pixels; pyautogui clicks in logical points. On a Retina
    display these differ by ~2x. We measure the ratio instead of assuming it.

    Cached and keyed on the logical screen size: the polling hot path (wait_for_text)
    no longer spins up a fresh mss capture context on every call, but a resolution
    change still invalidates the cache automatically."""
    global _SCALE_CACHE
    try:
        size = tuple(pyautogui.size())
    except Exception:
        size = None
    if _SCALE_CACHE is not None and _SCALE_CACHE[0] == size:
        return _SCALE_CACHE[1], _SCALE_CACHE[2], _SCALE_CACHE[3]
    with mss.mss() as sct:
        mon = sct.monitors[1]
    try:
        lw, lh = size if size else pyautogui.size()
        sx = mon["width"] / lw if lw else 1.0
        sy = mon["height"] / lh if lh else 1.0
    except Exception:
        sx = sy = 1.0
    # Guard against weird values; clamp to sane DPI ratios.
    if not (0.5 <= sx <= 4.0):
        sx = 1.0
    if not (0.5 <= sy <= 4.0):
        sy = 1.0
    _SCALE_CACHE = (size, sx, sy, mon)
    return sx, sy, mon


def _grab(region: dict | None):
    """Grab a screenshot. `region` is in LOGICAL points {x,y,width,height} (the agent's
    coordinate space) or None for the whole primary screen.

    Returns (PIL.Image in physical px, scale_x, scale_y, origin_logical_x, origin_logical_y).
    """
    sx, sy, mon = _scale()
    if region:
        left = int(mon["left"] + float(region["x"]) * sx)
        top = int(mon["top"] + float(region["y"]) * sy)
        width = max(1, int(float(region["width"]) * sx))
        height = max(1, int(float(region["height"]) * sy))
        grab_box = {"left": left, "top": top, "width": width, "height": height}
        origin_lx = float(region["x"])
        origin_ly = float(region["y"])
    else:
        grab_box = mon
        origin_lx = mon["left"] / sx
        origin_ly = mon["top"] / sy
    with mss.mss() as sct:
        raw = sct.grab(grab_box)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img, sx, sy, origin_lx, origin_ly


# ---------------------------------------------------------------------------
# OCR backends
# ---------------------------------------------------------------------------
def _vision_available() -> bool:
    global _VISION
    if _VISION is not None:
        return _VISION is not False
    if not _IS_MAC:
        _VISION = False
        return False
    try:
        import Vision  # noqa: F401  (pyobjc-framework-Vision)
        import Quartz  # noqa: F401  (pyobjc-framework-Quartz)
        _VISION = True
    except Exception:
        _VISION = False
    return _VISION is True


def _ocr_mac(img: Image.Image, languages: list[str] | None = None) -> list[dict]:
    """OCR a PIL image with Apple's Vision framework. Returns fragments in the image's
    own physical-pixel space: {text, x, y, w, h, conf} with top-left origin."""
    import Quartz
    import Vision
    from Foundation import NSData

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    if src is None:
        return []
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        return []
    W = Quartz.CGImageGetWidth(cg)
    H = Quartz.CGImageGetHeight(cg)

    req = Vision.VNRecognizeTextRequest.alloc().init()
    try:
        req.setRecognitionLevel_(1)  # 1 = accurate, 0 = fast
        req.setUsesLanguageCorrection_(True)
        if languages:
            req.setRecognitionLanguages_(languages)
    except Exception:
        pass
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok, _err = handler.performRequests_error_([req], None)
    if not ok:
        return []
    out: list[dict] = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        text = cand.string()
        if not text:
            continue
        bb = obs.boundingBox()  # normalized, origin BOTTOM-left
        x = bb.origin.x * W
        w = bb.size.width * W
        h = bb.size.height * H
        y = (1.0 - bb.origin.y - bb.size.height) * H  # flip to top-left origin
        try:
            conf = float(cand.confidence())
        except Exception:
            conf = 1.0
        out.append({"text": str(text), "x": x, "y": y, "w": w, "h": h, "conf": conf})
    return out


def _ocr_win(img: Image.Image) -> list[dict]:
    """Windows fallback OCR via Windows.Media.Ocr. Returns word-level fragments with
    bounding boxes in image physical-pixel space. Best-effort; empty on failure."""
    import json as _json
    import subprocess
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"ember_ocr_{int(time.time()*1000)}.png"
    img.save(tmp)
    ps = r'''
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t,$rt){ $at=$asTaskGeneric.MakeGenericMethod($rt).Invoke($null,@($t)); $at.Wait(-1)|Out-Null; $at.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync("__PATH__")) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync('Read')) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$items = foreach($line in $result.Lines){ foreach($w in $line.Words){ $r=$w.BoundingRect; [pscustomobject]@{ text=$w.Text; x=$r.X; y=$r.Y; w=$r.Width; h=$r.Height } } }
$items | ConvertTo-Json -Compress
'''.replace("__PATH__", str(tmp))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        raw = (r.stdout or "").strip()
        data = _json.loads(raw) if raw else []
        if isinstance(data, dict):
            data = [data]
        return [{"text": str(d["text"]), "x": float(d["x"]), "y": float(d["y"]),
                 "w": float(d["w"]), "h": float(d["h"]), "conf": 1.0} for d in data]
    except Exception:
        return []
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _ocr_image(img: Image.Image, languages: list[str] | None = None) -> list[dict]:
    if _IS_MAC and _vision_available():
        return _ocr_mac(img, languages=languages)
    if _IS_WIN:
        return _ocr_win(img)
    return []


def ocr_backend_status() -> dict:
    """Report which OCR engine is active (for diagnostics / settings UI)."""
    if _IS_MAC:
        return {"ok": True, "platform": "mac", "engine": "Apple Vision",
                "available": _vision_available(),
                "hint": "" if _vision_available() else
                "pip install pyobjc-framework-Vision pyobjc-framework-Quartz"}
    if _IS_WIN:
        return {"ok": True, "platform": "windows", "engine": "Windows.Media.Ocr",
                "available": True, "hint": ""}
    return {"ok": False, "platform": sys.platform, "engine": "none", "available": False,
            "hint": "OCR is only available on macOS and Windows"}


# ---------------------------------------------------------------------------
# Fragment helpers
# ---------------------------------------------------------------------------
def _to_logical(frag: dict, sx: float, sy: float, ox: float, oy: float) -> dict:
    """Convert one OCR fragment from image physical px to logical screen points."""
    lx = ox + frag["x"] / sx
    ly = oy + frag["y"] / sy
    lw = frag["w"] / sx
    lh = frag["h"] / sy
    return {
        "text": frag["text"],
        "x": int(round(lx)), "y": int(round(ly)),
        "w": int(round(lw)), "h": int(round(lh)),
        "center_x": int(round(lx + lw / 2)),
        "center_y": int(round(ly + lh / 2)),
        "conf": round(float(frag.get("conf", 1.0)), 3),
    }


def _score(query: str, text: str) -> float:
    q, t = query.lower().strip(), text.lower().strip()
    if not q:
        return 0.0
    if q == t:
        return 100.0
    if _fuzz is not None:
        # token_set_ratio handles word-order/extra-words; partial handles substrings.
        return max(_fuzz.token_set_ratio(q, t), _fuzz.partial_ratio(q, t))
    if q in t:
        return 90.0
    return 0.0


# ---------------------------------------------------------------------------
# Public: read all on-screen text
# ---------------------------------------------------------------------------
def read_screen_text(region: dict | None = None, query: str = "",
                     min_conf: float = 0.0, max_results: int = 120) -> dict:
    """Read every text fragment currently visible (optionally within a logical-point
    region), each with its exact clickable center. If `query` is given, results are
    ranked by similarity to it (best first). This is how Ember 'sees' text without
    guessing coordinates off a grid."""
    try:
        img, sx, sy, ox, oy = _grab(region)
    except Exception as e:
        return {"ok": False, "error": f"screen grab failed: {e}"}
    raw = _ocr_image(img)
    if not raw and not (_IS_MAC and _vision_available()) and not _IS_WIN:
        return {"ok": False, "error": "OCR backend unavailable on this platform",
                "backend": ocr_backend_status()}
    frags = [_to_logical(f, sx, sy, ox, oy) for f in raw
             if float(f.get("conf", 1.0)) >= min_conf]
    if query:
        scored = []
        for f in frags:
            f["match"] = round(_score(query, f["text"]), 1)
            scored.append(f)
        scored.sort(key=lambda f: -f["match"])
        frags = [f for f in scored if f["match"] >= 50] or scored[:10]
    frags = frags[:max_results]
    return {"ok": True, "count": len(frags), "fragments": frags,
            "full_text": " ".join(f["text"] for f in frags) if not query else None,
            "backend": ocr_backend_status().get("engine")}


def locate_text(text: str, region: dict | None = None, occurrence: int = 1) -> dict:
    """Return the exact location of on-screen text without clicking it. `occurrence`
    selects the Nth match (1-based) when text appears multiple times."""
    res = read_screen_text(region=region, query=text)
    if not res.get("ok"):
        return res
    matches = [f for f in res["fragments"] if f.get("match", 0) >= 70]
    if not matches:
        # fall back to best available, but flag low confidence
        if res["fragments"]:
            best = res["fragments"][0]
            return {"ok": False, "error": f"no confident match for '{text}'",
                    "best_guess": best, "alternatives": res["fragments"][:5]}
        return {"ok": False, "error": f"'{text}' not found on screen"}
    idx = max(0, min(len(matches) - 1, int(occurrence) - 1))
    chosen = matches[idx]
    return {"ok": True, "match": chosen, "occurrences": len(matches),
            "alternatives": matches[:5]}


# ---------------------------------------------------------------------------
# Public: select on-screen text (the "select text on screen" tool)
# ---------------------------------------------------------------------------
def select_screen_text(text: str, region: dict | None = None, occurrence: int = 1,
                       copy: bool = False, to_text: str | None = None) -> dict:
    """Visually select on-screen text by dragging across it (like a human click-drag).

    - text:       the phrase to start the selection at (located by OCR).
    - to_text:    optional phrase to END the selection at, enabling multi-line/range
                  selection from `text` ... to `to_text`.
    - copy:       if True, copy the selection to the clipboard after selecting.

    Returns the selected region in logical points so the caller can verify."""
    start = locate_text(text, region=region, occurrence=occurrence)
    if not start.get("ok"):
        return start
    s = start["match"]
    # Start just inside the left edge, vertically centered.
    sx_pt = s["x"] + 2
    sy_pt = s["center_y"]
    if to_text:
        end = locate_text(to_text, region=region)
        if not end.get("ok"):
            return {"ok": False, "error": f"end anchor '{to_text}' not found", "start": s}
        e = end["match"]
        ex_pt = e["x"] + e["w"] - 2
        ey_pt = e["center_y"]
    else:
        ex_pt = s["x"] + s["w"] - 2
        ey_pt = s["center_y"]
    try:
        pyautogui.moveTo(sx_pt, sy_pt, duration=0.12)
        pyautogui.mouseDown(button="left")
        # Move in two hops so apps register an actual selection drag.
        pyautogui.moveTo((sx_pt + ex_pt) // 2, (sy_pt + ey_pt) // 2, duration=0.12)
        pyautogui.moveTo(ex_pt, ey_pt, duration=0.18)
        pyautogui.mouseUp(button="left")
    except Exception as e:
        return {"ok": False, "error": f"drag-select failed: {e}"}
    result = {"ok": True, "selected_from": [sx_pt, sy_pt], "selected_to": [ex_pt, ey_pt],
              "start_text": s["text"], "end_text": (to_text or s["text"])}
    if copy:
        try:
            import pyperclip
            time.sleep(0.05)
            key = "command" if _IS_MAC else "ctrl"
            pyautogui.hotkey(key, "c")
            time.sleep(0.1)
            result["clipboard"] = pyperclip.paste()
        except Exception as e:
            result["copy_error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Public: smart_click - the no-blind-guessing click algorithm
# ---------------------------------------------------------------------------
def smart_click(target: str, double: bool = False, button: str = "left",
                region: dict | None = None, prefer: str = "auto") -> dict:
    """Click the on-screen thing described by `target`, choosing real coordinates
    instead of guessing off a grid.

    Resolution order (each yields EXACT geometry, never a guess):
      1. Accessibility tree  - buttons/fields/menus by their accessible name.
      2. Vision OCR          - any visible text becomes a precise click point.
    Picks the highest-confidence candidate, clicks its exact center, and reports
    which method matched + the runner-up candidates. If nothing is confident, it
    REFUSES to click and returns candidates so the agent can decide - this is what
    eliminates blind misclicks."""
    import tools  # local import to avoid a cycle at module load
    candidates: list[dict] = []

    # --- 1. Accessibility tree (fast, exact, semantic) ---
    if prefer in ("auto", "ax", "accessibility"):
        try:
            ax = tools.find_ui_elements(filter_text=target, scope="foreground", max_results=8)
            for el in (ax.get("elements") or []):
                name = el.get("name") or ""
                sc = _score(target, name) if name else 0.0
                # AX matches are trustworthy; give them a small confidence bonus.
                candidates.append({
                    "method": "accessibility",
                    "label": name or el.get("type", ""),
                    "x": el["x"], "y": el["y"],
                    "score": min(100.0, sc + 8) if name else 40.0,
                    "type": el.get("type", ""),
                })
        except Exception:
            pass

    # --- 2. Vision OCR (anything visible) ---
    if prefer in ("auto", "ocr", "text") or not candidates:
        ocr = read_screen_text(region=region, query=target)
        if ocr.get("ok"):
            for f in ocr.get("fragments", [])[:8]:
                candidates.append({
                    "method": "ocr",
                    "label": f["text"],
                    "x": f["center_x"], "y": f["center_y"],
                    "score": float(f.get("match", 0.0)),
                    "conf": f.get("conf"),
                })

    if not candidates:
        return {"ok": False, "error": f"could not locate '{target}' (no AX match, no OCR text)",
                "hint": "try take_screenshot to inspect, or read_screen_text to list visible text"}

    candidates.sort(key=lambda c: -c["score"])
    # Collapse near-duplicate hits (same spot found by both AX and OCR) so one target isn't
    # counted as two occurrences.
    distinct: list[dict] = []
    for c in candidates:
        if not any(abs(c["x"] - d["x"]) < 12 and abs(c["y"] - d["y"]) < 12 for d in distinct):
            distinct.append(c)
    best = distinct[0]
    runners = distinct[1:4]

    # Refuse to click on a weak match - this is the anti-blind-guess guarantee.
    CONFIDENT = 70.0
    if best["score"] < CONFIDENT:
        return {"ok": False, "error": f"no confident target for '{target}' "
                f"(best '{best['label']}' scored {best['score']:.0f}/100)",
                "best_guess": best, "alternatives": runners,
                "hint": "refine the target text, or call read_screen_text to see exact labels"}

    # Ambiguity guard: if the target matches several DISTINCT, similarly-confident spots, don't
    # blindly click the first — report every occurrence so the caller can pick the right one.
    ambiguous = [c for c in distinct if c["score"] >= CONFIDENT and (best["score"] - c["score"]) <= 12]
    if len(ambiguous) >= 2:
        return {"ok": False, "ambiguous": True,
                "error": f"'{target}' matches {len(ambiguous)} places on screen — which one?",
                "occurrences": [{"label": c["label"], "x": c["x"], "y": c["y"],
                                 "score": round(c["score"], 1), "method": c["method"]}
                                for c in ambiguous],
                "hint": "narrow it: pass a region, use more specific target text, or click exact "
                        "coordinates (click x,y) for the intended occurrence listed above"}

    res = tools.click(best["x"], best["y"], button=button, double=double)
    res["matched"] = {"label": best["label"], "method": best["method"],
                      "score": round(best["score"], 1), "x": best["x"], "y": best["y"]}
    if runners:
        res["alternatives"] = [{"label": r["label"], "score": round(r["score"], 1)} for r in runners]
    return res


# ---------------------------------------------------------------------------
# Fixed cross-platform OCR entry points (replace the Windows-only ones)
# ---------------------------------------------------------------------------
def wait_for_text(text: str, timeout: float = 10.0, region: dict | None = None,
                  poll: float = 0.6) -> dict:
    """Block until `text` appears on screen (or timeout). Returns its location when found.
    Use after an action that loads new UI, instead of a fixed wait() - faster and reliable."""
    deadline_budget = max(0.5, min(60.0, float(timeout)))
    elapsed = 0.0
    last = None
    while elapsed < deadline_budget:
        loc = locate_text(text, region=region)
        if loc.get("ok"):
            loc["waited"] = round(elapsed, 1)
            return loc
        last = loc
        time.sleep(poll)
        elapsed += poll
    return {"ok": False, "error": f"'{text}' did not appear within {deadline_budget:.0f}s",
            "last_seen": last}


def assert_text_visible(text: str, region: dict | None = None) -> dict:
    """Verification helper: confirm `text` is currently visible on screen. Returns ok=True
    with its location, or ok=False so the agent can't honestly claim success without proof."""
    loc = locate_text(text, region=region)
    if loc.get("ok"):
        return {"ok": True, "visible": True, "text": text, "at": loc["match"]}
    return {"ok": False, "visible": False, "text": text,
            "error": f"'{text}' is NOT visible on screen", "alternatives": loc.get("alternatives")}


def ocr_image_file(path: str, languages: list[str] | None = None) -> dict:
    """OCR an image file on disk (cross-platform). Returns full text + fragments."""
    try:
        img = Image.open(str(Path(path).expanduser())).convert("RGB")
    except Exception as e:
        return {"ok": False, "error": f"could not open image: {e}"}
    raw = _ocr_image(img, languages=languages)
    text = "\n".join(f["text"] for f in raw)
    return {"ok": True, "text": text[:10000], "length": len(text),
            "fragment_count": len(raw), "backend": ocr_backend_status().get("engine")}


def ocr_screen_region(region: dict | None = None) -> dict:
    """OCR the live screen (optionally a logical-point region) -> plain text."""
    res = read_screen_text(region=region)
    if not res.get("ok"):
        return res
    return {"ok": True, "text": (res.get("full_text") or
            " ".join(f["text"] for f in res["fragments"]))[:10000],
            "fragment_count": res["count"], "backend": res.get("backend")}
