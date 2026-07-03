"""Ember Link - control this PC from a phone browser on the same Wi-Fi, or (opt-in) from
ANYWHERE via a tunnel (see tunnel.py + enable_remote()).

Self-contained: pure Python stdlib HTTP server (no Flask, no websockets, no cloud
backend). Ember starts it; you open the printed URL on your phone, enter the PIN, and
you get a live view of the PC screen plus a trackpad/keyboard. Tap the screen image to
click exactly there - so even with no working mouse/keyboard drivers on the PC, the phone
drives it.

Security model: two credentials, deliberately scoped differently.
  - The short PIN only ever authenticates requests that arrive from a private/LAN source
    address (see _is_lan_ip). This matters because a Cloudflare/ngrok tunnel forwards public
    traffic to `localhost`, so a tunnel-relayed request looks IDENTICAL to a local one at the
    socket level (source IP 127.0.0.1) - if the PIN worked there too, anyone who found the
    public tunnel URL could just brute-force the 6-digit PIN over the internet. Restricting PIN
    auth to real LAN-looking addresses closes that.
  - The long pairing token (issue_pair_token) is what a device gets AFTER proving it knows the
    PIN from the LAN, and it works from anywhere (LAN or the public tunnel). That's the "pair on
    Wi-Fi, then roam" flow: pairing itself can only happen on the LAN; once paired, the token -
    never the PIN - is what the internet-facing tunnel accepts.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pyautogui

import tools

pyautogui.FAILSAFE = False  # phone control can legitimately reach screen corners
pyautogui.PAUSE = 0          # remove the 50ms post-call stall so rapid moves/keys stay snappy

_STATE = {"server": None, "thread": None, "pin": None, "port": None, "ip": None,
          "last_active": 0.0, "idle_timeout": 1800.0}
_SCT = None                  # reused mss grabber (re-creating it per frame is slow)
_SCT_LOCK = threading.Lock()
_CHAT_HANDLER = None
_CHAT_LOG: list[dict] = []
_CHAT_LOCK = threading.Lock()
_CHAT_STREAM_ID: str | None = None

# Phone input arrives on per-request threads (ThreadingHTTPServer); pyautogui mouse state
# is process-global, so serialize input application to avoid interleaved move/click races.
_INPUT_LOCK = threading.Lock()

# PIN brute-force throttle: the PIN is a stable 4-digit code on the LAN, so rate-limit
# failed auth per client IP (small delay each failure + lockout after a burst).
_AUTH_FAILS: dict[str, dict] = {}
_AUTH_LOCK = threading.Lock()
_AUTH_MAX_FAILS = 5         # failures within the window before lockout
_AUTH_WINDOW_S = 60.0       # rolling window for counting failures
_AUTH_LOCKOUT_S = 120.0     # cooldown once locked out


# Pairing tokens: a phone pairs once on the LAN (proves it knows the PIN) and gets a LONG random
# secret. Remote (off-network) connections authenticate with that token instead of the short PIN,
# so the PIN is never the thing standing between the internet and your computer. Tokens persist so
# pairing survives restarts; revoke_pairings() unpairs every device.
# Stored as an ORDER-PRESERVING list (oldest first) so trimming to _MAX_TOKENS keeps the newest
# ones — a plain set() has no defined order, so slicing it wouldn't reliably keep new tokens.
_PAIR_TOKENS: list[str] = []
_TOKEN_LOCK = threading.Lock()
_TOKENS_LOADED = False
_MAX_TOKENS = 24


def _tokens_file():
    return _data_dir() / "remote_tokens.txt"


def _load_tokens() -> None:
    global _TOKENS_LOADED
    if _TOKENS_LOADED:
        return
    try:
        f = _tokens_file()
        if f.exists():
            with _TOKEN_LOCK:
                for ln in f.read_text().splitlines():
                    t = ln.strip()
                    if len(t) >= 20 and t not in _PAIR_TOKENS:
                        _PAIR_TOKENS.append(t)
    except OSError:
        pass
    _TOKENS_LOADED = True


def _save_tokens() -> None:
    try:
        with _TOKEN_LOCK:
            data = "\n".join(_PAIR_TOKENS[-_MAX_TOKENS:])
        _tokens_file().write_text(data)
    except OSError:
        pass


def issue_pair_token() -> str:
    """Mint, persist and return a new long pairing token (called after a PIN-verified pairing)."""
    _load_tokens()
    tok = secrets.token_urlsafe(32)
    with _TOKEN_LOCK:
        _PAIR_TOKENS.append(tok)
        # keep the list bounded (oldest dropped first) to avoid unbounded growth
        del _PAIR_TOKENS[:-_MAX_TOKENS]
    _save_tokens()
    return tok


def _token_valid(tok) -> bool:
    tok = str(tok or "")
    if len(tok) < 20:
        return False
    _load_tokens()
    with _TOKEN_LOCK:
        toks = list(_PAIR_TOKENS)
    for t in toks:
        if secrets.compare_digest(tok, t):
            return True
    return False


def revoke_pairings() -> dict:
    """Forget every paired device (they'll need to re-pair on the LAN)."""
    _load_tokens()
    with _TOKEN_LOCK:
        n = len(_PAIR_TOKENS)
        _PAIR_TOKENS.clear()
    _save_tokens()
    return {"ok": True, "revoked": n}


def paired_count() -> int:
    _load_tokens()
    with _TOKEN_LOCK:
        return len(_PAIR_TOKENS)


def _is_lan_ip(ip: str) -> bool:
    """True for a genuine private/LAN address (192.168.x.x, 10.x.x.x, etc.) - deliberately FALSE
    for loopback (127.0.0.1 / ::1), because that's what a tunnel-relayed public request looks
    like once cloudflared/ngrok forwards it to localhost. Used to keep the short PIN LAN-only."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private and not addr.is_loopback


def _auth_locked(ip: str) -> bool:
    with _AUTH_LOCK:
        rec = _AUTH_FAILS.get(ip)
        return bool(rec and rec.get("locked_until", 0) > time.time())


def _auth_record(ip: str, ok: bool) -> None:
    now = time.time()
    with _AUTH_LOCK:
        rec = _AUTH_FAILS.setdefault(ip, {"fails": 0, "window": now, "locked_until": 0.0})
        if ok:
            rec["fails"] = 0
            rec["locked_until"] = 0.0
            return
        if now - rec["window"] > _AUTH_WINDOW_S:
            rec["fails"] = 0
            rec["window"] = now
        rec["fails"] += 1
        if rec["fails"] >= _AUTH_MAX_FAILS:
            rec["locked_until"] = now + _AUTH_LOCKOUT_S


def _capture(hd: bool = True, max_w: int | None = None, quality: int | None = None):
    """Grab the screen at HD quality. Returns (jpeg_bytes, logical_w, logical_h).
    The phone client can tune max width + JPEG quality per quality mode."""
    import io
    import mss
    from PIL import Image
    global _SCT
    with _SCT_LOCK:
        if _SCT is None:
            _SCT = mss.mss()
        try:
            raw = _SCT.grab(_SCT.monitors[1])
        except Exception:
            _SCT = mss.mss()  # re-init if the cached grabber went stale
            raw = _SCT.grab(_SCT.monitors[1])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    lw, lh = pyautogui.size()  # logical points, for click mapping
    max_w = int(max_w or (1680 if hd else 1050))
    max_w = max(720, min(2560, max_w))
    quality = int(quality or (82 if hd else 64))
    quality = max(45, min(92, quality))
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=quality,
        optimize=False,
        progressive=False,
        subsampling=1 if quality >= 78 else 2,
    )
    return buf.getvalue(), lw, lh


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def set_chat_handler(handler):
    """Register a desktop-app callback for remote chat commands."""
    global _CHAT_HANDLER
    _CHAT_HANDLER = handler


def _chat_add(role: str, text: str) -> dict:
    item = {
        "id": f"chat_{int(time.time() * 1000)}_{secrets.token_hex(3)}",
        "role": role,
        "text": str(text or ""),
        "ts": time.time(),
    }
    with _CHAT_LOCK:
        _CHAT_LOG.append(item)
        del _CHAT_LOG[:-120]
    return item


def push_chat(role: str, text: str) -> dict:
    """Expose desktop UI events to the phone chat timeline."""
    return _chat_add(role, text)


def update_stream(delta: str = "", done: bool = False):
    """Append streaming assistant text into one remote chat bubble."""
    global _CHAT_STREAM_ID
    with _CHAT_LOCK:
        if _CHAT_STREAM_ID is None:
            item = {
                "id": f"stream_{int(time.time() * 1000)}_{secrets.token_hex(3)}",
                "role": "assistant",
                "text": "",
                "ts": time.time(),
            }
            _CHAT_LOG.append(item)
            _CHAT_STREAM_ID = item["id"]
        for item in reversed(_CHAT_LOG):
            if item.get("id") == _CHAT_STREAM_ID:
                item["text"] = (item.get("text") or "") + str(delta or "")
                item["ts"] = time.time()
                break
        del _CHAT_LOG[:-120]
        if done:
            _CHAT_STREAM_ID = None


def _chat_snapshot() -> list[dict]:
    with _CHAT_LOCK:
        return list(_CHAT_LOG[-80:])


PAGE = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Ember Link">
<meta name="theme-color" content="#070708">
<link rel="apple-touch-icon" href="/icon.png">
<link rel="icon" href="/icon.png">
<link rel="manifest" href="/manifest.webmanifest">
<title>Ember Link</title><style>
:root{--bg:#070708;--fg:rgba(255,255,255,.94);--mut:rgba(255,255,255,.62);--faint:rgba(255,255,255,.38);--glass:rgba(255,255,255,.105);--glass2:rgba(255,255,255,.16);--line:rgba(255,255,255,.2);--line2:rgba(255,255,255,.32);--solid:#fff;--dark:#0a0a0c;--err:#ff6b6b}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
input,textarea{-webkit-user-select:text;user-select:text}
html,body{min-height:100%;overscroll-behavior:none}
body{margin:0;background:radial-gradient(circle at 50% -10%,#3a3a3d 0,#18191b 38%,#060607 84%);color:var(--fg);-webkit-overflow-scrolling:touch}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(140deg,rgba(255,255,255,.08),transparent 34%),linear-gradient(0deg,rgba(255,255,255,.025),transparent);mix-blend-mode:screen}
.glass{background:linear-gradient(180deg,rgba(255,255,255,.16),rgba(255,255,255,.07));border:1px solid var(--line);box-shadow:inset 0 1px 0 rgba(255,255,255,.26),0 18px 45px rgba(0,0,0,.28);backdrop-filter:blur(26px) saturate(170%);-webkit-backdrop-filter:blur(26px) saturate(170%)}
button{color:var(--fg);border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.14),rgba(255,255,255,.07));box-shadow:inset 0 1px 0 rgba(255,255,255,.2),0 8px 22px rgba(0,0,0,.18);padding:13px 10px;font-size:14px;font-weight:720;flex:1;min-width:0}
button:active,button.on{background:rgba(255,255,255,.9);color:#08080a;border-color:rgba(255,255,255,.92)}
button.small{flex:0 0 auto;width:auto;padding:8px 12px;font-size:12px;border-radius:14px;white-space:nowrap}
.top{position:sticky;top:0;z-index:30;padding:8px 8px calc(8px + env(safe-area-inset-top));display:grid;grid-template-columns:repeat(4,1fr) auto;gap:7px;background:rgba(7,7,8,.58);backdrop-filter:blur(28px);-webkit-backdrop-filter:blur(28px);border-bottom:1px solid rgba(255,255,255,.12)}
#fsbtn{width:48px;font-size:18px}
#gate{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:22px;background:radial-gradient(circle at 50% 0,#444 0,#141416 46%,#070708 100%)}
#gate h1{margin:0;font-size:38px;letter-spacing:-.4px}#gate h1 span{font-weight:850}#gate h1 b{font-weight:500;color:rgba(255,255,255,.58)}
#pin{font-size:32px;text-align:center;width:210px;letter-spacing:8px;padding:15px;border-radius:20px;border:1px solid var(--line2);background:rgba(255,255,255,.1);color:var(--fg);box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}
.hint{color:var(--mut);font-size:12px;text-align:center}.err{color:var(--err)}
/* height (not just max-height) is required: every child below is position:absolute, so with
   no height set the box has no in-flow content to size itself from and collapses to
   min-height - that collapse is what made the non-fullscreen mirror look "cut in half". */
#screenwrap{position:sticky;top:58px;z-index:20;background:#000;min-height:180px;height:58vh;max-height:58vh;overflow:hidden;border-bottom:1px solid rgba(255,255,255,.12)}
#screenwrap:fullscreen,#screenwrap:-webkit-full-screen{height:100vh;max-height:100vh;width:100vw;background:#000}
/* Two stacked frames (only one ever visible) instead of one <img> whose src keeps changing -
   swapping src on a live element flashes it blank on slow e-ink WebKit (Kindle) while the new
   frame decodes. Each new frame loads into the HIDDEN one and only swaps visibility once fully
   decoded, so the visible frame never blanks. #screenhit is a separate, never-swapped element
   that owns all tap/drag handling so touch input isn't affected by which frame is on top, and
   sits above .tag/.toolbar (explicit z-index) so nothing can steal a tap meant for the mirror. */
.screenimg{position:absolute;inset:0;margin:auto;max-width:100%;max-height:58vh;display:block;object-fit:contain;pointer-events:none}
#screenhit{position:absolute;inset:0;touch-action:none;z-index:5}
/* Real fullscreen reserves a strip at the bottom for #fscontrols (mouse pad + keys) so a phone
   used fullscreen works like a tablet - mirror on top, controls always reachable below - instead
   of the mirror eating the whole screen with no way to tap/type without leaving fullscreen. */
#screenwrap:fullscreen .screenimg,#screenwrap:-webkit-full-screen .screenimg{max-width:100vw;max-height:calc(100vh - 84px);width:100vw;height:calc(100vh - 84px);bottom:84px}
#screenwrap:fullscreen #screenhit,#screenwrap:-webkit-full-screen #screenhit{bottom:84px}
.tag{position:absolute;top:10px;left:10px;padding:6px 11px;border-radius:999px;color:var(--fg);font-size:12px;font-weight:760;background:rgba(0,0,0,.46);border:1px solid rgba(255,255,255,.18);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
.toolbar{position:absolute;top:9px;right:9px;display:flex;gap:6px}.toolbar button{background:rgba(0,0,0,.44);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
#fscontrols{display:none;position:absolute;left:0;right:0;bottom:0;height:84px;padding:8px 10px;gap:8px;align-items:center;background:rgba(9,9,11,.88);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);z-index:15;box-sizing:border-box}
#screenwrap:fullscreen #fscontrols,#screenwrap:-webkit-full-screen #fscontrols{display:flex}
#fsPad{flex:1;height:100%;border-radius:16px;border:1px dashed rgba(255,255,255,.3);touch-action:none}
#fscontrols button{width:52px;height:100%;flex:0 0 auto;font-size:15px}
#fsKbRow{display:none;position:absolute;left:0;right:0;bottom:84px;padding:8px 10px;gap:8px;background:rgba(9,9,11,.88);backdrop-filter:blur(20px);z-index:15;box-sizing:border-box}
#screenwrap.fskb #fsKbRow{display:flex}
#fsKbRow input{flex:1;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.11);color:var(--fg);padding:12px;font-size:16px;outline:none}
#fsKbRow button{width:70px;flex:0 0 auto}
.pane{padding:12px}.lbl{color:var(--faint);font-size:11px;font-weight:850;text-transform:uppercase;letter-spacing:.08em;margin:10px 4px 7px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:9px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:9px}.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:9px}
#pad,#bigpad{border:1px dashed rgba(255,255,255,.28);border-radius:24px;color:var(--mut);display:flex;align-items:center;justify-content:center;text-align:center;touch-action:none;min-height:148px;margin-bottom:10px}
#bigpad{height:calc(100vh - 262px);min-height:330px;font-size:15px}
#kb{display:flex;gap:8px;margin-bottom:14px}#kb input,#chatInput,#livekb{width:100%;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.11);color:var(--fg);padding:14px;font-size:16px;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.15)}
#kb button{flex:0 0 auto;width:auto;white-space:nowrap}
#livekb{min-height:120px;resize:none}.foot{height:26px}
#chatPane{display:none;height:calc(100vh - 74px);padding:12px;grid-template-rows:minmax(0,1fr) auto;gap:10px}
#chatLog{overflow:auto;border-radius:26px;padding:12px;display:flex;flex-direction:column;gap:9px}
.msg{max-width:92%;padding:10px 12px;border-radius:18px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.13);white-space:pre-wrap;font-size:14px;line-height:1.35}
.msg.user{align-self:flex-end;background:rgba(255,255,255,.88);color:#08080a}.msg.system,.msg.tool{align-self:center;color:var(--mut);font-size:12px}.msg.assistant{align-self:flex-start}
.chatBox{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}.chatBox button{width:86px}.quick{display:flex;gap:7px;overflow:auto;padding-bottom:3px}.quick button{white-space:nowrap;flex:0 0 auto}
.dragOn{background:rgba(255,255,255,.9)!important;color:#09090a!important}
/* Landscape "fake fullscreen" for devices without the Fullscreen API (e.g. Kindle): rotate the
   mirror 90° and fill the screen so a portrait e-reader is used as a landscape monitor. */
#fsexit{display:none}
body.fakefs{overflow:hidden}
body.fakefs #screenwrap{position:fixed;top:50%;left:50%;width:100vh;height:100vw;max-height:none;min-height:0;transform:translate(-50%,-50%) rotate(90deg);transform-origin:center center;background:#000;z-index:60;border:0}
body.fakefs .screenimg{max-width:100%;max-height:100%;width:auto;height:auto}
body.fakefs .toolbar,body.fakefs .tag,body.fakefs .top{display:none}
body.fakefs #fsexit{display:block;position:fixed;z-index:70;left:8px;top:50%;transform:translateY(-50%) rotate(90deg);transform-origin:center center;background:rgba(0,0,0,.55);font-size:13px;padding:8px 13px;border-radius:14px;flex:0 0 auto;width:auto}
@media(max-width:410px){button{font-size:13px;padding:12px 8px}.top{gap:5px}.toolbar{left:10px;right:auto;top:auto;bottom:10px}.tag{top:10px}}
/* iPad / large tablets: roomier layout, bigger mirror + touch targets, centred content.
   (Add to Home Screen launches it standalone like a native app; iPadOS Safari also gives
   real fullscreen so the mirror uses the OS Fullscreen API, not the Kindle rotate fallback.) */
@media(min-width:760px){
  body{max-width:1000px;margin:0 auto}
  .top{gap:10px;padding:12px 16px calc(12px + env(safe-area-inset-top))}
  button{font-size:16px;padding:16px 12px;border-radius:20px}
  button.small{font-size:13px;padding:9px 14px}
  #fsbtn{width:56px;font-size:20px}
  #screenwrap{height:72vh;max-height:72vh}
  .screenimg{max-height:72vh}
  .pane{padding:18px;max-width:760px;margin:0 auto}
  .grid4,.grid3,.grid2{gap:12px}
  #bigpad{min-height:420px}
  #pin{font-size:34px;width:260px}
  .msg{font-size:15px;max-width:80%}
}
</style></head><body>
<div id=gate>
  <h1><span>Ember</span><b> Link</b></h1>
  <div class=hint>Enter the PIN shown in the desktop app</div>
  <input id=pin inputmode=numeric placeholder="----">
  <button onclick="connect()" style="width:210px">Connect</button>
  <div id=err class="hint err"></div>
</div>

<nav class=top>
  <button id=m_full class=on onclick="setMode('full')">Mirror</button>
  <button id=m_mouse onclick="setMode('mouse')">Mouse</button>
  <button id=m_kb onclick="setMode('kb')">Keys</button>
  <button id=m_chat onclick="setMode('chat')">Chat</button>
  <button id=fsbtn onclick="toggleFS()" title=Fullscreen>⛶</button>
</nav>
<button id=fsexit onclick="toggleFS()">✕ Exit fullscreen</button>

<div id=screenwrap class=modepane data-mode=full>
  <img id=screenA class=screenimg decoding=async>
  <img id=screenB class=screenimg decoding=async style="display:none">
  <div id=screenhit></div>
  <div class=tag id=tag>live</div>
  <div class=toolbar>
    <button class=small id=qbtn onclick="cycleQuality()">Speed</button>
    <button class=small id=spdbtn onclick="cycleSpeed()">Fast</button>
  </div>
  <div id=fsKbRow><input id=fsKbInput placeholder="Type, then Send"><button onclick="sendFsText()">Send</button></div>
  <div id=fscontrols>
    <div id=fsPad></div>
    <button onpointerdown="ev('click')">L</button>
    <button onpointerdown="ev('rclick')">R</button>
    <button onclick="toggleFsKb()">⌨</button>
  </div>
</div>

<section class="pane modepane" data-mode=full>
  <div class=lbl>Mirror control</div>
  <div class=grid3>
    <button onpointerdown="ev('click')">Left</button>
    <button onpointerdown="ev('rclick')">Right</button>
    <button onpointerdown="ev('dclick')">Double</button>
  </div>
  <div class=grid2>
    <button id=dragBtn onclick="toggleDragLock()">Drag Lock</button>
    <button onclick="toggleFS()">Fullscreen Mirror</button>
  </div>
  <div class=hint style="padding-bottom:10px">Tap the mirror to click. Drag directly on it to drag items, sliders, or selections.</div>

  <div class=lbl>Quick actions</div>
  <div class=grid3>
    <button onpointerdown="macro('lock')">Lock PC</button>
    <button onpointerdown="macro('sleep_display')">Sleep</button>
    <button onpointerdown="macro('mute')">Mute</button>
  </div>
  <div class=grid3>
    <button onpointerdown="macro('unmute')">Unmute</button>
    <button onpointerdown="macro('mute_mic')">Mic Off</button>
    <button onpointerdown="macro('unmute_mic')">Mic On</button>
  </div>
  <div id=kb><input id=cmdx placeholder="run a command, e.g. open -a Safari"><button onclick="runcmd()" style="max-width:90px">Run</button></div>

  <div class=lbl>Scroll</div>
  <div class=grid4>
    <button onpointerdown="scroll(5)">▲▲</button><button onpointerdown="scroll(2)">▲</button>
    <button onpointerdown="scroll(-2)">▼</button><button onpointerdown="scroll(-5)">▼▼</button>
  </div>

  <div class=lbl>Keys</div>
  <div class=grid4>
    <button onpointerdown="key('escape')">Esc</button><button onpointerdown="key('tab')">Tab</button>
    <button onpointerdown="key('delete')">Del</button><button onpointerdown="key('enter')">Enter</button>
  </div>
  <div class=grid4>
    <button onpointerdown="key('left')">←</button><button onpointerdown="key('up')">↑</button>
    <button onpointerdown="key('down')">↓</button><button onpointerdown="key('right')">→</button>
  </div>

  <div class=lbl>Shortcuts</div>
  <div class=grid4>
    <button onpointerdown="key('cmd+c')">Copy</button><button onpointerdown="key('cmd+v')">Paste</button>
    <button onpointerdown="key('cmd+x')">Cut</button><button onpointerdown="key('cmd+z')">Undo</button>
  </div>
  <div class=grid4>
    <button onpointerdown="key('cmd+a')">All</button><button onpointerdown="key('cmd+space')">Spotlight</button>
    <button onpointerdown="key('cmd+tab')">Apps</button><button onpointerdown="key('cmd+w')">Close</button>
  </div>

  <div class=lbl>Trackpad</div>
  <div id=pad class=glass>Drag here to move. Enable Drag Lock to hold while moving.</div>

  <div class=lbl>Type text</div>
  <div id=kb><input id=tx placeholder="type, then Send"><button onclick="sendtext()" style="max-width:90px">Send</button></div>
  <div class=foot></div>
</section>

<section id=s_mouse class="pane modepane" data-mode=mouse style="display:none">
  <div class=lbl>Trackpad</div>
  <div id=bigpad class=glass>Move anywhere here. Tap to click. Drag Lock holds the mouse button down.</div>
  <div class=grid3>
    <button onpointerdown="ev('click')">Left</button><button onpointerdown="ev('rclick')">Right</button><button onpointerdown="ev('dclick')">Double</button>
  </div>
  <div class=grid2><button id=dragBtn2 onclick="toggleDragLock()">Drag Lock</button><button onpointerdown="ev('up')">Release</button></div>
  <div class=grid4>
    <button onpointerdown="scroll(5)">▲▲</button><button onpointerdown="scroll(2)">▲</button>
    <button onpointerdown="scroll(-2)">▼</button><button onpointerdown="scroll(-5)">▼▼</button>
  </div>
  <div class=foot></div>
</section>

<section id=s_kb class="pane modepane" data-mode=kb style="display:none">
  <div class=lbl>Live keyboard</div>
  <textarea id=livekb rows=3 placeholder="Tap here and type straight into the computer"></textarea>
  <div class=grid4>
    <button onpointerdown="key('left')">←</button><button onpointerdown="key('up')">↑</button>
    <button onpointerdown="key('down')">↓</button><button onpointerdown="key('right')">→</button>
  </div>
  <div class=grid4>
    <button onpointerdown="key('escape')">Esc</button><button onpointerdown="key('tab')">Tab</button>
    <button onpointerdown="key('delete')">Del</button><button onpointerdown="key('enter')">Enter</button>
  </div>
  <div class=grid4>
    <button onpointerdown="key('cmd+c')">Copy</button><button onpointerdown="key('cmd+v')">Paste</button>
    <button onpointerdown="key('cmd+x')">Cut</button><button onpointerdown="key('cmd+z')">Undo</button>
  </div>
</section>

<section id=chatPane class=modepane data-mode=chat>
  <div id=chatLog class=glass></div>
  <div>
    <div class=quick>
      <button class=small data-chat="Look at my screen and tell me what to do next.">Read screen</button>
      <button class=small data-chat="Click the right thing on screen and continue.">Do next step</button>
      <button class=small data-chat="Summarize what is open on my computer.">Summarize</button>
      <button class=small data-chat="Stop what you are doing.">Stop</button>
    </div>
    <div class=chatBox>
      <textarea id=chatInput rows=2 placeholder="Tell Ember what to do on the computer"></textarea>
      <button onclick="sendChat()">Send</button>
    </div>
  </div>
</section>

<script>
let PIN=localStorage.getItem("ember_pin")||"",TOK=localStorage.getItem("ember_tok")||"",SW=0,SH=0,MODE="full",lastUrl="",fetching=false,dragLock=false,chatLoop=false,FAKEFS=false;
let front=document.getElementById("screenA"),back=document.getElementById("screenB"),hit=document.getElementById("screenhit");
// Default to the lightest tier - a big JPEG's decode() alone (needed to kill the Kindle
// black-flash) is real work on weak/e-ink hardware, so "Balanced" as the default made the very
// first impression laggy. Power users can still bump it up with the quality toggle.
let QUAL=[{label:"Speed",maxw:1100,q:62,hd:0},{label:"Balanced",maxw:1500,q:78,hd:1},{label:"Sharp",maxw:1920,q:86,hd:1}],QI=0;
let SPEEDS=[90,160,300,650],SLABEL=["Ultra","Fast","Smooth","Lite"],SPI=1,SPEED=SPEEDS[SPI];
async function post(o){o.pin=PIN;o.tok=TOK;try{return (await fetch("/api/event",{method:"POST",body:JSON.stringify(o)})).ok}catch(e){return false}}
async function postR(o){o.pin=PIN;o.tok=TOK;try{let r=await fetch("/api/event",{method:"POST",body:JSON.stringify(o)});if(!r.ok)return{ok:false};try{return await r.json();}catch(e){return{ok:true};}}catch(e){return{ok:false,detail:"can't reach Ember"};}}
function screenUrl(){let q=QUAL[QI];return `/api/screen?pin=${encodeURIComponent(PIN)}&tok=${encodeURIComponent(TOK)}&hd=${q.hd}&maxw=${q.maxw}&q=${q.q}&t=${Date.now()}`}
async function pair(){try{let r=await fetch("/api/pair",{method:"POST",body:JSON.stringify({pin:PIN,tok:TOK})});if(r.ok){let j=await r.json();if(j&&j.token){TOK=j.token;localStorage.setItem("ember_tok",TOK);}}}catch(e){}}
function go(){document.getElementById("gate").style.display="none";loop();pollChat()}
async function connect(){PIN=document.getElementById("pin").value.trim();let e=document.getElementById("err");e.textContent="";try{let r=await fetch(screenUrl(),{cache:"no-store"});if(!r.ok){e.textContent="Wrong PIN";return}SW=+r.headers.get("X-Screen-W");SH=+r.headers.get("X-Screen-H");localStorage.setItem("ember_pin",PIN);await pair();go()}catch(err){e.textContent="Can't reach Ember - check you're on the same Wi-Fi and that Ember Link is running."}}
async function tryAuto(){if(!TOK&&!PIN)return false;try{let r=await fetch(screenUrl(),{cache:"no-store"});if(r.ok){SW=+r.headers.get("X-Screen-W");SH=+r.headers.get("X-Screen-H");go();return true;}}catch(e){}return false;}
async function loop(){if(MODE!=="full"||document.hidden){setTimeout(loop,450);return}if(fetching){setTimeout(loop,70);return}fetching=true;let start=performance.now();
 try{let r=await fetch(screenUrl(),{cache:"no-store"});if(r.ok){let b=await r.blob();let url=URL.createObjectURL(b);
  // Load the new frame into the HIDDEN image and wait until it's fully decoded (not just
  // downloaded - decode() guarantees paint-ready, unlike onload on old WebKit) before swapping
  // visibility. The VISIBLE image's src is never touched, so it can't flash blank mid-frame
  // (the Kindle e-ink flicker bug).
  back.src=url;
  // Wait for the frame to be paint-ready, but NEVER hang the loop: if decode() rejects on a
  // truncated frame the error event has usually already fired, so a bare onload/onerror Promise
  // would never settle and freeze the whole mirror. Guard with back.complete + a timeout.
  let settle=()=>new Promise(res=>{if(back.complete){res();return}back.onload=res;back.onerror=res;setTimeout(res,400);});
  try{if(back.decode)await back.decode();else await settle();}
  catch(e){await settle();}
  front.style.display="none";back.style.display="block";
  let oldFront=front,oldUrl=lastUrl;front=back;back=oldFront;lastUrl=url;
  if(oldUrl)URL.revokeObjectURL(oldUrl);
  SW=+r.headers.get("X-Screen-W")||SW;SH=+r.headers.get("X-Screen-H")||SH;document.getElementById("tag").textContent=`live · ${Math.round(b.size/1024)} KB`}}catch(e){document.getElementById("tag").textContent="reconnecting"}finally{fetching=false;setTimeout(loop,Math.max(35,SPEED-(performance.now()-start)))}}
function imgRect(){
 // Map taps to the ACTUAL rendered mirror image, not the full #screenhit box. The image is
 // letterboxed (object-fit:contain) inside a fixed-height wrap, so on a portrait phone against a
 // wide desktop the image fills only part of the box - measuring the box would send every tap to
 // the wrong desktop coordinate. Derive the contained image rect from its natural size.
 let el=front,b=el.getBoundingClientRect();
 let nw=el.naturalWidth||SW||b.width,nh=el.naturalHeight||SH||b.height;
 if(!nw||!nh||!b.width||!b.height)return b;
 let scale=Math.min(b.width/nw,b.height/nh),w=nw*scale,h=nh*scale;
 return {left:b.left+(b.width-w)/2,top:b.top+(b.height-h)/2,width:w,height:h};}
function pxy(e){let b=imgRect();let rx=(e.clientX-b.left)/b.width,ry=(e.clientY-b.top)/b.height;let x,y;
 if(FAKEFS){x=ry;y=1-rx;}else{x=rx;y=ry;}   // landscape fake-fullscreen rotates the mirror 90° CW
 if(x<0||x>1||y<0||y>1)return null;return{x:Math.round(x*SW),y:Math.round(y*SH),cx:e.clientX,cy:e.clientY}}
let sd=null,screenDrag=false,lastDrag=0;
hit.addEventListener("pointerdown",e=>{let p=pxy(e);if(!p)return;e.preventDefault();hit.setPointerCapture&&hit.setPointerCapture(e.pointerId);sd=p;screenDrag=dragLock;if(dragLock)post({t:"dragstart",x:p.x,y:p.y});});
hit.addEventListener("pointermove",e=>{if(!sd)return;let p=pxy(e);if(!p)return;e.preventDefault();let moved=Math.abs(p.cx-sd.cx)+Math.abs(p.cy-sd.cy);if(!screenDrag&&moved>9){screenDrag=true;post({t:"dragstart",x:sd.x,y:sd.y});document.getElementById("tag").textContent="dragging"}if(screenDrag&&performance.now()-lastDrag>16){post({t:"dragto",x:p.x,y:p.y});lastDrag=performance.now()}});
function screenEnd(e){if(!sd)return;let p=pxy(e)||sd;e.preventDefault();if(screenDrag){post({t:"dragend",x:p.x,y:p.y});document.getElementById("tag").textContent="dropped"}else{post({t:"clickat",x:p.x,y:p.y});document.getElementById("tag").textContent="clicked"}sd=null;screenDrag=false;setTimeout(()=>document.getElementById("tag").textContent="live",450)}
hit.addEventListener("pointerup",screenEnd);hit.addEventListener("pointercancel",screenEnd);
function attachWheelScroll(el){if(!el)return;let accum=0,last=0;
 el.addEventListener("wheel",e=>{e.preventDefault();accum-=e.deltaY;let now=performance.now();if(now-last>16){post({t:"scroll",a:Math.max(-8,Math.min(8,Math.round(accum/30)))});accum=0;last=now}},{passive:false});}
function attachPad(pad){if(!pad)return;let lx=0,ly=0,moving=false,moved=0,ax=0,ay=0,last=0,held=false;
 pad.addEventListener("pointerdown",e=>{e.preventDefault();moving=true;moved=0;ax=ay=0;lx=e.clientX;ly=e.clientY;pad.classList.add("dragOn");pad.setPointerCapture&&pad.setPointerCapture(e.pointerId);if(dragLock){held=true;post({t:"down"})}});
 pad.addEventListener("pointermove",e=>{if(!moving)return;e.preventDefault();let dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;moved+=Math.abs(dx)+Math.abs(dy);ax+=dx;ay+=dy;let now=performance.now();if(now-last>16){post({t:"move",dx:Math.round(ax*1.55),dy:Math.round(ay*1.55)});ax=ay=0;last=now}});
 function end(e){if(!moving)return;e&&e.preventDefault();if(Math.round(ax)||Math.round(ay))post({t:"move",dx:Math.round(ax*1.55),dy:Math.round(ay*1.55)});if(held)post({t:"up"});else if(moved<7)post({t:"click"});moving=false;held=false;ax=ay=0;pad.classList.remove("dragOn")}
 pad.addEventListener("pointerup",end);pad.addEventListener("pointercancel",end);
 // A physical two-finger scroll gesture on a trackpad/mouse fires "wheel" events even over a
 // plain div - the pointer handlers above only cover single-pointer drag-to-move, so without
 // this a two-finger scroll here silently did nothing.
 attachWheelScroll(pad);}
attachPad(document.getElementById("pad"));attachPad(document.getElementById("bigpad"));
attachWheelScroll(hit);   // two-finger scroll over the live screen mirror also scrolls the desktop
// The fullscreen bottom bar's own mini trackpad - reuses the exact same drag/tap/scroll code as
// the Mouse tab's trackpad, so fullscreen works like a tablet (mirror + touch controls always
// reachable together) instead of forcing an exit-fullscreen round trip to move the mouse.
attachPad(document.getElementById("fsPad"));attachWheelScroll(document.getElementById("fsPad"));
function ev(k){post({t:k})}function scroll(a){post({t:"scroll",a:a})}function key(k){post({t:"key",k:k})}
function flash(m){let t=document.getElementById("tag");if(t){t.textContent=m;setTimeout(()=>{t.textContent="live"},900)}}
async function macro(n){let label=n.replace(/_/g," ");let r=await postR({t:"macro",name:n});if(r&&r.ok!==false){flash(label+" ✓")}else{flash("✗ "+((r&&r.detail)||label+" failed"))}}
async function runcmd(){let i=document.getElementById("cmdx");if(!i||!i.value)return;let cmd=i.value;i.value="";let r=await postR({t:"macro_cmd",cmd:cmd});if(r&&r.ok!==false){flash("ran ✓")}else{flash("✗ "+((r&&r.detail)||"command failed"))}}
function sendtext(){let i=document.getElementById("tx");if(i.value){post({t:"type",text:i.value});i.value=""}}
function toggleFsKb(){let on=document.getElementById("screenwrap").classList.toggle("fskb");if(on)setTimeout(()=>document.getElementById("fsKbInput").focus(),60)}
function sendFsText(){let i=document.getElementById("fsKbInput");if(i.value){post({t:"type",text:i.value});i.value=""}}
function setMode(m){MODE=m;document.querySelectorAll(".modepane").forEach(el=>{el.style.display=el.dataset.mode===m?(m==="chat"?"grid":""):"none"});["full","mouse","kb","chat"].forEach(x=>document.getElementById("m_"+x).classList.toggle("on",x===m));if(m==="kb")setTimeout(()=>document.getElementById("livekb").focus(),60);if(m==="chat")pollChat()}
function fakeFS(on){FAKEFS=on;document.body.classList.toggle("fakefs",FAKEFS);flash(FAKEFS?"landscape":"live")}
function toggleFS(){
 if(FAKEFS){fakeFS(false);return}   // exit button / re-tap while already in fake-fullscreen
 if(document.fullscreenElement||document.webkitFullscreenElement){(document.exitFullscreen||document.webkitExitFullscreen).call(document);return}
 let d=document.getElementById("screenwrap"),req=d.requestFullscreen||d.webkitRequestFullscreen;
 if(!req){fakeFS(true);return}
 // Some WebKit builds (Kindle Silk) expose requestFullscreen but never actually enter it - no
 // event, no rejection, just silent no-op. Don't trust feature-detection: try it, then verify
 // shortly after and fall back to rotating the mirror ourselves if nothing actually happened.
 try{let p=req.call(d);if(p&&p.catch)p.catch(()=>fakeFS(true))}catch(e){fakeFS(true);return}
 setTimeout(()=>{if(!document.fullscreenElement&&!document.webkitFullscreenElement&&!FAKEFS)fakeFS(true)},350);}
function cycleQuality(){QI=(QI+1)%QUAL.length;document.getElementById("qbtn").textContent=QUAL[QI].label}
function cycleSpeed(){SPI=(SPI+1)%SPEEDS.length;SPEED=SPEEDS[SPI];document.getElementById("spdbtn").textContent=SLABEL[SPI]}
function toggleDragLock(){dragLock=!dragLock;["dragBtn","dragBtn2"].forEach(id=>{let b=document.getElementById(id);if(b){b.classList.toggle("dragOn",dragLock);b.textContent=dragLock?"Dragging On":"Drag Lock"}});if(!dragLock)post({t:"up"})}
let livekb=document.getElementById("livekb");livekb.addEventListener("keydown",e=>{let k=e.key;if(k.length===1){post({t:"type",text:k});e.preventDefault()}else if(k==="Backspace"){key("backspace");e.preventDefault()}else if(k==="Enter"){key("enter");e.preventDefault()}else if(k==="Tab"){key("tab");e.preventDefault()}else if(k.indexOf("Arrow")===0){key(k.slice(5).toLowerCase());e.preventDefault()}livekb.value=""});
function esc(s){return String(s||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]))}
function renderChat(items){let log=document.getElementById("chatLog");log.innerHTML=(items&&items.length?items:[{role:"system",text:"Remote chat is ready. Tell Ember what to do on the desktop."}]).map(m=>`<div class="msg ${esc(m.role)}">${esc(m.text)}</div>`).join("");log.scrollTop=log.scrollHeight}
function pollChat(){if(chatLoop||(!PIN&&!TOK))return;chatLoop=true;chatTick()}
async function chatTick(){if(!PIN&&!TOK){chatLoop=false;return}try{let r=await fetch("/api/chat?pin="+encodeURIComponent(PIN)+"&tok="+encodeURIComponent(TOK),{cache:"no-store"});if(r.ok){let j=await r.json();renderChat(j.messages||[])}}catch(e){}setTimeout(chatTick,MODE==="chat"?750:1800)}
async function sendChat(){let i=document.getElementById("chatInput"),text=i.value.trim();if(!text)return;i.value="";await fetch("/api/chat",{method:"POST",body:JSON.stringify({pin:PIN,tok:TOK,text})});pollChat()}
document.querySelectorAll("[data-chat]").forEach(b=>b.addEventListener("click",()=>{document.getElementById("chatInput").value=b.dataset.chat;sendChat()}));
document.getElementById("chatInput").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendChat()}});
// A magic-link fragment (#tok=…) carries the pairing token to THIS origin. localStorage is
// per-ORIGIN, so a phone that paired on the LAN origin (http://192.168.x.x:8765) has NO token on
// the public tunnel origin (https://…trycloudflare.com) - "open the link from anywhere" could
// never authenticate. Read the token out of the URL, persist it for this origin, then strip it
// from the address bar so it isn't left in history or a screenshot.
(function(){try{let m=(location.hash||"").match(/tok=([^&]+)/);if(m&&m[1]){TOK=decodeURIComponent(m[1]);localStorage.setItem("ember_tok",TOK);history.replaceState(null,"",location.pathname+location.search);}}catch(e){}})();
// Already paired on this device? Reconnect automatically (works from any network via the token).
tryAuto();
</script></body></html>"""


# PWA install assets (so an iPad/phone can "Add to Home Screen" and launch Ember Link
# standalone, like a native app). Served without a PIN — they hold no private data.
_ICON_CACHE = {"bytes": None}


def _icon_bytes() -> bytes:
    if _ICON_CACHE["bytes"] is None:
        import os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        try:
            with open(p, "rb") as f:
                _ICON_CACHE["bytes"] = f.read()
        except Exception:
            _ICON_CACHE["bytes"] = b""
    return _ICON_CACHE["bytes"]


MANIFEST = json.dumps({
    "name": "Ember Link",
    "short_name": "Ember",
    "description": "Control your computer from this device — Ember Link.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#070708",
    "theme_color": "#070708",
    "icons": [
        {"src": "/icon.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
})


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence console spam
        pass

    def _auth(self, pin, tok=None):
        """Authorise a request by the short PIN (LAN-only) OR a long pairing token (anywhere).
        The PIN is deliberately rejected for non-LAN-looking source addresses - see _is_lan_ip -
        so a public tunnel can never be brute-forced with the 6-digit PIN, only a real token."""
        ip = self.client_address[0] if self.client_address else "?"
        # A valid 32-byte pairing token is NOT brute-forceable and must be honoured BEFORE the
        # PIN lockout is consulted. Every tunnel-relayed request shares ip 127.0.0.1, so if the
        # lockout gated token auth too, anyone who found the public URL could send 5 junk PINs
        # and lock out every legitimately-paired device (persistent remote-access DoS). The
        # lockout therefore only ever throttles PIN attempts.
        if _token_valid(tok):
            _STATE["last_active"] = time.time()  # refresh the idle-timeout watchdog
            return True
        if _auth_locked(ip):
            time.sleep(1.0)  # slow even the rejection while locked out
            return False
        pin_ok = (bool(_STATE["pin"]) and _is_lan_ip(ip)
                  and secrets.compare_digest(str(pin or ""), str(_STATE["pin"])))
        if pin_ok:
            _STATE["last_active"] = time.time()  # refresh the idle-timeout watchdog
        else:
            time.sleep(0.3)  # cap brute-force throughput
        _auth_record(ip, pin_ok)
        return pin_ok

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/manifest.webmanifest") or self.path.startswith("/manifest.json"):
            body = MANIFEST.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/icon.png") or self.path.startswith("/apple-touch-icon"):
            data = _icon_bytes()
            if not data:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/api/screen"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if not self._auth((q.get("pin") or [""])[0], (q.get("tok") or [""])[0]):
                self.send_response(403); self.end_headers(); return
            try:
                hd = (q.get("hd") or ["1"])[0] != "0"
                max_w = int((q.get("maxw") or ["0"])[0] or 0) or None
                quality = int((q.get("q") or ["0"])[0] or 0) or None
                data, lw, lh = _capture(hd=hd, max_w=max_w, quality=quality)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("X-Screen-W", str(lw))
                self.send_header("X-Screen-H", str(lh))
                self.send_header("X-Frame-Bytes", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(500); self.end_headers()
            return
        if self.path.startswith("/api/chat"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if not self._auth((q.get("pin") or [""])[0], (q.get("tok") or [""])[0]):
                self.send_response(403); self.end_headers(); return
            body = json.dumps({"ok": True, "messages": _chat_snapshot()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/pair":
            # A device on the LAN proves it knows the PIN (or already holds a token) and gets a
            # long pairing token, so it can reconnect from anywhere afterwards.
            try:
                n = int(self.headers.get("Content-Length", 0))
                o = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                o = {}
            if not self._auth(o.get("pin"), o.get("tok")):
                self.send_response(403); self.end_headers(); return
            body = json.dumps({"ok": True, "token": issue_pair_token()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/chat":
            try:
                n = int(self.headers.get("Content-Length", 0))
                o = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                o = {}
            if not self._auth(o.get("pin"), o.get("tok")):
                self.send_response(403); self.end_headers(); return
            text = str(o.get("text", "")).strip()
            if not text:
                self.send_response(400); self.end_headers(); return
            _chat_add("user", text)
            handler = _CHAT_HANDLER
            if handler is None:
                _chat_add("system", "Ember Link is connected, but the desktop AI bridge is not ready yet.")
            else:
                try:
                    handler(text)
                except Exception as e:
                    _chat_add("system", f"Could not send to Ember: {type(e).__name__}: {e}")
            body = json.dumps({"ok": True, "messages": _chat_snapshot()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/api/event":
            self.send_response(404); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            o = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            o = {}
        if not self._auth(o.get("pin"), o.get("tok")):
            self.send_response(403); self.end_headers(); return
        try:
            result = _apply(o)
            if isinstance(result, dict):
                # Macros (Lock PC / Mic Off / run-command) return a real {ok, detail}; hand it
                # back so the phone can flash the ACTUAL outcome instead of always claiming "✓"
                # even when the action failed (e.g. mic mute unsupported on this OS).
                body = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(204); self.end_headers()
        except Exception:
            self.send_response(500); self.end_headers()


def _apply(o: dict):
    with _INPUT_LOCK:
        return _apply_locked(o)


def _apply_locked(o: dict):
    t = o.get("t")
    if t == "move":
        pyautogui.moveRel(int(o.get("dx", 0)), int(o.get("dy", 0)), duration=0)
    elif t == "moveto":
        pyautogui.moveTo(int(o.get("x", 0)), int(o.get("y", 0)), duration=0)
    elif t == "down":
        pyautogui.mouseDown()
    elif t == "up":
        pyautogui.mouseUp()
    elif t == "dragstart":
        pyautogui.moveTo(int(o.get("x", 0)), int(o.get("y", 0)), duration=0)
        pyautogui.mouseDown()
    elif t == "dragto":
        pyautogui.moveTo(int(o.get("x", 0)), int(o.get("y", 0)), duration=0)
    elif t == "dragend":
        pyautogui.moveTo(int(o.get("x", 0)), int(o.get("y", 0)), duration=0)
        pyautogui.mouseUp()
    elif t == "click":
        pyautogui.click()
    elif t == "clickat":
        # Move + click as ONE locked operation. Sending them as two separate POSTs let the
        # click race ahead of the move on a different server thread and land at the old cursor
        # position ("tapped the wrong thing" bugs the user couldn't reproduce).
        pyautogui.moveTo(int(o.get("x", 0)), int(o.get("y", 0)), duration=0)
        pyautogui.click()
    elif t == "dclick":
        pyautogui.doubleClick()
    elif t == "rclick":
        pyautogui.click(button="right")
    elif t == "scroll":
        pyautogui.scroll(int(o.get("a", 0)) * 80)
    elif t == "key":
        tools.press_key(str(o.get("k", "")))
    elif t == "type":
        tools.type_text(str(o.get("text", "")))
    elif t == "macro":
        return _run_macro(str(o.get("name", "")))
    elif t == "macro_cmd":
        return _run_shell_macro(str(o.get("cmd", "")))


# --- Quick one-tap macros (the phone toolbar) --------------------------------------------
# Each macro is a best-effort OS action triggered from the phone with a single tap (Gemini's
# "Lock PC / Mute Mic / custom command" idea). The desktop app can override any macro via
# set_macro_hooks(); tests inject fakes there. Kept tiny + stdlib-only so it's hermetically
# testable, and every entry point returns a dict and NEVER raises (so the HTTP handler can't 500).
_MACRO_HOOKS: dict = {}

# internal name -> phone button label
MACROS = [
    ("lock", "Lock PC"),
    ("mute", "Mute"),
    ("unmute", "Unmute"),
    ("mute_mic", "Mic Off"),
    ("unmute_mic", "Mic On"),
    ("sleep_display", "Sleep Screen"),
]
MACRO_NAMES = frozenset(n for n, _ in MACROS)


def set_macro_hooks(**hooks) -> None:
    """Override a macro's implementation (name -> callable() returning a dict/bool on success).
    The desktop app uses this to wire, e.g., 'lock' to its own secure lock."""
    for k, v in hooks.items():
        if callable(v):
            _MACRO_HOOKS[k] = v


def _macro_cmd(cmd) -> tuple:
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return (r.returncode == 0, (r.stdout or r.stderr or "").strip()[:200])
    except Exception as e:
        return (False, str(e)[:200])


def _default_macro(name: str) -> dict:
    """Best-effort OS implementation for a macro when the app hasn't injected a hook."""
    import sys
    if name == "lock":
        try:
            import panic
            r = panic._default_lock_screen()
            return {"ok": bool(r.get("ok")), "macro": name, "detail": r.get("detail", "locked")}
        except Exception as e:
            return {"ok": False, "macro": name, "detail": str(e)}
    if name == "sleep_display":
        if sys.platform == "darwin":
            ok, d = _macro_cmd(["pmset", "displaysleepnow"])
        elif sys.platform.startswith("win"):
            ok, d = _macro_cmd(["powershell", "-NoProfile", "-Command",
                "(Add-Type -Name M -Namespace W -PassThru -MemberDefinition "
                "'[DllImport(\"user32.dll\")]public static extern int "
                "SendMessage(int h,int m,int w,int l);')::SendMessage(-1,0x0112,0xF170,2)"])
        else:
            ok, d = _macro_cmd(["xset", "dpms", "force", "off"])
        return {"ok": ok, "macro": name, "detail": d or "display asleep"}
    if name in ("mute", "unmute"):
        on = (name == "mute")
        if sys.platform == "darwin":
            ok, d = _macro_cmd(["osascript", "-e",
                                f"set volume output muted {'true' if on else 'false'}"])
        elif sys.platform.startswith("win"):
            # VolumeMute is a toggle key; best-effort on Windows without extra tooling.
            ok, d = _macro_cmd(["powershell", "-NoProfile", "-Command",
                                "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"])
        else:
            ok, d = _macro_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if on else "0"])
        return {"ok": ok, "macro": name, "detail": d or ("muted" if on else "unmuted")}
    if name in ("mute_mic", "unmute_mic"):
        on = (name == "mute_mic")
        if sys.platform == "darwin":
            ok, d = _macro_cmd(["osascript", "-e",
                                f"set volume input volume {0 if on else 100}"])
        elif sys.platform.startswith("win"):
            return {"ok": False, "macro": name,
                    "detail": f"mic {'mute' if on else 'unmute'} needs nircmd on Windows"}
        else:
            ok, d = _macro_cmd(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "1" if on else "0"])
        return {"ok": ok, "macro": name, "detail": d or ("microphone muted" if on else "microphone unmuted")}
    return {"ok": False, "macro": name, "detail": "unhandled macro"}


def _run_macro(name: str) -> dict:
    """Run a quick macro by name (injected hook first, else the OS default). Never raises."""
    name = (name or "").strip().lower()
    if name not in MACRO_NAMES:
        return {"ok": False, "macro": name, "detail": "unknown macro"}
    hook = _MACRO_HOOKS.get(name)
    try:
        if hook is not None:
            res = hook()
            if isinstance(res, dict):
                res.setdefault("ok", True)
                res.setdefault("macro", name)
                return res
            return {"ok": (res is None or bool(res)), "macro": name, "detail": "ok"}
        return _default_macro(name)
    except Exception as e:
        return {"ok": False, "macro": name, "detail": f"{type(e).__name__}: {e}"}


def _run_shell_macro(cmd: str) -> dict:
    """Run a one-tap custom shell command from the phone (PIN-gated, LAN-only). Best-effort."""
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "detail": "empty command"}
    hook = _MACRO_HOOKS.get("shell")
    try:
        if hook is not None:
            res = hook(cmd)
            return res if isinstance(res, dict) else {"ok": bool(res), "detail": "ok"}
        return tools.run_powershell(cmd)
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def _data_dir():
    from pathlib import Path
    import sys
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


def stable_pin() -> str:
    """A PIN that stays the SAME across sessions and reboots (stored in app-support), so the
    user memorises it once and the boot-time remote always uses it."""
    f = _data_dir() / "remote_pin.txt"
    try:
        if f.exists():
            p = f.read_text().strip()
            if p.isdigit() and 4 <= len(p) <= 8:
                return p
    except OSError:
        pass
    # 6 digits (1,000,000 combos). Combined with the per-IP lockout below, that is
    # brute-force-resistant on a LAN; old 4-digit pins keep working until deleted.
    p = f"{secrets.randbelow(900000) + 100000}"
    _write_stable_pin(f, p)
    return p


def _write_stable_pin(f, p: str) -> None:
    """Write-then-rename so a crash/force-quit mid-write can never leave a truncated/corrupt
    pin file behind - a partial write there would silently look 'invalid' to stable_pin() on the
    next launch and mint a brand new (different!) pin, which is exactly the 'the PIN changed and
    now it says wrong' symptom this guards against."""
    try:
        import os
        tmp = f.with_suffix(f.suffix + f".tmp{os.getpid()}")
        tmp.write_text(p)
        os.replace(tmp, f)
    except OSError:
        pass


def _idle_watchdog(srv) -> None:
    """Auto-stop Ember Link after a stretch with no successful auth, to shrink the
    exposure window if the user forgets to stop it. Exits when the server changes."""
    while _STATE.get("server") is srv:
        timeout = _STATE.get("idle_timeout") or 0
        if timeout and time.time() - _STATE.get("last_active", 0) > timeout:
            stop()
            return
        time.sleep(15)


def start(port: int = 8765, pin: str | None = None, idle_timeout: float = 1800.0) -> dict:
    """Start Ember Link. Returns the phone URL + PIN to display in the UI.
    Uses a stable PIN by default so it's the same every session/boot.
    Auto-stops after `idle_timeout` seconds with no successful auth (0 disables)."""
    _load_tokens()
    if _STATE["server"]:
        return {"ok": True, "url": _STATE["url"], "pin": _STATE["pin"], "already_running": True}
    pin = pin or stable_pin()
    ip = _lan_ip()
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError as e:
        return {"ok": False, "error": f"could not bind port {port}: {e}"}
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _STATE.update(server=srv, thread=th, pin=pin, port=port, ip=ip,
                  url=f"http://{ip}:{port}", last_active=time.time(),
                  idle_timeout=idle_timeout)
    if idle_timeout and idle_timeout > 0:
        threading.Thread(target=_idle_watchdog, args=(srv,), daemon=True).start()
    return {"ok": True, "url": _STATE["url"], "pin": pin,
            "hint": f"On your phone (same Wi-Fi) open {_STATE['url']} and enter PIN {pin}"}


# Remote-access (beyond-Wi-Fi) tunnel — OFF by default; the user opts in via enable_remote().
_REMOTE = {"tunnel": None, "link": ""}


def enable_remote(port: int | None = None) -> dict:
    """Open a public tunnel so Ember Link is reachable from outside the Wi-Fi. The server must be
    running. The returned `link` embeds a fresh pairing token in its #fragment so opening it on a
    phone signs in automatically from ANY network - the short PIN is never exposed to the internet
    (the tunnel origin only ever accepts the token). Returns {ok, url, link, pin} or an error."""
    if not _STATE.get("server"):
        return {"ok": False, "error": "start Ember Link first"}
    try:
        import tunnel
    except Exception as e:
        return {"ok": False, "error": f"tunnel module unavailable: {e}"}
    tm = _REMOTE.get("tunnel")
    if tm is None:
        tm = tunnel.TunnelManager()
        _REMOTE["tunnel"] = tm
    res = tm.start(int(port or _STATE.get("port") or 8765))
    if res.get("ok"):
        _STATE["last_active"] = time.time()
        res["pin"] = _STATE.get("pin")
        # Bake a pairing token into the shareable link so roaming actually works (a phone can't
        # carry the token it earned on the LAN origin over to the tunnel origin - separate
        # localStorage). The link is unguessable (43-char token), so it's the credential; the
        # page reads it out of the #fragment and stores it for the tunnel origin.
        base = (res.get("url") or "").rstrip("/")
        link = f"{base}/#tok={issue_pair_token()}" if base else ""
        _REMOTE["link"] = link
        res["link"] = link
        res["hint"] = ("Open this link on your phone from any network - it signs in "
                       "automatically: " + (link or res.get("url", "")))
    return res


def disable_remote() -> dict:
    _REMOTE["link"] = ""
    tm = _REMOTE.get("tunnel")
    if tm is None:
        return {"ok": True, "stopped": False}
    r = tm.stop()
    return {"ok": True, "stopped": bool(r.get("stopped"))}


def remote_url() -> str:
    tm = _REMOTE.get("tunnel")
    return (tm.status().get("url") if tm else "") or ""


def remote_link() -> str:
    """The full shareable magic link (tunnel URL + embedded token), or '' if remote is off."""
    return _REMOTE.get("link") or ""


def stop() -> dict:
    srv = _STATE.get("server")
    disable_remote()  # tear down any public tunnel too
    if not srv:
        return {"ok": True, "stopped": False}
    try:
        srv.shutdown(); srv.server_close()
    except Exception:
        pass
    # Clear stale fields too, so status() doesn't report a live url after stop and a
    # later restart doesn't inherit an old streaming-bubble id.
    global _CHAT_STREAM_ID
    _STATE.update(server=None, thread=None, pin=None, url=None, port=None)
    _CHAT_STREAM_ID = None
    return {"ok": True, "stopped": True}


def status() -> dict:
    return {"ok": True, "running": bool(_STATE.get("server")),
            "url": _STATE.get("url"), "pin": _STATE.get("pin"),
            "remote_url": remote_url(), "remote_link": remote_link(),
            "paired": paired_count()}
