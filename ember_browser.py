"""Ember Browser — a secure, AI-first web browser built on Qt WebEngine (Chromium).

Security:
- Blocks known ad/tracker/telemetry domains on every request (local list, no network).
- Enforces Ember's web policy (web_policy.check_url) on user navigation.
- HTTPS-first; private in-memory profile; popups/clipboard hardened.

AI-first (uses your Gemini or Claude key from Ember Settings):
- Ember Search: type a query and get an AI answer + web results on one page.
- ✨ AI panel: Summarize / Ask about the page.
- 🔎 AI Check: estimate whether the page's text is AI-generated.
- "ai <question>" or a trailing "?" in the address bar asks without a URL.
- 🧩 AI extension maker: describe what you want ("hide the comments") and Ember's AI
  writes a userscript that's injected into matching pages (see browser_extensions.py).

Plus: tabs, bookmarks, find-in-page (Ctrl+F), zoom (Ctrl+ +/-), Ctrl+T/W/L.

QtWebEngine is optional (PyQt6-WebEngine). If unavailable, WEBENGINE_OK is False and
the caller shows the import error in WEBENGINE_ERROR.
"""
from __future__ import annotations

import html as _html
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QTabWidget, QLabel, QTextBrowser, QSplitter, QSizePolicy, QMenu,
                             QInputDialog, QMessageBox, QProgressBar, QGraphicsOpacityEffect,
                             QLineEdit as _QLE, QDialog, QListWidget, QListWidgetItem,
                             QPlainTextEdit)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (QWebEngineProfile, QWebEnginePage,
                                       QWebEngineUrlRequestInterceptor, QWebEngineSettings)
    WEBENGINE_OK = True
    WEBENGINE_ERROR = ""
except Exception as e:
    WEBENGINE_OK = False
    WEBENGINE_ERROR = f"{type(e).__name__}: {e}"

SEARCH_HOST = "ember.search"   # internal sentinel the start page / address bar post to

_TRACKERS = {
    "doubleclick.net", "google-analytics.com", "googletagmanager.com", "googlesyndication.com",
    "googleadservices.com", "adservice.google.com", "connect.facebook.net", "facebook.net",
    "ads-twitter.com", "analytics.twitter.com", "scorecardresearch.com", "quantserve.com",
    "adnxs.com", "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "amazon-adsystem.com",
    "hotjar.com", "mixpanel.com", "segment.com", "segment.io", "branch.io", "appsflyer.com",
    "moatads.com", "rubiconproject.com", "pubmatic.com", "openx.net", "casalemedia.com",
    "bluekai.com", "krxd.net", "demdex.net", "adsrvr.org", "2mdn.net", "yieldmo.com",
    "newrelic.com", "nr-data.net", "fullstory.com", "amplitude.com", "sentry.io",
}

# ---------------------------------------------------------------------------
# Ember Search — a polished, colourful, customisable search experience.
# The look is driven entirely by CSS custom properties that a tiny inline script sets from
# EMBER_CFG (injected per-page) BEFORE first paint, so themes never flash. Colour presets +
# a custom accent + light/dark + quick-link tiles + a live customise panel all persist to
# browser_theme.json (see _load_theme/_save_theme), applied server-side on every render.
# ---------------------------------------------------------------------------

# Colourful built-in themes: (accent, secondary-accent). 'custom' uses the user's own accent.
_SEARCH_PRESETS = {
    "ember":  ("#e8632e", "#f0a13c"),
    "ocean":  ("#2b8cff", "#22d3ee"),
    "forest": ("#2fb46a", "#8ae06a"),
    "grape":  ("#8b5cf6", "#d946ef"),
    "rose":   ("#f43f6a", "#fb923c"),
    "slate":  ("#5b6b86", "#93a3bd"),
}

_DEFAULT_SHORTCUTS = [
    {"label": "YouTube",   "url": "https://www.youtube.com"},
    {"label": "Wikipedia", "url": "https://www.wikipedia.org"},
    {"label": "GitHub",    "url": "https://github.com"},
    {"label": "Reddit",    "url": "https://www.reddit.com"},
    {"label": "Maps",      "url": "https://www.openstreetmap.org"},
    {"label": "News",      "url": "https://news.google.com"},
]

_DEFAULT_THEME = {
    "preset": "ember",       # one of _SEARCH_PRESETS or "custom"
    "accent": "#e8632e",     # used when preset == "custom"
    "accent2": "#f0a13c",
    "mode": "dark",          # "dark" | "light"
    "clock": True,
    "shortcuts": _DEFAULT_SHORTCUTS,
}

_SEARCH_CSS = r"""
:root{
  --accent:#e8632e; --accent-rgb:232,99,46; --accent2:#f0a13c; --accent2-rgb:240,161,60;
  --bg:#0e0f13; --bg2:#15171e; --card:#181a22; --line:#262a36; --line2:#333849;
  --fg:#e9eaf0; --muted:#9198a6; --faint:#6b7280; --link:#8ab0ff; --good:#6cc07a;
  --radius:18px; --shadow:0 10px 34px rgba(0,0,0,.38);
}
:root.light{
  --bg:#f4f6fb; --bg2:#ffffff; --card:#ffffff; --line:#e7eaf1; --line2:#dfe3ec;
  --fg:#191c24; --muted:#5b6472; --faint:#8b93a3; --link:#2860df; --good:#0f9d58;
  --shadow:0 12px 34px rgba(25,30,50,.10);
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{
  background:
    radial-gradient(1200px 640px at 8% -10%, rgba(var(--accent-rgb),.20), transparent 60%),
    radial-gradient(1000px 560px at 105% -6%, rgba(var(--accent2-rgb),.16), transparent 55%),
    var(--bg);
  color:var(--fg); font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:720px;margin:0 auto;padding:26px 22px 72px}

/* ---- home hero ---- */
.hero{display:flex;flex-direction:column;align-items:center;padding-top:min(14vh,120px)}
.logo{font-size:clamp(40px,8vw,60px);font-weight:850;letter-spacing:-1.5px;line-height:1;
  background:linear-gradient(100deg,var(--accent),var(--accent2));-webkit-background-clip:text;
  background-clip:text;-webkit-text-fill-color:transparent;color:transparent}
.logo .spark{-webkit-text-fill-color:initial;color:var(--accent2)}
.tag{color:var(--muted);margin-top:10px;font-size:14px}
.greet{color:var(--muted);font-size:13px;margin-top:4px;height:16px}

/* ---- search box (shared) ---- */
.searchbox{display:flex;align-items:center;gap:10px;width:100%;margin-top:26px;
  background:var(--card);border:1px solid var(--line);border-radius:30px;padding:6px 6px 6px 18px;
  box-shadow:var(--shadow);transition:border-color .15s,box-shadow .15s}
.searchbox:focus-within{border-color:var(--accent);box-shadow:0 0 0 4px rgba(var(--accent-rgb),.16),var(--shadow)}
.searchbox svg{flex:0 0 auto;opacity:.7}
.searchbox input{flex:1;border:0;background:transparent;color:var(--fg);font-size:16.5px;outline:none;padding:12px 0}
.searchbox input::placeholder{color:var(--faint)}
.searchbox button{flex:0 0 auto;border:0;cursor:pointer;font-weight:750;font-size:15px;color:#fff;
  padding:12px 22px;border-radius:24px;background:linear-gradient(100deg,var(--accent),var(--accent2))}
.searchbox button:hover{filter:brightness(1.07)}

/* ---- quick-link tiles ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:12px;margin-top:30px;width:100%}
.tile{display:flex;flex-direction:column;align-items:center;gap:8px;padding:14px 6px;border-radius:16px;
  background:var(--card);border:1px solid var(--line);color:var(--fg);text-align:center;transition:transform .12s,border-color .12s,background .12s;position:relative}
.tile:hover{transform:translateY(-3px);border-color:var(--accent);text-decoration:none}
.tile .ic{width:40px;height:40px;border-radius:12px;display:flex;align-items:center;justify-content:center;
  background:rgba(var(--accent-rgb),.14);overflow:hidden}
.tile .ic img{width:26px;height:26px}
.tile .ic .ltr{font-weight:800;color:var(--accent);font-size:19px}
.tile .lb{font-size:12px;color:var(--muted);max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile.add .ic{background:transparent;border:1.5px dashed var(--line2)}
.tile.add .ic .ltr{color:var(--faint)}
.tile .rm{position:absolute;top:3px;right:5px;width:18px;height:18px;border-radius:9px;border:0;cursor:pointer;
  background:var(--line2);color:var(--fg);font-size:12px;line-height:16px;display:none;padding:0}
.tiles.editing .tile:not(.add):hover .rm{display:block}

/* ---- customise button + panel ---- */
.gear{position:fixed;top:14px;right:16px;width:40px;height:40px;border-radius:12px;border:1px solid var(--line);
  background:var(--card);color:var(--muted);cursor:pointer;font-size:17px;box-shadow:var(--shadow)}
.gear:hover{color:var(--accent);border-color:var(--accent)}
.panel{position:fixed;top:0;right:0;bottom:0;width:min(340px,88vw);background:var(--bg2);
  border-left:1px solid var(--line);box-shadow:-14px 0 40px rgba(0,0,0,.34);padding:20px;overflow-y:auto;
  transform:translateX(102%);transition:transform .22s ease;z-index:20}
.panel.open{transform:translateX(0)}
.backdrop{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.34);opacity:0;visibility:hidden;transition:opacity .22s ease;z-index:19}
.backdrop.on{opacity:1;visibility:visible}
.panel h2{margin:0 0 2px;font-size:18px}
.panel .sub{color:var(--muted);font-size:12px;margin-bottom:18px}
.panel .grp{margin:18px 0}
.panel .grp>label{display:block;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:9px}
.swatches{display:flex;gap:10px;flex-wrap:wrap}
.sw{width:34px;height:34px;border-radius:11px;cursor:pointer;border:2px solid transparent;position:relative}
.sw.on{border-color:var(--fg)}
.sw.on::after{content:"✓";position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;font-weight:800;text-shadow:0 1px 2px rgba(0,0,0,.5)}
.seg{display:flex;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.seg button{flex:1;border:0;background:transparent;color:var(--muted);padding:9px;cursor:pointer;font-weight:650;font-size:13px}
.seg button.on{background:linear-gradient(100deg,var(--accent),var(--accent2));color:#fff}
.row{display:flex;align-items:center;justify-content:space-between;gap:10px}
.mini{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:10px;padding:8px 10px;font-size:14px;outline:none}
.mini:focus{border-color:var(--accent)}
.pbtn{width:100%;margin-top:8px;border:0;cursor:pointer;font-weight:750;color:#fff;padding:11px;border-radius:12px;
  background:linear-gradient(100deg,var(--accent),var(--accent2))}
.pbtn.ghost{background:transparent;color:var(--muted);border:1px solid var(--line);font-weight:600}
.switch{position:relative;width:44px;height:26px;border-radius:14px;background:var(--line2);cursor:pointer;transition:background .15s;flex:0 0 auto}
.switch.on{background:var(--accent)}
.switch i{position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:left .15s}
.switch.on i{left:21px}
.acc-in{width:34px;height:34px;padding:0;border:1px solid var(--line);border-radius:9px;background:none;cursor:pointer}

/* ---- results ---- */
.rhead{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:12px 22px;margin:0 -22px 6px;
  background:color-mix(in srgb,var(--bg) 86%, transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.rhead .mark{font-weight:850;font-size:18px;letter-spacing:-.5px;background:linear-gradient(100deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.rhead .searchbox{margin:0;flex:1;box-shadow:none;padding:3px 4px 3px 14px}
.rhead .searchbox input{font-size:15px;padding:9px 0}
.rhead .searchbox button{padding:9px 16px;font-size:13.5px}
.answer{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin:18px 0;box-shadow:var(--shadow);position:relative;overflow:hidden}
.answer::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:linear-gradient(var(--accent),var(--accent2))}
.answer h3{margin:0 0 10px;font-size:12.5px;letter-spacing:.5px;text-transform:uppercase;color:var(--accent2);display:flex;align-items:center;gap:7px}
.answer .body{font-size:15px;line-height:1.62}
.answer .cite{color:var(--accent);font-weight:700;text-decoration:none;padding:0 2px}
.copy{position:absolute;top:14px;right:14px;border:1px solid var(--line);background:var(--bg2);color:var(--muted);border-radius:9px;font-size:12px;padding:5px 10px;cursor:pointer}
.copy:hover{color:var(--accent);border-color:var(--accent)}
.calc{display:inline-flex;align-items:center;gap:8px;background:rgba(var(--accent-rgb),.12);border:1px solid rgba(var(--accent-rgb),.3);border-radius:12px;padding:8px 14px;font-size:20px;font-weight:750;margin:6px 0 14px}
.reslist{margin-top:8px}
.card{display:flex;gap:13px;align-items:flex-start;padding:13px 15px;border-radius:14px;border:1px solid transparent;transition:background .12s,border-color .12s}
.card:hover{background:var(--card);border-color:var(--line)}
.card .fav{width:30px;height:30px;border-radius:8px;flex:0 0 auto;background:var(--card);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;overflow:hidden;margin-top:2px}
.card .fav img{width:20px;height:20px}
.card .fav .ltr{font-weight:800;color:var(--accent);font-size:15px;align-items:center;justify-content:center}
.card .txt{min-width:0}
.card .ti{color:var(--link);font-size:17px;font-weight:600;display:block;line-height:1.3}
.card .dom{color:var(--good);font-size:12.5px;margin-top:2px}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:22px;align-items:center}
.pills .lbl{color:var(--faint);font-size:12.5px;margin-right:2px}
.pill{border:1px solid var(--line);background:var(--card);color:var(--muted);border-radius:20px;padding:6px 13px;font-size:13px}
.pill:hover{border-color:var(--accent);color:var(--accent);text-decoration:none}
.empty{color:var(--muted);text-align:center;padding:26px;font-size:14px}
.skl{height:14px;border-radius:7px;background:linear-gradient(90deg,var(--card),var(--line),var(--card));background-size:200% 100%;animation:sh 1.2s infinite}
@keyframes sh{0%{background-position:200% 0}100%{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){
  *{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}
  .tile:hover{transform:none}
}
"""

# Sets the theme CSS variables from EMBER_CFG BEFORE first paint (runs in <head>), so no flash.
_SEARCH_HEAD_JS = r"""
(function(){
  var C=window.EMBER_CFG||{};
  var P={"ember":["#e8632e","#f0a13c"],"ocean":["#2b8cff","#22d3ee"],"forest":["#2fb46a","#8ae06a"],
         "grape":["#8b5cf6","#d946ef"],"rose":["#f43f6a","#fb923c"],"slate":["#5b6b86","#93a3bd"]};
  function rgb(h){h=(h||"").replace('#','');if(h.length===3)h=h.split('').map(function(x){return x+x}).join('');
    var n=parseInt(h||"e8632e",16);return ((n>>16)&255)+","+((n>>8)&255)+","+(n&255);}
  window.__emberAcc=function(c){var p=(c.preset&&c.preset!=='custom'&&P[c.preset])?P[c.preset]:[c.accent||'#e8632e',c.accent2||c.accent||'#f0a13c'];return p;};
  window.__emberApply=function(c){
    var r=document.documentElement,a=window.__emberAcc(c);
    r.classList.toggle('light',(c.mode||'dark')==='light');
    r.style.setProperty('--accent',a[0]); r.style.setProperty('--accent2',a[1]);
    r.style.setProperty('--accent-rgb',rgb(a[0])); r.style.setProperty('--accent2-rgb',rgb(a[1]));
  };
  window.__emberApply(C);
  // "/" focuses the search box from anywhere (a search-engine convention), unless you're
  // already typing in a field.
  document.addEventListener('keydown',function(e){
    if(e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&
       !/^(INPUT|TEXTAREA|SELECT)$/.test((e.target&&e.target.tagName)||'')){
      var q=document.querySelector('input[name=q]');
      if(q){e.preventDefault();q.focus();if(q.select)q.select();}
    }
  });
})();
"""

# Home-only behaviour: greeting/clock, quick-link tiles (with favicons), and the live
# customise panel. Live theme changes preview instantly; "Save" round-trips to Python
# (?embercfg=…) which persists to browser_theme.json so it survives restarts.
_HOME_JS = r"""
(function(){
  var C=window.EMBER_CFG||{}; C.shortcuts=C.shortcuts||[]; if(!C.preset)C.preset='ember'; if(!C.mode)C.mode='dark';
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(m){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[m]});}
  function host(u){try{return new URL(u).hostname.replace(/^www\./,'')}catch(e){return ''}}
  function fav(u){var h=host(u);return h?('https://icons.duckduckgo.com/ip3/'+h+'.ico'):'';}
  var PRESETS=['ember','ocean','forest','grape','rose','slate'];
  var editing=false;

  function greet(){
    var g=document.getElementById('greet'); if(!g)return;
    if(!C.clock){g.textContent='';return;}
    var d=new Date(),h=d.getHours();
    var part=h<5?'Good night':h<12?'Good morning':h<18?'Good afternoon':'Good evening';
    var t=d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    g.textContent=part+' · '+t;
  }
  function renderTiles(){
    var box=document.getElementById('tiles'); if(!box)return;
    box.classList.toggle('editing',editing);
    var html='';
    C.shortcuts.forEach(function(s,i){
      var f=fav(s.url),lt=esc((s.label||host(s.url)||'?').charAt(0).toUpperCase());
      var ic=f?('<img src="'+esc(f)+'" referrerpolicy=no-referrer loading=lazy onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'block\'"><span class=ltr style="display:none">'+lt+'</span>'):('<span class=ltr>'+lt+'</span>');
      html+='<a class=tile href="'+esc(s.url)+'"><button class=rm data-i="'+i+'" title="Remove">&times;</button>'
        +'<span class=ic>'+ic+'</span><span class=lb>'+esc(s.label||host(s.url))+'</span></a>';
    });
    html+='<a class="tile add" id=addTile href="javascript:void(0)"><span class=ic><span class=ltr>+</span></span><span class=lb>Add</span></a>';
    box.innerHTML=html;
    Array.prototype.forEach.call(box.querySelectorAll('.rm'),function(b){b.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();C.shortcuts.splice(+b.getAttribute('data-i'),1);renderTiles();});});
    var at=document.getElementById('addTile'); if(at)at.addEventListener('click',function(){openPanel();editing=true;renderTiles();var l=document.getElementById('scLabel');if(l)l.focus();});
  }
  function paintPanel(){
    // reflect current cfg in the panel controls
    var sw=document.getElementById('swatches');
    if(sw)Array.prototype.forEach.call(sw.children,function(el){el.classList.toggle('on',el.getAttribute('data-p')===C.preset);});
    var seg=document.getElementById('modeSeg');
    if(seg)Array.prototype.forEach.call(seg.children,function(b){b.classList.toggle('on',b.getAttribute('data-m')===C.mode);});
    var cl=document.getElementById('clkSwitch'); if(cl)cl.classList.toggle('on',!!C.clock);
    var ac=document.getElementById('accIn'); if(ac&&C.preset==='custom')ac.value=C.accent||'#e8632e';
  }
  function apply(){window.__emberApply(C);paintPanel();}
  function setBackdrop(on){var b=document.getElementById('backdrop');if(b)b.classList.toggle('on',on);}
  function panelOpen(){var p=document.getElementById('panel');return !!(p&&p.classList.contains('open'));}
  function openPanel(){var p=document.getElementById('panel');if(p)p.classList.add('open');setBackdrop(true);}
  function closePanel(){var p=document.getElementById('panel');if(p)p.classList.remove('open');setBackdrop(false);editing=false;renderTiles();}
  function save(){
    // round-trip to Python to persist to disk; navigation is blocked (no reload) by the handler.
    try{location.href='https://ember.search/?embercfg='+encodeURIComponent(JSON.stringify(C));}catch(e){}
  }

  document.addEventListener('DOMContentLoaded',function(){
    greet(); setInterval(greet,15000); renderTiles(); paintPanel();
    var g=document.getElementById('gear'); if(g)g.addEventListener('click',function(){panelOpen()?closePanel():openPanel();});
    var cl=document.getElementById('closePanel'); if(cl)cl.addEventListener('click',closePanel);
    var bd=document.getElementById('backdrop'); if(bd)bd.addEventListener('click',closePanel);
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&panelOpen())closePanel();});
    var sw=document.getElementById('swatches');
    if(sw)Array.prototype.forEach.call(sw.children,function(el){el.addEventListener('click',function(){C.preset=el.getAttribute('data-p');apply();});});
    var ac=document.getElementById('accIn');
    if(ac)ac.addEventListener('input',function(){C.preset='custom';C.accent=ac.value;C.accent2=ac.value;apply();});
    var seg=document.getElementById('modeSeg');
    if(seg)Array.prototype.forEach.call(seg.children,function(b){b.addEventListener('click',function(){C.mode=b.getAttribute('data-m');apply();});});
    var cs=document.getElementById('clkSwitch'); if(cs)cs.addEventListener('click',function(){C.clock=!C.clock;cs.classList.toggle('on',C.clock);greet();});
    function addShortcut(){
      var l=document.getElementById('scLabel'),u=document.getElementById('scUrl');
      var url=(u.value||'').trim(); if(!url)return;
      if(!/^https?:\/\//i.test(url))url='https://'+url;
      C.shortcuts.push({label:(l.value||host(url)).trim(),url:url}); l.value='';u.value='';renderTiles();u.focus();
    }
    var addBtn=document.getElementById('scAdd');
    if(addBtn)addBtn.addEventListener('click',addShortcut);
    // Enter in either shortcut field adds it — no need to reach for the button.
    ['scLabel','scUrl'].forEach(function(id){
      var el=document.getElementById(id);
      if(el)el.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();addShortcut();}});
    });
    var ed=document.getElementById('editBtn'); if(ed)ed.addEventListener('click',function(){editing=!editing;ed.textContent=editing?'Done editing':'Edit shortcuts';renderTiles();});
    var sv=document.getElementById('saveBtn'); if(sv)sv.addEventListener('click',function(){save();closePanel();});
    var rs=document.getElementById('resetBtn'); if(rs)rs.addEventListener('click',function(){C.preset='ember';C.mode='dark';C.clock=true;C.accent='#e8632e';C.accent2='#f0a13c';apply();});
  });
})();
"""

BROWSER_QSS = """
QWidget { background:#0c0e14; color:#e9edf5; font:13px -apple-system,'Segoe UI',sans-serif; }
QWidget#browserChrome { background:#11141d; border-bottom:1px solid #252a36; }
QPushButton { background:#1a1e29; border:1px solid #292f3d; border-radius:9px; padding:6px 8px;
              color:#cbd3e3; font-weight:650; }
QPushButton:hover { background:#252a37; border-color:#41495b; color:#ffffff; }
QPushButton:pressed { background:#303747; }
QPushButton#toolbarButton { background:transparent; border-color:transparent; padding:5px; }
QPushButton#toolbarButton:hover { background:#232936; border-color:#303747; }
QPushButton#aiButton { background:#263a68; border-color:#3f5f9f; color:#edf3ff; }
QLineEdit { background:#161a24; border:1px solid #29303e; border-radius:12px; padding:8px 12px;
            color:#fff; selection-background-color:#527fd1; }
QLineEdit#addressBar { border-radius:17px; padding:8px 15px; background:#191d28; }
QLineEdit:focus { border-color:#6d9bea; background:#1b202c; }
QLabel#connectionState { color:#65d6aa; font-size:11px; font-weight:800; }
QLabel#connectionState[secure="false"] { color:#f0b36a; }
QTabWidget::pane { border:none; }
QTabBar::tab { background:transparent; color:#969fb2; padding:8px 16px; border:none;
               border-top-left-radius:9px; border-top-right-radius:9px; margin:2px 1px 0 1px; }
QTabBar::tab:selected { background:#1a1e29; color:#f6f8fc; }
QTabBar::tab:hover { background:#151923; color:#dbe2ee; }
QTextBrowser { background:#131722; border:1px solid #29303e; border-radius:12px; }
QDialog { background:#0c0e14; }
QListWidget, QPlainTextEdit { background:#111520; border:1px solid #29303e; border-radius:11px; padding:5px; }
QListWidget::item { padding:10px 8px; border-radius:8px; }
QListWidget::item:selected { background:#293d66; color:#ffffff; }
QMenu { background:#171b25; color:#e9edf5; border:1px solid #303747; border-radius:10px; padding:6px; }
QMenu::item { padding:8px 18px; border-radius:7px; }
QMenu::item:selected { background:#2b3e66; }
QMenu::separator { height:1px; background:#2b3140; margin:6px 10px; }
QSplitter::handle { background:#252b38; width:3px; }
"""


def _host_is_blocked(host: str, domains: frozenset) -> bool:
    """host == d or host is a subdomain of d, checked by walking up the label hierarchy with O(1)
    set lookups instead of scanning every blocked domain — the merged list below can be 100k+
    entries once the user pulls in a big public list (e.g. StevenBlack) via the Ad Blocker."""
    parts = host.split(".")
    for i in range(len(parts)):
        if ".".join(parts[i:]) in domains:
            return True
    return False


if WEBENGINE_OK:
    class _Guard(QWebEngineUrlRequestInterceptor):
        """Blocks ad/tracker requests in-page. Shares Ember's system-wide ad-blocker list
        (network_adblock.blocklist()) instead of a separate, much smaller hardcoded set, so
        turning on a bigger list there (or adding a custom domain) also strengthens Ember
        Browser — previously the two blockers were disconnected and the browser stayed stuck
        on ~50 hardcoded domains no matter what the system-wide blocker was set to."""
        def __init__(self):
            super().__init__()
            self.blocked = 0
            self.enabled = True
            self._domains = frozenset(_TRACKERS)
            self._refreshing = False
            self._last_refresh = 0.0
            self._refresh_domains()

        def _refresh_domains(self):
            if self._refreshing:
                return
            self._refreshing = True

            def work():
                domains = set(_TRACKERS)
                try:
                    import network_adblock
                    domains |= network_adblock.blocklist()
                except Exception:
                    pass
                self._domains = frozenset(domains)
                self._last_refresh = time.monotonic()
                self._refreshing = False

            threading.Thread(target=work, daemon=True).start()

        def interceptRequest(self, info):
            try:
                if not self.enabled:
                    return
                if time.monotonic() - self._last_refresh > 60:
                    self._refresh_domains()
                host = (info.requestUrl().host() or "").lower()
                if host and _host_is_blocked(host, self._domains):
                    info.block(True)
                    self.blocked += 1
            except Exception:
                pass

    class _Page(QWebEnginePage):
        """Page that intercepts Ember Search submissions instead of navigating to them."""
        searchRequested = pyqtSignal(str)
        configRequested = pyqtSignal(str)   # the start page posting a saved customisation

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            s = url.toString()
            if SEARCH_HOST in s and "embercfg=" in s:
                # The customise panel posts its JSON here to persist it. Emit it and BLOCK the
                # navigation so the live-previewed page isn't reloaded out from under the user.
                cfg = parse_qs(urlparse(s).query).get("embercfg", [""])[0]
                self.configRequested.emit(cfg)
                return False
            if SEARCH_HOST in s and ("?q=" in s or "&q=" in s):
                q = parse_qs(urlparse(s).query).get("q", [""])[0]
                self.searchRequested.emit(q)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)

        def createWindow(self, _type):
            """Links with target="_blank", window.open(), and ctrl/middle-click "open in new
            tab" all ask the PAGE to make a new window. QtWebEngine calls this to get one; the
            default returns None, so those links get silently DROPPED — which is exactly why they
            "don't open in a new tab or open at all". Route them to a real new Ember tab and hand
            back its page for the engine to load the target URL into."""
            browser = getattr(self, "_browser", None)
            if browser is not None:
                try:
                    return browser._new_tab_page()
                except Exception:
                    pass
            return super().createWindow(_type)


def _ddg(query: str):
    """Fetch a few organic web results from DuckDuckGo's HTML endpoint."""
    try:
        import re
        import requests
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        out = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
            href, title = _html.unescape(m.group(1)), re.sub("<[^>]+>", "", m.group(2)).strip()
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            if href.startswith("//"):
                href = "https:" + href
            if href and title:
                out.append((title, href))
            if len(out) >= 6:
                break
        return out
    except Exception:
        return []


def _fetch_page_text(url: str, limit: int = 3500) -> str:
    """Best-effort fetch + strip of a result page's readable text (for grounding the AI
    answer in CURRENT web content instead of the model's training data)."""
    try:
        import re
        import requests
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (EmberBrowser)"}, timeout=8)
        t = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav).*?</\1>", " ", r.text)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = re.sub(r"\s+", " ", _html.unescape(t)).strip()
        return t[:limit]
    except Exception:
        return ""


def _modern_user_agent() -> str:
    """A current desktop-Chrome User-Agent for the running OS.

    QtWebEngine's default UA advertises 'QtWebEngine/x.y Chrome/OLD', which sites like BandLab,
    Google Docs, Figma etc. sniff and reject with 'update your browser / use Chrome'. Presenting
    a plain, current Chrome UA (no QtWebEngine token) makes those pages treat us as a modern
    browser. Underlying engine features are unchanged — this only removes the UA-sniff banner."""
    ver = "131.0.0.0"
    if sys.platform == "darwin":
        plat = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("win"):
        plat = "Windows NT 10.0; Win64; x64"
    else:
        plat = "X11; Linux x86_64"
    return (f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{ver} Safari/537.36")


def _instant_answer(query: str):
    """Compute a quick local answer for arithmetic queries (e.g. '12*8+3')."""
    import re
    s = (query or "").strip()
    if re.fullmatch(r"[0-9eE.\s+\-*/()%]+", s) and any(op in s for op in "+-*/"):
        try:
            return f"= {eval(s, {'__builtins__': {}}, {})}"  # input is digits/operators only
        except Exception:
            return None
    return None


class EmberBrowser(QWidget):
    _ai_result = pyqtSignal(str)
    _search_result = pyqtSignal(str, str)
    _answer_ready = pyqtSignal(str, str)         # query, answer-html — fills the card in place
    _ext_made = pyqtSignal(str, str, str, str)   # name, match, description, js

    def __init__(self, settings: dict | None = None):
        super().__init__()
        self.settings = settings or {}
        self.setWindowTitle("Ember Browser")
        self.resize(1180, 800)
        self.setMinimumSize(640, 480)
        self._theme = self._load_theme()
        self.setStyleSheet(self._qss())
        self._ai_result.connect(self._show_ai_result)
        self._search_result.connect(self._load_search_results)
        self._answer_ready.connect(self._update_search_answer)
        self._ext_made.connect(self._on_ext_made)
        self._bookmarks = self._load_bookmarks()
        self._history = self._load_history()
        self._downloads: list[dict] = []
        self._closed_tabs: list[str] = []

        self._profile = QWebEngineProfile(self)
        # Present as a current Chrome so sites don't refuse us with an "unsupported browser /
        # update your browser" banner (the default QtWebEngine UA triggers that on BandLab etc.).
        try:
            self._profile.setHttpUserAgent(_modern_user_agent())
        except Exception:
            pass
        self._guard = _Guard()
        try:
            self._profile.setUrlRequestInterceptor(self._guard)
        except Exception:
            pass
        try:
            self._profile.downloadRequested.connect(self._on_download)
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Wrap the toolbar row in a container so it can be hidden as a unit when a page goes
        # fullscreen (e.g. a fullscreen video), leaving only the web content on screen.
        self._chrome = QWidget()
        self._chrome.setObjectName("browserChrome")
        bar = QHBoxLayout(self._chrome)
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)

        def _btn(text, tip, fn, w=34):
            b = QPushButton(text)
            b.setObjectName("toolbarButton")
            b.setToolTip(tip)
            b.setFixedWidth(w)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            return b

        def _ibtn(icon_name, fallback, tip, fn, w=34):
            """A toolbar button using Ember's own icon set, falling back to text/emoji if
            the SVG icon can't be rendered — so the bar never ends up blank."""
            b = _btn("", tip, fn, w)
            try:
                import icons
                from PyQt6.QtCore import QSize
                ic = icons.qicon(icon_name, size=18, color="#cdd1db")
                if ic is not None and not ic.isNull():
                    b.setIcon(ic)
                    b.setIconSize(QSize(18, 18))
                    return b
            except Exception:
                pass
            b.setText(fallback)
            return b

        bar.addWidget(_ibtn("back", "←", "Back", lambda: self._cur() and self._cur().back()))
        bar.addWidget(_ibtn("forward", "→", "Forward", lambda: self._cur() and self._cur().forward()))
        bar.addWidget(_ibtn("reload", "⟳", "Reload", lambda: self._cur() and self._cur().reload()))
        bar.addWidget(_ibtn("home", "⌂", "Ember Search home", lambda: self._go_home()))
        self._lock = QLabel("●")
        self._lock.setObjectName("connectionState")
        self._lock.setToolTip("Connection security")
        bar.addWidget(self._lock)
        self.address = QLineEdit()
        self.address.setObjectName("addressBar")
        self.address.setPlaceholderText("Search Ember, enter a URL, or ask a question (end with ?)…")
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self._navigate_from_bar)
        self.address.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar.addWidget(self.address, 1)
        self._privacy_btn = _ibtn("shield-check", "◆", "Privacy and site data", self._show_privacy_menu)
        bar.addWidget(self._privacy_btn)
        bar.addWidget(_ibtn("star", "★", "Bookmark this page", self._bookmark_current))
        bar.addWidget(_ibtn("plus", "+", "New tab", lambda: self._new_tab()))
        ai_btn = _ibtn("sparkle", "AI", "Open the AI page assistant", self._toggle_ai, w=38)
        ai_btn.setObjectName("aiButton")
        bar.addWidget(ai_btn)
        bar.addWidget(_btn("•••", "Browser tools and settings", self._show_more_menu, w=38))
        outer.addWidget(self._chrome)

        # Slim page-load progress line (animates as pages load, fades out when done).
        self._loadbar = QProgressBar()
        self._loadbar.setTextVisible(False)
        self._loadbar.setRange(0, 100)
        self._loadbar.setFixedHeight(3)
        self._loadbar.setStyleSheet(
            "QProgressBar{background:transparent;border:none;}"
            "QProgressBar::chunk{background:#e2562a;}")
        self._loadbar.setVisible(False)
        outer.addWidget(self._loadbar)

        # find bar (hidden until Ctrl+F)
        self._find_bar = QWidget()
        fb = QHBoxLayout(self._find_bar)
        fb.setContentsMargins(8, 0, 8, 4)
        self._find_in = QLineEdit()
        self._find_in.setPlaceholderText("Find…")
        self._find_in.returnPressed.connect(lambda: self._find_next(True))
        self._find_in.textChanged.connect(lambda t: self._find_next(True))
        fb.addWidget(self._find_in, 1)
        fb.addWidget(_btn("∧", "Previous", lambda: self._find_next(False)))
        fb.addWidget(_btn("∨", "Next", lambda: self._find_next(True)))
        fb.addWidget(_btn("✕", "Close", self._toggle_find))
        self._find_bar.setVisible(False)
        outer.addWidget(self._find_bar)

        self._split = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Tab groups: right-click a tab to colour/assign it to a named group.
        self._tab_groups = dict(self.settings.get("browser_tab_groups", {}))  # name -> color hex
        tb = self.tabs.tabBar()
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tb.customContextMenuRequested.connect(self._show_tab_menu)
        self._split.addWidget(self.tabs)
        self._ai_panel = self._build_ai_panel()
        self._ai_panel.setVisible(False)
        self._split.addWidget(self._ai_panel)
        self._split.setStretchFactor(0, 1)
        outer.addWidget(self._split, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#8a8f98; font-size:11px; padding:2px 10px;")
        outer.addWidget(self._status)

        # Fullscreen state (HTML5 page fullscreen + F11 window fullscreen).
        self._fs_active = False       # a web PAGE is currently fullscreen (chrome hidden)
        self._fs_hidden: list = []    # chrome widgets we hid, to restore exactly
        self._fs_was_max = False

        for seq, fn in (("Ctrl+T", lambda: self._new_tab()),
                        ("Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex())),
                        ("Ctrl+L", lambda: (self.address.setFocus(), self.address.selectAll())),
                        ("Ctrl+F", self._toggle_find),
                        ("Ctrl+J", self._show_downloads),
                        ("Ctrl+Shift+A", self._show_tab_search),
                        ("Ctrl+Shift+T", self._restore_last_closed_tab),
                        ("F11", self._toggle_window_fullscreen),
                        ("Escape", self._exit_any_fullscreen),
                        ("Ctrl+=", lambda: self._zoom(0.1)), ("Ctrl++", lambda: self._zoom(0.1)),
                        ("Ctrl+-", lambda: self._zoom(-0.1)), ("Ctrl+0", lambda: self._zoom(0))):
            QShortcut(QKeySequence(seq), self, activated=fn)

        session = self._load_session()
        if session:
            for url in session[:12]:
                self._new_tab(url=url)
        else:
            self._new_tab()  # opens the Ember Search start page

    # ---- fullscreen (HTML5 video fullscreen + F11 window fullscreen) ----
    def _on_fullscreen_requested(self, request, _view):
        """A web page (video player, presentation, game) asked to enter/exit fullscreen. Accept
        it AND hide the browser chrome so the content actually fills the screen — without both,
        clicking fullscreen on a YouTube video did nothing."""
        try:
            request.accept()
        except Exception:
            return
        try:
            entering = bool(request.toggleOn())
        except Exception:
            entering = not self._fs_active
        if entering:
            self._enter_page_fullscreen()
        else:
            self._exit_page_fullscreen()

    def _enter_page_fullscreen(self):
        if self._fs_active:
            return
        self._fs_active = True
        self._fs_was_max = self.isMaximized()
        # Hide every bit of chrome, remembering exactly what was visible to restore it later.
        self._fs_hidden = []
        for w in (getattr(self, "_chrome", None), getattr(self, "_loadbar", None),
                  getattr(self, "_find_bar", None), getattr(self, "_status", None)):
            if w is not None and w.isVisible():
                self._fs_hidden.append(w)
                w.setVisible(False)
        try:
            self.tabs.tabBar().setVisible(False)
        except Exception:
            pass
        self.showFullScreen()

    def _exit_page_fullscreen(self):
        if not self._fs_active:
            return
        self._fs_active = False
        for w in self._fs_hidden:
            try:
                w.setVisible(True)
            except Exception:
                pass
        self._fs_hidden = []
        try:
            self.tabs.tabBar().setVisible(True)
        except Exception:
            pass
        self.showMaximized() if self._fs_was_max else self.showNormal()

    def _toggle_window_fullscreen(self):
        """F11: fullscreen the whole browser window (chrome stays visible), like any browser."""
        if self.isFullScreen():
            self._exit_page_fullscreen() if self._fs_active else self.showNormal()
        else:
            self._fs_was_max = self.isMaximized()
            self.showFullScreen()

    def _exit_any_fullscreen(self):
        """Esc: leave page fullscreen if a page put us there; also un-fullscreen a plain F11
        window fullscreen. A no-op otherwise (so Esc doesn't disrupt normal browsing)."""
        if self._fs_active:
            # Also tell the page to drop its own fullscreen state so its UI stays consistent.
            try:
                v = self._cur()
                if v is not None:
                    v.triggerPageAction(QWebEnginePage.WebAction.ExitFullScreen)
            except Exception:
                pass
            self._exit_page_fullscreen()
        elif self.isFullScreen():
            self.showNormal()

    def _show_more_menu(self):
        """Keep the everyday toolbar calm while preserving every advanced browser feature."""
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        bookmarks = menu.addAction("Bookmarks")
        history = menu.addAction("History")
        downloads = menu.addAction("Downloads")
        downloads.setShortcut(QKeySequence("Ctrl+J"))
        menu.addSeparator()
        duplicate = menu.addAction("Duplicate current tab")
        reopen = menu.addAction("Reopen closed tab")
        reopen.setEnabled(bool(self._closed_tabs))
        menu.addSeparator()
        reader = menu.addAction("Reader mode")
        dark = menu.addAction("Dark mode for this site")
        find = menu.addAction("Find on page…")
        find.setShortcut(QKeySequence("Ctrl+F"))
        menu.addSeparator()
        ai_check = menu.addAction("Check page for AI-generated text")
        passwords = menu.addAction("Passwords and autofill")
        extensions = menu.addAction("Extensions")
        menu.addSeparator()
        fullscreen = menu.addAction("Enter fullscreen")
        fullscreen.setShortcut(QKeySequence("F11"))
        chosen = menu.exec(QCursor.pos())
        handlers = {
            bookmarks: self._show_bookmarks_menu,
            history: self._show_history_menu,
            downloads: self._show_downloads,
            duplicate: self._duplicate_current_tab,
            reopen: self._restore_last_closed_tab,
            reader: self._reader_mode,
            dark: self._toggle_dark,
            find: self._toggle_find,
            ai_check: self._ai_check_page,
            passwords: self._show_password_menu,
            extensions: self._show_extensions_menu,
            fullscreen: self._toggle_window_fullscreen,
        }
        if chosen in handlers:
            handlers[chosen]()

    def _show_privacy_menu(self):
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        blocked = menu.addAction(f"{self._guard.blocked:,} tracker requests blocked this session")
        blocked.setEnabled(False)
        toggle = menu.addAction("Pause tracker blocking" if self._guard.enabled else "Resume tracker blocking")
        menu.addSeparator()
        clear_site = menu.addAction("Clear cookies and cache…")
        clear_history = menu.addAction("Clear browsing history…")
        chosen = menu.exec(QCursor.pos())
        if chosen is toggle:
            self._guard.enabled = not self._guard.enabled
            self._status.setText("Tracker blocking on" if self._guard.enabled else "Tracker blocking paused")
        elif chosen is clear_site:
            self._clear_site_data()
        elif chosen is clear_history:
            if QMessageBox.question(
                    self, "Clear browsing history", "Clear Ember Browser's local browsing history?"
                    ) == QMessageBox.StandardButton.Yes:
                self._history.clear()
                self._save_history()
                self._status.setText("Browsing history cleared")

    def _clear_site_data(self):
        if QMessageBox.question(
                self, "Clear cookies and cache",
                "Clear cookies and cached website data? You may be signed out of websites."
                ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._profile.cookieStore().deleteAllCookies()
            self._profile.clearHttpCache()
            self._status.setText("Cookies and cache cleared")
        except Exception as exc:
            QMessageBox.warning(self, "Clear site data", str(exc))

    # ---- tabs ----
    def _new_tab(self, url: str | None = None, *, blank: bool = False):
        view = QWebEngineView()
        page = _Page(self._profile, view)
        # Back-reference so the page's createWindow() can open target=_blank / window.open links
        # as real Ember tabs instead of dropping them.
        page._browser = self
        # Queued, NOT direct: _ember_search calls setHtml, and doing that synchronously from
        # inside acceptNavigationRequest re-enters QtWebEngine and crashes. Defer to the loop.
        page.searchRequested.connect(self._ember_search, Qt.ConnectionType.QueuedConnection)
        page.configRequested.connect(self._apply_config, Qt.ConnectionType.QueuedConnection)
        view.setPage(page)
        s = view.settings()
        try:
            # True (with createWindow above) so window.open() opens a new TAB; without it, JS-
            # opened links do nothing. Anchor target=_blank works regardless via createWindow.
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
            # Without this, HTML5 fullscreen (a video's fullscreen button, presentations, games)
            # is disabled at the engine level and clicking it does nothing.
            s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        except Exception:
            pass
        # A page asking to go fullscreen must be explicitly accepted AND the window made
        # fullscreen; QtWebEngine won't do either on its own.
        try:
            page.fullScreenRequested.connect(lambda req, v=view: self._on_fullscreen_requested(req, v))
        except Exception:
            pass
        view.urlChanged.connect(lambda u, v=view: self._on_url_changed(v, u))
        view.titleChanged.connect(lambda t, v=view: self._on_title(v, t))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(v, ok))
        view.loadStarted.connect(lambda v=view: self._on_load_progress(v, 0))
        view.loadProgress.connect(lambda pct, v=view: self._on_load_progress(v, pct))
        idx = self.tabs.addTab(view, "New tab")
        self.tabs.setCurrentIndex(idx)
        if url:
            self._navigate(url, view)
        elif not blank:
            view.setHtml(self._home_html(), QUrl(f"https://{SEARCH_HOST}/"))
        # blank=True: leave the page empty — createWindow returns it and the engine loads the
        # target URL into it (loading home first would just be overwritten).
        return view

    def _new_tab_page(self):
        """Open a blank foreground tab and return its page — used by _Page.createWindow so
        new-tab / target=_blank / window.open links open in a real Ember tab."""
        return self._new_tab(blank=True).page()

    def _close_tab(self, index: int):
        if index < 0:
            return
        w = self.tabs.widget(index)
        try:
            url = w.url().toString() if w is not None else ""
            if url and not url.startswith("data:"):
                self._closed_tabs.append(url)
                self._closed_tabs = self._closed_tabs[-20:]
        except Exception:
            pass
        self.tabs.removeTab(index)
        if w is not None:
            w.deleteLater()
        if self.tabs.count() == 0:
            self._new_tab()

    def _restore_last_closed_tab(self):
        if self._closed_tabs:
            self._new_tab(url=self._closed_tabs.pop())

    def _duplicate_current_tab(self):
        view = self._cur()
        if view is not None:
            self._new_tab(url=view.url().toString())

    def _show_tab_search(self):
        if not self.tabs.count():
            return
        labels = [self.tabs.tabText(i) or "Untitled tab" for i in range(self.tabs.count())]
        chosen, ok = QInputDialog.getItem(self, "Search tabs", "Switch to:", labels, 0, False)
        if ok and chosen in labels:
            self.tabs.setCurrentIndex(labels.index(chosen))

    def _cur(self):
        return self.tabs.currentWidget()

    def _on_tab_changed(self, _i):
        if self._cur() is not None:
            self._sync_address(self._cur().url())

    def _go_home(self):
        v = self._cur() or self._new_tab()
        v.setHtml(self._home_html(), QUrl(f"https://{SEARCH_HOST}/"))
        self.address.clear()

    # ---- navigation ----
    def _to_url(self, text: str) -> str:
        text = text.strip()
        if "://" in text:
            return text
        if " " not in text and "." in text:
            return "https://" + text
        return ""   # not a URL -> caller does Ember Search

    def _navigate_from_bar(self):
        text = self.address.text().strip()
        if not text:
            return
        low = text.lower()
        if low.startswith("ai ") or text.endswith("?"):
            q = text[3:].strip() if low.startswith("ai ") else text
            self._set_ai_panel_visible(True)
            self._ask_web(q)
            return
        url = self._to_url(text)
        if url:
            self._navigate(url)
        else:
            self._ember_search(text)

    def _navigate(self, url: str, view=None):
        view = view or self._cur()
        if view is None:
            return
        try:
            import web_policy
            verdict = web_policy.check_url(url)
            if isinstance(verdict, dict) and verdict.get("allowed") is False:
                self._status.setText(f"⛔ Blocked by web policy: {verdict.get('reason', url)}")
                return
        except Exception:
            pass
        view.setUrl(QUrl(url))

    def _on_url_changed(self, view, qurl):
        if view is self._cur():
            self._sync_address(qurl)

    def _sync_address(self, qurl):
        s = qurl.toString()
        if SEARCH_HOST in s:
            return
        self.address.setText(s)
        self.address.setCursorPosition(0)
        secure = qurl.scheme() == "https"
        self._lock.setText("●")
        self._lock.setProperty("secure", secure)
        self._lock.setToolTip("Secure connection" if secure else "Connection is not encrypted")
        self._lock.style().unpolish(self._lock)
        self._lock.style().polish(self._lock)

    def _on_title(self, view, title):
        i = self.tabs.indexOf(view)
        if i >= 0:
            self.tabs.setTabText(i, (title or "New tab")[:24])
        self._record_history(view.url().toString(), title)

    def _refresh_status(self):
        self._status.setText(f"🛡 {self._guard.blocked} trackers blocked this session"
                             f"   ·   {len(self._bookmarks)} bookmarks")
        try:
            self._privacy_btn.setToolTip(
                f"Privacy · {self._guard.blocked:,} tracker requests blocked this session")
        except Exception:
            pass

    def _on_load_progress(self, view, pct: int):
        """Drive the slim top progress line as the CURRENT tab loads (ignore background tabs)."""
        bar = getattr(self, "_loadbar", None)
        if bar is None or view is not self._cur():
            return
        try:
            if pct < 100:
                if not bar.isVisible():
                    bar.setVisible(True)
                bar.setValue(pct)
            else:
                bar.setValue(100)
                QTimer.singleShot(280, lambda: bar.setVisible(False) if bar.value() >= 100 else None)
        except Exception:
            pass

    def _on_load_finished(self, view, ok):
        self._on_load_progress(view, 100)
        self._refresh_status()
        if not ok:
            return
        # Password autofill: if we have a saved login for this domain, offer to fill it.
        try:
            url = view.url().toString()
            if SEARCH_HOST in url or not url.startswith("http"):
                return
            import browser_passwords
            login = browser_passwords.get_login(url)
            if login:
                self._status.setText(f"🔑 Saved login for {login['domain']} — click the 🔑 button to fill")
                self._pending_autofill_domain = login["domain"]
        except Exception:
            pass
        # Inject any enabled, matching AI-built extensions (userscripts) for this page.
        try:
            import browser_extensions
            url = view.url().toString()
            if url.startswith("http"):
                scripts = browser_extensions.scripts_for_url(url)
                for ext in scripts:
                    view.page().runJavaScript(
                        browser_extensions.wrap_for_injection(ext.get("js", "")))
                if scripts:
                    names = ", ".join(e.get("name", "?") for e in scripts)
                    self._status.setText(f"🧩 Ran extension(s): {names}")
        except Exception:
            pass

    # ---- password manager ----
    def _current_domain(self) -> str:
        try:
            import browser_passwords
            v = self._cur()
            return browser_passwords._domain(v.url().toString()) if v is not None else ""
        except Exception:
            return ""

    def _show_password_menu(self):
        import browser_passwords
        dom = self._current_domain()
        menu = QMenu(self)
        act_save = menu.addAction(f"Save login for {dom or 'this site'}…")
        act_fill = menu.addAction(f"Fill login on {dom or 'this site'}")
        act_fill.setEnabled(bool(dom and browser_passwords.get_login(dom)))
        menu.addSeparator()
        act_manage = menu.addAction("Manage saved logins…")
        from PyQt6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())
        if chosen is act_save:
            self._save_login_ui()
        elif chosen is act_fill:
            self._fill_login()
        elif chosen is act_manage:
            self._manage_logins()

    def _save_login_ui(self):
        import browser_passwords
        dom = self._current_domain()
        if not dom:
            QMessageBox.information(self, "Passwords", "Open a website first, then save its login.")
            return
        existing = browser_passwords.get_login(dom) or {}
        user, ok = QInputDialog.getText(self, "Save login", f"Username for {dom}:",
                                        _QLE.EchoMode.Normal, existing.get("username", ""))
        if not ok:
            return
        pw, ok = QInputDialog.getText(self, "Save login", f"Password for {dom}:",
                                      _QLE.EchoMode.Password, existing.get("password", ""))
        if not ok:
            return
        if browser_passwords.save_login(dom, user.strip(), pw):
            QMessageBox.information(self, "Passwords", f"Saved login for {dom} (encrypted).")
        else:
            QMessageBox.warning(self, "Passwords", "Could not save the login.")

    def _fill_login(self):
        import browser_passwords
        v = self._cur()
        dom = self._current_domain()
        if v is None or not dom:
            return
        login = browser_passwords.get_login(dom)
        if not login:
            QMessageBox.information(self, "Passwords", f"No saved login for {dom}.")
            return
        try:
            v.page().runJavaScript(browser_passwords.autofill_js(login))
            self._status.setText(f"🔑 Filled login for {dom}")
        except Exception as e:
            QMessageBox.warning(self, "Passwords", f"Autofill failed: {e}")

    def _manage_logins(self):
        import browser_passwords
        doms = browser_passwords.list_logins()
        if not doms:
            QMessageBox.information(self, "Saved logins", "No saved logins yet.")
            return
        dom, ok = QInputDialog.getItem(self, "Saved logins",
                                       "Select a site to delete its saved login:", doms, 0, False)
        if ok and dom:
            if QMessageBox.question(self, "Delete login", f"Delete the saved login for {dom}?") \
                    == QMessageBox.StandardButton.Yes:
                browser_passwords.delete_login(dom)
                self._status.setText(f"Deleted saved login for {dom}")

    # ---- AI-built extensions (userscripts) ----
    def _show_extensions_menu(self):
        import browser_extensions
        menu = QMenu(self)
        make = menu.addAction("✨ Make an extension with AI…")
        menu.addSeparator()
        action_map = {}
        exts = browser_extensions.list_extensions()
        if exts:
            for e in exts:
                mark = "●" if e.get("enabled", True) else "○"
                sub = menu.addMenu(f"{mark} {e.get('name', 'Untitled')}")
                action_map[sub.addAction("Run on this page now")] = ("run", e)
                action_map[sub.addAction("Disable" if e.get("enabled", True) else "Enable")] = ("toggle", e)
                action_map[sub.addAction("Edit JavaScript…")] = ("edit", e)
                action_map[sub.addAction("Delete")] = ("delete", e)
        else:
            none = menu.addAction("(no extensions yet — make one!)")
            none.setEnabled(False)
        from PyQt6.QtGui import QCursor
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is make:
            self._make_extension_ai()
            return
        kind_ext = action_map.get(chosen)
        if not kind_ext:
            return
        kind, e = kind_ext
        if kind == "run":
            self._run_extension_now(e)
        elif kind == "toggle":
            new_state = not e.get("enabled", True)
            browser_extensions.set_enabled(e["id"], new_state)
            self._status.setText(("Enabled " if new_state else "Disabled ") + e.get("name", ""))
        elif kind == "edit":
            self._edit_extension(e)
        elif kind == "delete":
            if QMessageBox.question(self, "Delete extension",
                                    f"Delete “{e.get('name', '')}”?") == QMessageBox.StandardButton.Yes:
                browser_extensions.delete_extension(e["id"])
                self._status.setText("Deleted extension")

    def _make_extension_ai(self):
        import browser_extensions
        desc, ok = QInputDialog.getMultiLineText(
            self, "Make an extension",
            "Describe what it should do — Ember's AI writes the JavaScript:\n"
            "(e.g. “hide the comments section”, “give every page a dark background”)", "")
        if not ok or not desc.strip():
            return
        v = self._cur()
        cur_url = v.url().toString() if v is not None else ""
        try:
            default_match = urlparse(cur_url).netloc or "*"
        except Exception:
            default_match = "*"
        match, ok = QInputDialog.getText(
            self, "Where should it run?",
            "URL match — a domain (youtube.com), a glob (*.example.com/*), or * for every site:",
            _QLE.EchoMode.Normal, default_match)
        if not ok:
            return
        match = match.strip() or "*"
        name, ok = QInputDialog.getText(self, "Name it", "Extension name:",
                                        _QLE.EchoMode.Normal, desc.strip()[:40])
        if not ok:
            return
        name = name.strip() or "Untitled"
        self._status.setText("🧩 Ember is writing your extension…")

        def work():
            out = self._model_text(browser_extensions.build_userscript_prompt(desc, cur_url))
            self._ext_made.emit(name, match, desc.strip(), browser_extensions.extract_js(out))
        threading.Thread(target=work, daemon=True).start()

    def _on_ext_made(self, name, match, desc, js):
        import browser_extensions
        if not js.strip() or js.lstrip().startswith(("AI error", "Add a Gemini", "Add an Anthropic")):
            QMessageBox.warning(self, "Extension",
                                js.strip() or "The AI didn't return any JavaScript. Try rephrasing.")
            self._status.setText("Extension not created")
            return
        # It's code that will run on real pages — let the user review/edit before saving.
        reviewed, ok = QInputDialog.getMultiLineText(
            self, f"Review “{name}”",
            "Ember wrote this JavaScript. Review/edit it, then OK to save & enable:", js)
        if not ok:
            self._status.setText("Extension discarded")
            return
        ext = browser_extensions.save_extension(name, match, reviewed, description=desc)
        self._status.setText(f"🧩 Saved “{name}” — runs on {match}")
        self._run_extension_now(ext)

    def _run_extension_now(self, ext):
        import browser_extensions
        v = self._cur()
        if v is None:
            return
        url = v.url().toString()
        if not browser_extensions.match_url(ext.get("match", "*"), url):
            self._status.setText(
                f"“{ext.get('name', '')}” is scoped to {ext.get('match', '*')} — not this page")
            return
        try:
            v.page().runJavaScript(browser_extensions.wrap_for_injection(ext.get("js", "")))
            self._status.setText(f"🧩 Ran “{ext.get('name', '')}”")
        except Exception as e:
            self._status.setText(f"Extension error: {e}")

    def _edit_extension(self, ext):
        import browser_extensions
        js, ok = QInputDialog.getMultiLineText(
            self, f"Edit “{ext.get('name', '')}”", "JavaScript:", ext.get("js", ""))
        if not ok:
            return
        browser_extensions.save_extension(
            ext.get("name", ""), ext.get("match", "*"), js,
            description=ext.get("description", ""), ext_id=ext.get("id"),
            enabled=ext.get("enabled", True))
        self._status.setText("Updated extension")

    # ---- tab groups ----
    _GROUP_COLORS = [("Red", "#f7768e"), ("Amber", "#e0af68"), ("Green", "#9ece6a"),
                     ("Blue", "#7aa2f7"), ("Purple", "#bb9af7"), ("Cyan", "#7dcfff")]

    def _show_tab_menu(self, pos):
        from PyQt6.QtGui import QColor
        tb = self.tabs.tabBar()
        index = tb.tabAt(pos)
        if index < 0:
            return
        menu = QMenu(self)
        group_menu = menu.addMenu("Add tab to group")
        for gname, gcolor in self._GROUP_COLORS:
            act = group_menu.addAction(f"● {gname}")
            act.setData((index, gcolor))
        ungroup = menu.addAction("Remove from group")
        menu.addSeparator()
        duplicate_act = menu.addAction("Duplicate tab")
        close_act = menu.addAction("Close tab")
        chosen = menu.exec(tb.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is close_act:
            self._close_tab(index)
        elif chosen is duplicate_act:
            view = self.tabs.widget(index)
            if view is not None:
                self._new_tab(url=view.url().toString())
        elif chosen is ungroup:
            tb.setTabTextColor(index, QColor())
        elif chosen.data():
            idx, color = chosen.data()
            tb.setTabTextColor(idx, QColor(color))

    # ---- Ember Search ----
    # ---- Ember Search theming / customisation ----
    def _theme_file(self) -> Path:
        return self._data_file().with_name("browser_theme.json")

    def _load_theme(self) -> dict:
        t = {k: (list(v) if isinstance(v, list) else v) for k, v in _DEFAULT_THEME.items()}
        try:
            saved = json.loads(self._theme_file().read_text())
            if isinstance(saved, dict):
                for k in _DEFAULT_THEME:
                    if k in saved:
                        t[k] = saved[k]
        except Exception:
            pass
        if not isinstance(t.get("shortcuts"), list):
            t["shortcuts"] = [dict(s) for s in _DEFAULT_SHORTCUTS]
        return t

    def _save_theme(self):
        try:
            self._theme_file().write_text(json.dumps(self._theme, indent=2))
        except Exception:
            pass

    def _accent_pair(self):
        t = getattr(self, "_theme", _DEFAULT_THEME)
        if t.get("preset") in _SEARCH_PRESETS:
            return _SEARCH_PRESETS[t["preset"]]
        return (t.get("accent") or "#e8632e", t.get("accent2") or t.get("accent") or "#f0a13c")

    def _qss(self) -> str:
        """The Qt chrome stylesheet, re-tinted to the user's chosen accent so the whole browser
        feels cohesive with the search theme."""
        return BROWSER_QSS.replace("#e2562a", self._accent_pair()[0])

    def _shell(self, body: str, *, home: bool) -> str:
        """Wrap page body in the shared Ember Search shell: theme CSS + a head script that sets
        the accent/mode CSS variables from EMBER_CFG before first paint (no theme flash)."""
        cfg = json.dumps({
            "preset": self._theme.get("preset", "ember"),
            "accent": self._theme.get("accent", "#e8632e"),
            "accent2": self._theme.get("accent2", "#f0a13c"),
            "mode": self._theme.get("mode", "dark"),
            "clock": bool(self._theme.get("clock", True)),
            "shortcuts": self._theme.get("shortcuts", []),
        })
        js = _SEARCH_HEAD_JS + (_HOME_JS if home else "")
        return ("<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<style>{_SEARCH_CSS}</style>"
                f"<script>window.EMBER_CFG={cfg};</script>"
                f"<script>{js}</script>"
                "</head><body>" + body + "</body></html>")

    def _apply_config(self, cfg_json: str):
        """Persist a customisation change posted from the search page (?embercfg=…) and re-tint
        the native chrome to match. Runs on the GUI thread (queued from the page signal)."""
        try:
            cfg = json.loads(cfg_json)
        except Exception:
            return
        if not isinstance(cfg, dict):
            return
        for k in ("preset", "accent", "accent2", "mode", "clock", "shortcuts"):
            if k in cfg:
                self._theme[k] = cfg[k]
        self._save_theme()
        try:
            self.setStyleSheet(self._qss())
        except Exception:
            pass

    _SEARCH_SVG = ("<svg width=19 height=19 viewBox='0 0 24 24' fill=none stroke='currentColor' "
                   "stroke-width=2 stroke-linecap=round><circle cx=11 cy=11 r=7></circle>"
                   "<path d='M21 21l-4.2-4.2'></path></svg>")

    def _results_header(self, query: str) -> str:
        return ("<div class=rhead><span class=mark>Ember</span>"
                f"<form class=searchbox action='https://{SEARCH_HOST}/' method=get>{self._SEARCH_SVG}"
                "<input name=q autocomplete=off spellcheck=false autocapitalize=off "
                f"aria-label='Search the web' value=\"{_html.escape(query)}\">"
                "<button type=submit>Search</button></form></div>")

    def _home_html(self) -> str:
        swatches = "".join(
            f"<span class=sw data-p='{name}' title='{name.title()}' "
            f"style='background:linear-gradient(135deg,{a},{b})'></span>"
            for name, (a, b) in _SEARCH_PRESETS.items())
        accent = self._theme.get("accent", "#e8632e") if self._theme.get("preset") == "custom" \
            else self._accent_pair()[0]
        panel = (
            "<div class=panel id=panel>"
            "<div class=row><h2>Customise</h2>"
            "<button class='pbtn ghost' id=closePanel style='width:auto;margin:0;padding:6px 12px'>Done</button></div>"
            "<div class=sub>Make Ember Search yours — saved for next time.</div>"
            f"<div class=grp><label>Theme</label><div class=swatches id=swatches>{swatches}</div>"
            "<div class=row style='margin-top:12px'><span style='color:var(--muted);font-size:13px'>Custom accent</span>"
            f"<input type=color class=acc-in id=accIn value='{accent}'></div></div>"
            "<div class=grp><label>Appearance</label><div class=seg id=modeSeg>"
            "<button data-m=dark>Dark</button><button data-m=light>Light</button></div></div>"
            "<div class=grp><div class=row><label style='margin:0'>Greeting &amp; clock</label>"
            "<span class=switch id=clkSwitch><i></i></span></div></div>"
            "<div class=grp><label>Shortcuts</label>"
            "<div class=row style='gap:8px'>"
            "<input class=mini id=scLabel placeholder='Name' style='flex:1;min-width:0'>"
            "<input class=mini id=scUrl placeholder='site.com' style='flex:1.4;min-width:0'></div>"
            "<button class=pbtn id=scAdd style='margin-top:8px'>Add shortcut</button>"
            "<button class='pbtn ghost' id=editBtn style='margin-top:8px'>Edit shortcuts</button></div>"
            "<div class=grp><button class=pbtn id=saveBtn>Save</button>"
            "<button class='pbtn ghost' id=resetBtn style='margin-top:8px'>Reset to default</button></div>"
            "</div>")
        body = (
            "<div class=backdrop id=backdrop></div>"
            "<button class=gear id=gear title='Customise Ember Search' "
            "aria-label='Customise Ember Search'>&#9881;</button>" + panel +
            "<div class=wrap><div class=hero>"
            "<div class=logo>Ember<span class=spark>Search</span></div>"
            "<div class=greet id=greet></div>"
            f"<form class=searchbox action='https://{SEARCH_HOST}/' method=get>{self._SEARCH_SVG}"
            "<input name=q autofocus autocomplete=off spellcheck=false autocapitalize=off "
            "aria-label='Search the web' placeholder='Search the web, or ask Ember anything…'>"
            "<button type=submit>Search</button></form>"
            "<div class=tiles id=tiles></div>"
            "</div></div>")
        return self._shell(body, home=True)

    def _ember_search(self, query: str):
        query = (query or "").strip()
        if not query:
            return
        self.address.setText(query)
        v = self._cur() or self._new_tab()
        body = (f"<div class=wrap>{self._results_header(query)}"
                "<div class=answer><h3>&#10024; Ember AI is thinking&hellip;</h3><div class=body>"
                "<div class=skl style='width:94%'></div>"
                "<div class=skl style='width:80%;margin-top:9px'></div>"
                "<div class=skl style='width:88%;margin-top:9px'></div></div></div>"
                "<div class=empty>Searching the web&hellip;</div></div>")
        v.setHtml(self._shell(body, home=False), QUrl(f"https://{SEARCH_HOST}/"))
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _grounded_answer(self, query: str, results):
        """Answer a query GROUNDED in live web content: pull text from the top results and
        have the model answer from THAT (with citations) rather than its training data."""
        context = ""
        for i, (title, href) in enumerate(results[:3], 1):
            snippet = _fetch_page_text(href)
            if snippet:
                context += f"\n\n[{i}] {title} — {href}\n{snippet}"
        if not context:
            return self._model_text(
                "Answer this concisely and factually. If it needs current data you may not "
                "have, say so.\n\nQuery: " + query)
        return self._model_text(
            "You are Ember's web search. Answer the user's query using ONLY the live web "
            "results below (they are current — prefer them over your own memory). Be concise "
            "and cite sources inline as [1], [2], [3] matching the numbering. If the results "
            "don't answer it, say so.\n\n"
            f"WEB RESULTS:{context}\n\nQUERY: {query}")

    def _search_thread(self, query: str):
        results = _ddg(query)
        inst = _instant_answer(query)
        # Phase 1: show the web results + engine links IMMEDIATELY, with the answer card in a
        # "thinking" state — so the page is useful straight away instead of waiting on the (slow)
        # grounded AI answer (which fetches pages + a model call).
        self._search_result.emit(
            query, self._search_results_html(query, None, results, inst, pending=True))
        # Phase 2: compute the AI answer, then slot it into the card in place (no reload).
        answer = self._grounded_answer(query, results)
        self._answer_ready.emit(query, self._render_answer_html(answer, results))

    def _render_answer_html(self, answer, results):
        """The AI answer as inner HTML for #ansBody: escaped, with [n] citations linkified to
        the matching result."""
        import re
        ans = _html.escape(answer or "(no AI answer yet — add an API key in Ember Settings to "
                                     "get grounded answers. Web results are below.)")

        def _cite(m):
            n = int(m.group(1))
            if 1 <= n <= len(results):
                return f"<a class=cite href='{_html.escape(results[n - 1][1])}'>[{n}]</a>"
            return m.group(0)

        return re.sub(r"\[(\d+)\]", _cite, ans).replace("\n", "<br>")

    def _search_results_html(self, query, answer, results, inst=None, pending=False):
        # Instant answer (arithmetic etc.) gets its own prominent chip, not buried in the prose.
        calc = ""
        if inst:
            calc = ("<div class=calc><span>&#128425;</span>"
                    f"<span>{_html.escape(str(inst))}</span></div>")

        # AI answer card. In `pending` mode it shows a shimmer + a "thinking" header (the copy
        # button stays hidden); phase 2 fills #ansBody in place via _update_search_answer. The
        # data-q marker lets that update ignore a stale answer if the user searched again.
        if pending:
            head = "&#10024; Ember AI is thinking&hellip;"
            inner = ("<div class=skl style='width:94%'></div>"
                     "<div class=skl style='width:80%;margin-top:9px'></div>"
                     "<div class=skl style='width:88%;margin-top:9px'></div>")
            copy_attr = " style='display:none'"
        else:
            head = "&#10024; Ember AI answer"
            inner = self._render_answer_html(answer, results)
            copy_attr = ""
        dq = _html.escape(query, quote=True)
        answer_card = (f"<div class=answer id=answerCard data-q=\"{dq}\">"
                       f"<button class=copy id=copyBtn{copy_attr}>Copy</button>"
                       f"<h3 id=ansHead>{head}</h3>"
                       f"<div class=body id=ansBody>{inner}</div></div>")

        # Result cards, each with a favicon (letter fallback) and a clean domain line.
        cards = ""
        for title, href in results:
            dom = urlparse(href).netloc.replace("www.", "")
            lt = _html.escape((dom[:1] or "?").upper())
            if dom:
                fav = _html.escape(f"https://icons.duckduckgo.com/ip3/{dom}.ico")
                ic = (f"<img src='{fav}' referrerpolicy=no-referrer loading=lazy "
                      "onerror=\"this.style.display='none';"
                      "this.nextElementSibling.style.display='flex'\">"
                      f"<span class=ltr style='display:none'>{lt}</span>")
            else:
                ic = f"<span class=ltr>{lt}</span>"
            cards += (f"<a class=card href='{_html.escape(href)}'><span class=fav>{ic}</span>"
                      f"<span class=txt><span class=ti>{_html.escape(title)}</span>"
                      f"<span class=dom>{_html.escape(dom or href)}</span></span></a>")
        if cards:
            cards = f"<div class=reslist>{cards}</div>"
        else:
            cards = ("<div class=empty>No web results came back this time. "
                     f"<a href='https://duckduckgo.com/?q={quote_plus(query)}'>Open DuckDuckGo</a> "
                     "to search directly.</div>")

        q = quote_plus(query)
        engines = [("DuckDuckGo", f"https://duckduckgo.com/?q={q}"),
                   ("Brave", f"https://search.brave.com/search?q={q}"),
                   ("Google", f"https://www.google.com/search?q={q}"),
                   ("Startpage", f"https://www.startpage.com/sp/search?query={q}"),
                   ("Wikipedia", f"https://en.wikipedia.org/w/index.php?search={q}")]
        pills = ("<div class=pills><span class=lbl>Also search on</span>"
                 + "".join(f"<a class=pill href='{u}'>{n}</a>" for n, u in engines) + "</div>")

        # Copy button: clipboard API first (ember.search is a secure origin), textarea fallback.
        copy_js = ("<script>(function(){var b=document.getElementById('copyBtn');if(!b)return;"
                   "b.addEventListener('click',function(){"
                   "var t=(document.getElementById('ansBody')||{}).textContent||'';"
                   "function done(){b.textContent='Copied \\u2713';"
                   "setTimeout(function(){b.textContent='Copy'},1400);}"
                   "function fb(){var a=document.createElement('textarea');a.value=t;"
                   "document.body.appendChild(a);a.select();"
                   "try{document.execCommand('copy')}catch(e){}document.body.removeChild(a);done();}"
                   "try{if(navigator.clipboard&&navigator.clipboard.writeText)"
                   "navigator.clipboard.writeText(t).then(done,fb);else fb();}catch(e){fb();}"
                   "});})();</script>")

        body = (f"<div class=wrap>{self._results_header(query)}"
                f"{calc}{answer_card}{cards}{pills}</div>{copy_js}")
        return self._shell(body, home=False)

    def _load_search_results(self, query, html):
        v = self._cur()
        if v is not None:
            v.setHtml(html, QUrl(f"https://{SEARCH_HOST}/"))

    def _update_search_answer(self, query, answer_html):
        """Phase 2: drop the finished AI answer into the results page's answer card in place
        (no reload, so the results the user is already reading don't jump) — but only if the
        current page is still showing THIS query (data-q guard against a newer search)."""
        v = self._cur()
        if v is None:
            return
        js = ("(function(){var card=document.getElementById('answerCard');"
              "if(!card||card.getAttribute('data-q')!==%s)return;"
              "var c=document.getElementById('ansBody');if(c)c.innerHTML=%s;"
              "var h=document.getElementById('ansHead');if(h)h.innerHTML='\\u2728 Ember AI answer';"
              "var b=document.getElementById('copyBtn');if(b)b.style.display='';"
              "})();" % (json.dumps(query), json.dumps(answer_html)))
        try:
            v.page().runJavaScript(js)
        except Exception:
            pass

    # ---- AI panel ----
    def _build_ai_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(460)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        t = QLabel("✨ Ember AI")
        t.setStyleSheet("font-weight:800; font-size:13px;")
        lay.addWidget(t)
        row = QHBoxLayout()
        sb = QPushButton("Summarize page")
        sb.clicked.connect(lambda: self._ask_ai("Summarize this page in a few clear bullet points."))
        cb = QPushButton("AI-check page")
        cb.clicked.connect(self._ai_check_page)
        wb = QPushButton("🌐 Web")
        wb.setToolTip("Search the live web to answer what's in the box")
        wb.clicked.connect(lambda: self._ask_web(self._ai_in.text().strip()))
        row.addWidget(sb)
        row.addWidget(cb)
        row.addWidget(wb)
        lay.addLayout(row)
        self._ai_out = QTextBrowser()
        self._ai_out.setOpenExternalLinks(True)
        lay.addWidget(self._ai_out, 1)
        self._ai_in = QLineEdit()
        self._ai_in.setPlaceholderText("Ask about this page  ·  or click 🌐 Web to search the internet")
        # Enter = ask about the current page; 🌐 Web = search the internet.
        self._ai_in.returnPressed.connect(lambda: self._ask_ai(self._ai_in.text().strip()))
        lay.addWidget(self._ai_in)
        return panel

    def _toggle_ai(self):
        self._set_ai_panel_visible(not self._ai_panel.isVisible())

    def _set_ai_panel_visible(self, show: bool):
        """Show/hide the AI side panel with a quick opacity fade (it's a plain QWidget, so
        an opacity effect is safe here — unlike the native web view)."""
        panel = self._ai_panel
        if show == panel.isVisible() and show:
            return
        try:
            eff = panel.graphicsEffect()
            if not isinstance(eff, QGraphicsOpacityEffect):
                eff = QGraphicsOpacityEffect(panel)
                panel.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(160)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            if show:
                panel.setVisible(True)
                eff.setOpacity(0.0)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
            else:
                anim.setStartValue(1.0)
                anim.setEndValue(0.0)
                anim.finished.connect(lambda: panel.setVisible(False))
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._panel_anim = anim   # keep a ref so it isn't GC'd mid-flight
        except Exception:
            panel.setVisible(show)

    def _ask_web(self, question: str):
        """Answer a general question by SEARCHING THE WEB (live results), not just the model's
        memory. Used for address-bar 'ai …' / '?' queries and the AI panel's 🌐 Web button."""
        if not question:
            return
        self._set_ai_panel_visible(True)
        self._ai_in.clear()
        self._ai_out.append(f"<b>You:</b> {_html.escape(question)}")
        self._ai_out.append("<i>🌐 Searching the web…</i>")

        def work():
            results = _ddg(question)
            ans = self._grounded_answer(question, results)
            if results:
                src = "<br>".join(f"[{i}] <a href='{_html.escape(h)}'>{_html.escape(t)}</a>"
                                  for i, (t, h) in enumerate(results[:3], 1))
                ans = ans + "\n\n<b>Sources:</b><br>" + src
            self._ai_result.emit(ans)
        threading.Thread(target=work, daemon=True).start()

    def _ask_ai(self, question: str):
        if not question:
            return
        self._set_ai_panel_visible(True)
        self._ai_in.clear()
        self._ai_out.append(f"<b>You:</b> {_html.escape(question)}")
        v = self._cur()
        if v is None:
            self._ai_result.emit("No page open.")
            return
        v.page().toPlainText(lambda text: threading.Thread(
            target=lambda: self._ai_result.emit(self._model_text(self._page_prompt(question, text or ""))),
            daemon=True).start())

    def _ai_check_page(self):
        self._set_ai_panel_visible(True)
        v = self._cur()
        if v is None:
            return
        self._ai_out.append("<b>AI check:</b> analyzing this page (URL + content)…")
        url = v.url().toString()

        # Grab HTML (for builder/provenance fingerprints) AND text (for the heuristic), then
        # run the whole-page detector off-thread — so e.g. a *.base44.app site is caught.
        def with_html(page_html):
            def with_text(text):
                def work():
                    try:
                        import ai_detect
                        r = ai_detect.detect_page(url=url, html=page_html or "", text=text or "")
                    except Exception as e:
                        r = {"ok": False, "error": str(e)}
                    if r.get("ok"):
                        self._ai_result.emit(f"🔎 AI-content check: <b>{r['verdict']}</b> "
                                             f"({r['ai_likelihood']}% AI-likelihood). {r.get('note', '')}")
                    else:
                        self._ai_result.emit(f"AI check: {r.get('error', 'could not analyze')}")
                threading.Thread(target=work, daemon=True).start()
            v.page().toPlainText(with_text)
        v.page().toHtml(with_html)

    def _page_prompt(self, question, page_text):
        url = self._cur().url().toString() if self._cur() else ""
        return ("You are Ember, an AI inside a web browser. Answer the user's request about the "
                "current page; be concise and say if the answer isn't on the page.\n\n"
                f"PAGE URL: {url}\nPAGE TEXT (truncated):\n{page_text[:14000]}\n\nUSER: {question}")

    def _show_ai_result(self, text: str):
        self._ai_out.append(f"<b>Ember:</b> {text}".replace("\n", "<br>"))

    def _model_text(self, prompt: str) -> str:
        provider = (self.settings.get("provider") or "").strip().lower()
        model = (self.settings.get("model_id") or self.settings.get("gemini_model") or "").strip()
        if not provider:
            provider = "claude" if "claude" in model.lower() else "gemini"
        try:
            if provider == "claude":
                key = "".join((self.settings.get("anthropic_api_key") or "").split())
                if not key:
                    return "Add an Anthropic API key in Ember Settings (⚙) to use Claude."
                import anthropic
                c = anthropic.Anthropic(api_key=key)
                mdl = model if "claude" in model.lower() else (self.settings.get("anthropic_model") or "claude-opus-4-8")
                r = c.messages.create(model=mdl, max_tokens=1024,
                                      messages=[{"role": "user", "content": prompt}])
                return ("".join(getattr(b, "text", "") for b in (r.content or [])) or "(no response)").strip()
            key = "".join((self.settings.get("gemini_api_key") or "").split())
            if not key:
                return "Add a Gemini API key in Ember Settings (⚙) to use AI features."
            from google import genai
            c = genai.Client(api_key=key)
            mdl = model if model and "claude" not in model.lower() else "gemini-3.1-flash-lite"
            return (getattr(c.models.generate_content(model=mdl, contents=prompt), "text", None)
                    or "(no response)").strip()
        except Exception as e:
            return f"AI error: {e}"

    # ---- find / zoom / bookmarks ----
    def _toggle_find(self):
        show = not self._find_bar.isVisible()
        self._find_bar.setVisible(show)
        if show:
            self._find_in.setFocus()
            self._find_in.selectAll()
        elif self._cur() is not None:
            self._cur().findText("")

    def _find_next(self, forward: bool):
        v = self._cur()
        if v is None:
            return
        flags = QWebEnginePage.FindFlag(0)
        if not forward:
            flags = QWebEnginePage.FindFlag.FindBackward
        v.findText(self._find_in.text(), flags)

    def _zoom(self, delta: float):
        v = self._cur()
        if v is None:
            return
        v.setZoomFactor(1.0 if delta == 0 else max(0.4, min(3.0, v.zoomFactor() + delta)))

    def _data_file(self) -> Path:
        try:
            import remote_server  # reuse the app's data dir if available
            d = remote_server._data_dir()
        except Exception:
            d = Path.home() / ".ember"
            d.mkdir(parents=True, exist_ok=True)
        return d / "bookmarks.json"

    def _session_path(self) -> Path:
        return self._data_file().with_name("browser_session.json")

    def _load_session(self) -> list[str]:
        try:
            data = json.loads(self._session_path().read_text(encoding="utf-8"))
            return [str(url) for url in data if isinstance(url, str) and url.startswith(("http://", "https://"))]
        except Exception:
            return []

    def _save_session(self):
        urls = []
        for index in range(self.tabs.count()):
            try:
                url = self.tabs.widget(index).url().toString()
                if url.startswith(("http://", "https://")) and SEARCH_HOST not in url:
                    urls.append(url)
            except Exception:
                pass
        try:
            self._session_path().write_text(json.dumps(urls[-12:], indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_session()
        super().closeEvent(event)

    def _load_bookmarks(self):
        try:
            return json.loads(self._data_file().read_text())
        except Exception:
            return []

    def _save_bookmarks(self):
        try:
            self._data_file().write_text(json.dumps(self._bookmarks, indent=2))
        except Exception:
            pass

    def _bookmark_current(self):
        v = self._cur()
        if v is None:
            return
        url = v.url().toString()
        title = self.tabs.tabText(self.tabs.currentIndex()) or url
        if url and not any(b.get("url") == url for b in self._bookmarks):
            self._bookmarks.append({"title": title, "url": url})
            self._save_bookmarks()
            self._status.setText(f"★ Bookmarked: {title}")
            self._refresh_status()

    def _show_bookmarks_menu(self):
        menu = QMenu(self)
        if not self._bookmarks:
            menu.addAction("(no bookmarks yet)").setEnabled(False)
        for b in self._bookmarks[-40:]:
            act = menu.addAction(b.get("title", b.get("url", "?"))[:60])
            act.triggered.connect(lambda _=False, u=b.get("url"): self._navigate(u))
        menu.exec(self.cursor().pos())

    # ---- history ----
    def _hist_path(self):
        return self._data_file().with_name("history.json")

    def _load_history(self):
        try:
            return json.loads(self._hist_path().read_text())
        except Exception:
            return []

    def _save_history(self):
        try:
            self._hist_path().write_text(json.dumps(self._history[-300:]))
        except Exception:
            pass

    def _record_history(self, url, title):
        if not url or SEARCH_HOST in url or url.startswith("data:"):
            return
        if self._history and self._history[-1].get("url") == url:
            if title:
                self._history[-1]["title"] = title
            return
        self._history.append({"url": url, "title": title or url})
        self._history = self._history[-300:]
        self._save_history()

    def _show_history_menu(self):
        menu = QMenu(self)
        if not self._history:
            menu.addAction("(no history yet)").setEnabled(False)
        for h in reversed(self._history[-40:]):
            act = menu.addAction((h.get("title") or h.get("url") or "?")[:60])
            act.triggered.connect(lambda _=False, u=h.get("url"): self._navigate(u))
        menu.exec(self.cursor().pos())

    # ---- downloads ----
    def _on_download(self, item):
        try:
            dl = Path.home() / "Downloads"
            dl.mkdir(parents=True, exist_ok=True)
            try:
                item.setDownloadDirectory(str(dl))
            except Exception:
                pass
            name = item.downloadFileName() if hasattr(item, "downloadFileName") else "file"
            path = str(dl / name)
            entry = {"name": name, "path": path, "status": "Downloading", "received": 0,
                     "total": 0, "item": item}
            self._downloads.insert(0, entry)
            self._downloads = self._downloads[:100]
            item.accept()
            self._status.setText(f"⬇ Downloading {name}…")
            try:
                item.receivedBytesChanged.connect(lambda *_, e=entry: self._update_download(e))
                item.totalBytesChanged.connect(lambda *_, e=entry: self._update_download(e))
                item.isFinishedChanged.connect(lambda *_, e=entry: self._finish_download(e))
            except Exception:
                pass
        except Exception as e:
            self._status.setText(f"Download error: {e}")

    def _update_download(self, entry: dict):
        item = entry.get("item")
        try:
            entry["received"] = int(item.receivedBytes())
            entry["total"] = int(item.totalBytes())
        except Exception:
            pass

    def _finish_download(self, entry: dict):
        item = entry.get("item")
        try:
            if not item.isFinished():
                return
        except Exception:
            pass
        entry["status"] = "Saved"
        self._update_download(entry)
        self._status.setText(f"✓ Saved {entry.get('name', 'download')} to Downloads")

    def _show_downloads(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Downloads")
        dialog.setMinimumSize(660, 440)
        dialog.setStyleSheet(self._qss())
        v = QVBoxLayout(dialog)
        title = QLabel("Downloads")
        title.setStyleSheet("font-size:18px;font-weight:800;padding:4px 2px;")
        v.addWidget(title)
        listing = QListWidget()
        for entry in self._downloads:
            total = int(entry.get("total") or 0)
            received = int(entry.get("received") or 0)
            detail = entry.get("status", "")
            if total > 0 and detail == "Downloading":
                detail += f" · {min(100, round(received * 100 / total))}%"
            row = QListWidgetItem(f"{entry.get('name', 'Download')}\n{detail} · {entry.get('path', '')}")
            row.setData(Qt.ItemDataRole.UserRole, entry.get("path", ""))
            listing.addItem(row)
        if not self._downloads:
            listing.addItem("No downloads this session.")
        v.addWidget(listing, 1)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear list")
        clear.clicked.connect(lambda: (self._downloads.clear(), listing.clear(),
                                       listing.addItem("No downloads this session.")))
        buttons.addWidget(clear)
        buttons.addStretch()
        show = QPushButton("Show in folder")

        def reveal():
            selected = listing.currentItem()
            path = selected.data(Qt.ItemDataRole.UserRole) if selected else ""
            if not path:
                return
            try:
                import subprocess
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", path])
                elif sys.platform.startswith("win"):
                    subprocess.Popen(["explorer", "/select,", path])
                else:
                    subprocess.Popen(["xdg-open", str(Path(path).parent)])
            except Exception as exc:
                QMessageBox.warning(dialog, "Downloads", str(exc))

        show.clicked.connect(reveal)
        buttons.addWidget(show)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        v.addLayout(buttons)
        dialog.exec()

    # ---- reader / dark mode ----
    def _reader_mode(self):
        v = self._cur()
        if v is not None:
            v.page().toPlainText(self._show_reader)

    def _show_reader(self, text):
        body = _html.escape((text or "").strip()).replace("\n\n", "</p><p>").replace("\n", "<br>")
        css = ("body{background:#15140f;color:#e8e6df;margin:0}"
               ".r{max-width:680px;margin:0 auto;padding:48px 22px;font:19px/1.75 Georgia,serif}")
        v = self._cur()
        if v is not None:
            v.setHtml(f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head>"
                      f"<body><div class='r'><p>{body}</p></div></body></html>",
                      QUrl(f"https://{SEARCH_HOST}/"))

    def _toggle_dark(self):
        v = self._cur()
        if v is None:
            return
        js = ("(function(){var id='__ember_dark';var e=document.getElementById(id);"
              "if(e){e.remove();}else{var s=document.createElement('style');s.id=id;"
              "s.textContent='html{filter:invert(1) hue-rotate(180deg)!important;background:#fff!important}"
              "img,video,picture,canvas,iframe,svg,[style*=\"background-image\"]"
              "{filter:invert(1) hue-rotate(180deg)!important}';"
              "document.documentElement.appendChild(s);}})();")
        v.page().runJavaScript(js)
