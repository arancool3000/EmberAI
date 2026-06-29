"""Ember Link - control this PC from a phone browser on the same Wi-Fi.

Self-contained: pure Python stdlib HTTP server (no Flask, no websockets, no cloud
backend). Ember starts it; you open the printed URL on your phone, enter the PIN, and
you get a live view of the PC screen plus a trackpad/keyboard. Tap the screen image to
click exactly there - so even with no working mouse/keyboard drivers on the PC, the phone
drives it.

Security: binds to the LAN, gated by a random per-session PIN. It's a manual remote, so
keep the PIN private and stop the server when done. Not meant to face the public internet.
"""
from __future__ import annotations

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
button.small{flex:0;padding:8px 11px;font-size:12px;border-radius:14px}
.top{position:sticky;top:0;z-index:30;padding:8px 8px calc(8px + env(safe-area-inset-top));display:grid;grid-template-columns:repeat(4,1fr) auto;gap:7px;background:rgba(7,7,8,.58);backdrop-filter:blur(28px);-webkit-backdrop-filter:blur(28px);border-bottom:1px solid rgba(255,255,255,.12)}
#fsbtn{width:48px;font-size:18px}
#gate{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:22px;background:radial-gradient(circle at 50% 0,#444 0,#141416 46%,#070708 100%)}
#gate h1{margin:0;font-size:38px;letter-spacing:-.4px}#gate h1 span{font-weight:850}#gate h1 b{font-weight:500;color:rgba(255,255,255,.58)}
#pin{font-size:32px;text-align:center;width:210px;letter-spacing:8px;padding:15px;border-radius:20px;border:1px solid var(--line2);background:rgba(255,255,255,.1);color:var(--fg);box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}
.hint{color:var(--mut);font-size:12px;text-align:center}.err{color:var(--err)}
#screenwrap{position:sticky;top:58px;z-index:20;background:#000;display:flex;align-items:center;justify-content:center;min-height:180px;max-height:58vh;overflow:hidden;border-bottom:1px solid rgba(255,255,255,.12)}
#screenwrap:fullscreen{height:100vh;max-height:100vh;width:100vw;background:#000}
#screenwrap:-webkit-full-screen{height:100vh;max-height:100vh;width:100vw;background:#000}
#screen{max-width:100%;max-height:58vh;display:block;touch-action:none;object-fit:contain}
#screenwrap:fullscreen #screen,#screenwrap:-webkit-full-screen #screen{max-width:100vw;max-height:100vh;width:100vw;height:100vh}
.tag{position:absolute;top:10px;left:10px;padding:6px 11px;border-radius:999px;color:var(--fg);font-size:12px;font-weight:760;background:rgba(0,0,0,.46);border:1px solid rgba(255,255,255,.18);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
.toolbar{position:absolute;top:9px;right:9px;display:flex;gap:6px}.toolbar button{background:rgba(0,0,0,.44);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
.pane{padding:12px}.lbl{color:var(--faint);font-size:11px;font-weight:850;text-transform:uppercase;letter-spacing:.08em;margin:10px 4px 7px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:9px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:9px}.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:9px}
#pad,#bigpad{border:1px dashed rgba(255,255,255,.28);border-radius:24px;color:var(--mut);display:flex;align-items:center;justify-content:center;text-align:center;touch-action:none;min-height:148px;margin-bottom:10px}
#bigpad{height:calc(100vh - 262px);min-height:330px;font-size:15px}
#kb{display:flex;gap:8px;margin-bottom:14px}#kb input,#chatInput,#livekb{width:100%;border:1px solid var(--line);border-radius:18px;background:rgba(255,255,255,.11);color:var(--fg);padding:14px;font-size:16px;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.15)}
#livekb{min-height:120px;resize:none}.foot{height:26px}
#chatPane{display:none;height:calc(100vh - 74px);padding:12px;grid-template-rows:minmax(0,1fr) auto;gap:10px}
#chatLog{overflow:auto;border-radius:26px;padding:12px;display:flex;flex-direction:column;gap:9px}
.msg{max-width:92%;padding:10px 12px;border-radius:18px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.13);white-space:pre-wrap;font-size:14px;line-height:1.35}
.msg.user{align-self:flex-end;background:rgba(255,255,255,.88);color:#08080a}.msg.system,.msg.tool{align-self:center;color:var(--mut);font-size:12px}.msg.assistant{align-self:flex-start}
.chatBox{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}.chatBox button{width:86px}.quick{display:flex;gap:7px;overflow:auto;padding-bottom:3px}.quick button{white-space:nowrap;flex:0 0 auto}
.dragOn{background:rgba(255,255,255,.9)!important;color:#09090a!important}
@media(max-width:410px){button{font-size:13px;padding:12px 8px}.top{gap:5px}.toolbar{left:10px;right:auto;top:auto;bottom:10px}.tag{top:10px}}
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

<div id=screenwrap class=modepane data-mode=full>
  <img id=screen decoding=async>
  <div class=tag id=tag>live</div>
  <div class=toolbar>
    <button class=small id=qbtn onclick="cycleQuality()">Balanced</button>
    <button class=small id=spdbtn onclick="cycleSpeed()">Fast</button>
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
    <button onpointerdown="macro('lock')">🔒 Lock PC</button>
    <button onpointerdown="macro('mute_mic')">🎙️ Mic Off</button>
    <button onpointerdown="macro('sleep_display')">🌙 Sleep</button>
  </div>
  <div class=grid2>
    <button onpointerdown="macro('mute')">🔇 Mute</button>
    <button onpointerdown="macro('unmute')">🔊 Unmute</button>
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
let PIN="",SW=0,SH=0,MODE="full",img=document.getElementById("screen"),lastUrl="",fetching=false,dragLock=false,chatLoop=false;
let QUAL=[{label:"Speed",maxw:1100,q:62,hd:0},{label:"Balanced",maxw:1500,q:78,hd:1},{label:"Sharp",maxw:1920,q:86,hd:1}],QI=1;
let SPEEDS=[90,160,300,650],SLABEL=["Ultra","Fast","Smooth","Lite"],SPI=1,SPEED=SPEEDS[SPI];
async function post(o){o.pin=PIN;try{return (await fetch("/api/event",{method:"POST",body:JSON.stringify(o)})).ok}catch(e){return false}}
function screenUrl(){let q=QUAL[QI];return `/api/screen?pin=${encodeURIComponent(PIN)}&hd=${q.hd}&maxw=${q.maxw}&q=${q.q}&t=${Date.now()}`}
async function connect(){PIN=document.getElementById("pin").value.trim();let r=await fetch(screenUrl(),{cache:"no-store"});if(!r.ok){document.getElementById("err").textContent="Wrong PIN";return}SW=+r.headers.get("X-Screen-W");SH=+r.headers.get("X-Screen-H");document.getElementById("gate").style.display="none";loop();pollChat()}
async function loop(){if(MODE!=="full"||document.hidden){setTimeout(loop,450);return}if(fetching){setTimeout(loop,70);return}fetching=true;let start=performance.now();
 try{let r=await fetch(screenUrl(),{cache:"no-store"});if(r.ok){let b=await r.blob();let url=URL.createObjectURL(b);let old=lastUrl;lastUrl=url;img.src=url;if(old)URL.revokeObjectURL(old);SW=+r.headers.get("X-Screen-W")||SW;SH=+r.headers.get("X-Screen-H")||SH;document.getElementById("tag").textContent=`live · ${Math.round(b.size/1024)} KB`}}catch(e){document.getElementById("tag").textContent="reconnecting"}finally{fetching=false;setTimeout(loop,Math.max(35,SPEED-(performance.now()-start)))}}
function pxy(e){let b=img.getBoundingClientRect(),x=(e.clientX-b.left)/b.width,y=(e.clientY-b.top)/b.height;if(x<0||x>1||y<0||y>1)return null;return{x:Math.round(x*SW),y:Math.round(y*SH),cx:e.clientX,cy:e.clientY}}
let sd=null,screenDrag=false,lastDrag=0;
img.addEventListener("pointerdown",e=>{let p=pxy(e);if(!p)return;e.preventDefault();img.setPointerCapture&&img.setPointerCapture(e.pointerId);sd=p;screenDrag=dragLock;if(dragLock)post({t:"dragstart",x:p.x,y:p.y});});
img.addEventListener("pointermove",e=>{if(!sd)return;let p=pxy(e);if(!p)return;e.preventDefault();let moved=Math.abs(p.cx-sd.cx)+Math.abs(p.cy-sd.cy);if(!screenDrag&&moved>9){screenDrag=true;post({t:"dragstart",x:sd.x,y:sd.y});document.getElementById("tag").textContent="dragging"}if(screenDrag&&performance.now()-lastDrag>16){post({t:"dragto",x:p.x,y:p.y});lastDrag=performance.now()}});
function screenEnd(e){if(!sd)return;let p=pxy(e)||sd;e.preventDefault();if(screenDrag){post({t:"dragend",x:p.x,y:p.y});document.getElementById("tag").textContent="dropped"}else{post({t:"moveto",x:p.x,y:p.y});post({t:"click"});document.getElementById("tag").textContent="clicked"}sd=null;screenDrag=false;setTimeout(()=>document.getElementById("tag").textContent="live",450)}
img.addEventListener("pointerup",screenEnd);img.addEventListener("pointercancel",screenEnd);
function attachPad(pad){if(!pad)return;let lx=0,ly=0,moving=false,moved=0,ax=0,ay=0,last=0,held=false;
 pad.addEventListener("pointerdown",e=>{e.preventDefault();moving=true;moved=0;ax=ay=0;lx=e.clientX;ly=e.clientY;pad.classList.add("dragOn");pad.setPointerCapture&&pad.setPointerCapture(e.pointerId);if(dragLock){held=true;post({t:"down"})}});
 pad.addEventListener("pointermove",e=>{if(!moving)return;e.preventDefault();let dx=e.clientX-lx,dy=e.clientY-ly;lx=e.clientX;ly=e.clientY;moved+=Math.abs(dx)+Math.abs(dy);ax+=dx;ay+=dy;let now=performance.now();if(now-last>16){post({t:"move",dx:Math.round(ax*1.55),dy:Math.round(ay*1.55)});ax=ay=0;last=now}});
 function end(e){if(!moving)return;e&&e.preventDefault();if(Math.round(ax)||Math.round(ay))post({t:"move",dx:Math.round(ax*1.55),dy:Math.round(ay*1.55)});if(held)post({t:"up"});else if(moved<7)post({t:"click"});moving=false;held=false;ax=ay=0;pad.classList.remove("dragOn")}
 pad.addEventListener("pointerup",end);pad.addEventListener("pointercancel",end)}
attachPad(document.getElementById("pad"));attachPad(document.getElementById("bigpad"));
function ev(k){post({t:k})}function scroll(a){post({t:"scroll",a:a})}function key(k){post({t:"key",k:k})}
function flash(m){let t=document.getElementById("tag");if(t){t.textContent=m;setTimeout(()=>{t.textContent="live"},900)}}
function macro(n){post({t:"macro",name:n});flash(n.replace(/_/g," ")+" ✓")}
function runcmd(){let i=document.getElementById("cmdx");if(i&&i.value){post({t:"macro_cmd",cmd:i.value});flash("ran ✓");i.value=""}}
function sendtext(){let i=document.getElementById("tx");if(i.value){post({t:"type",text:i.value});i.value=""}}
function setMode(m){MODE=m;document.querySelectorAll(".modepane").forEach(el=>{el.style.display=el.dataset.mode===m?(m==="chat"?"grid":""):"none"});["full","mouse","kb","chat"].forEach(x=>document.getElementById("m_"+x).classList.toggle("on",x===m));if(m==="kb")setTimeout(()=>document.getElementById("livekb").focus(),60);if(m==="chat")pollChat()}
function toggleFS(){let d=document.getElementById("screenwrap");if(!document.fullscreenElement&&!document.webkitFullscreenElement)(d.requestFullscreen||d.webkitRequestFullscreen||function(){}).call(d);else(document.exitFullscreen||document.webkitExitFullscreen||function(){}).call(document)}
function cycleQuality(){QI=(QI+1)%QUAL.length;document.getElementById("qbtn").textContent=QUAL[QI].label}
function cycleSpeed(){SPI=(SPI+1)%SPEEDS.length;SPEED=SPEEDS[SPI];document.getElementById("spdbtn").textContent=SLABEL[SPI]}
function toggleDragLock(){dragLock=!dragLock;["dragBtn","dragBtn2"].forEach(id=>{let b=document.getElementById(id);if(b){b.classList.toggle("dragOn",dragLock);b.textContent=dragLock?"Dragging On":"Drag Lock"}});if(!dragLock)post({t:"up"})}
let livekb=document.getElementById("livekb");livekb.addEventListener("keydown",e=>{let k=e.key;if(k.length===1){post({t:"type",text:k});e.preventDefault()}else if(k==="Backspace"){key("backspace");e.preventDefault()}else if(k==="Enter"){key("enter");e.preventDefault()}else if(k==="Tab"){key("tab");e.preventDefault()}else if(k.indexOf("Arrow")===0){key(k.slice(5).toLowerCase());e.preventDefault()}livekb.value=""});
function esc(s){return String(s||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]))}
function renderChat(items){let log=document.getElementById("chatLog");log.innerHTML=(items&&items.length?items:[{role:"system",text:"Remote chat is ready. Tell Ember what to do on the desktop."}]).map(m=>`<div class="msg ${esc(m.role)}">${esc(m.text)}</div>`).join("");log.scrollTop=log.scrollHeight}
function pollChat(){if(chatLoop||!PIN)return;chatLoop=true;chatTick()}
async function chatTick(){if(!PIN){chatLoop=false;return}try{let r=await fetch("/api/chat?pin="+encodeURIComponent(PIN),{cache:"no-store"});if(r.ok){let j=await r.json();renderChat(j.messages||[])}}catch(e){}setTimeout(chatTick,MODE==="chat"?750:1800)}
async function sendChat(){let i=document.getElementById("chatInput"),text=i.value.trim();if(!text)return;i.value="";await fetch("/api/chat",{method:"POST",body:JSON.stringify({pin:PIN,text})});pollChat()}
document.querySelectorAll("[data-chat]").forEach(b=>b.addEventListener("click",()=>{document.getElementById("chatInput").value=b.dataset.chat;sendChat()}));
document.getElementById("chatInput").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendChat()}});
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence console spam
        pass

    def _auth(self, pin):
        ip = self.client_address[0] if self.client_address else "?"
        if _auth_locked(ip):
            time.sleep(1.0)  # slow even the rejection while locked out
            return False
        ok = bool(_STATE["pin"]) and secrets.compare_digest(str(pin or ""), str(_STATE["pin"]))
        if ok:
            _STATE["last_active"] = time.time()  # refresh the idle-timeout watchdog
        else:
            time.sleep(0.3)  # cap brute-force throughput
        _auth_record(ip, ok)
        return ok

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/screen"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if not self._auth((q.get("pin") or [""])[0]):
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
            if not self._auth((q.get("pin") or [""])[0]):
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
        if self.path == "/api/chat":
            try:
                n = int(self.headers.get("Content-Length", 0))
                o = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                o = {}
            if not self._auth(o.get("pin")):
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
        if not self._auth(o.get("pin")):
            self.send_response(403); self.end_headers(); return
        try:
            _apply(o)
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
    ("lock", "🔒 Lock PC"),
    ("mute", "🔇 Mute"),
    ("unmute", "🔊 Unmute"),
    ("mute_mic", "🎙️ Mic Off"),
    ("sleep_display", "🌙 Sleep Screen"),
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
    if name == "mute_mic":
        if sys.platform == "darwin":
            ok, d = _macro_cmd(["osascript", "-e", "set volume input volume 0"])
        elif sys.platform.startswith("win"):
            return {"ok": False, "macro": name,
                    "detail": "mic mute needs nircmd on Windows"}
        else:
            ok, d = _macro_cmd(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "1"])
        return {"ok": ok, "macro": name, "detail": d or "microphone muted"}
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
    try:
        f.write_text(p)
    except OSError:
        pass
    return p


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


def stop() -> dict:
    srv = _STATE.get("server")
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
            "url": _STATE.get("url"), "pin": _STATE.get("pin")}
