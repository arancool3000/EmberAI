"""Browser control via Chrome DevTools Protocol.
Launches (or attaches to) a Chrome/Edge instance with --remote-debugging-port enabled and drives the DOM
directly: enumerate interactive elements, click by text/selector, dismiss cookie banners, detect CAPTCHAs."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

try:
    import websocket  # websocket-client
except ImportError:
    websocket = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


PROFILE_DIR = _base_dir() / "browser_profile"


class CDPError(Exception):
    pass


COOKIE_DISMISS_JS = r"""
(function() {
  const ACCEPT_WORDS = ['accept all','accept cookies','accept','agree','allow all','allow','got it',
                        'understood','i accept','i agree','consent','ok','okay','continue','enable all'];
  const REJECT_WORDS = ['reject all','reject','decline all','decline','disagree','refuse','no thanks',
                        'only essential','only necessary','required only'];
  const wanted = window._ember_cookie_pref === 'reject' ? REJECT_WORDS : ACCEPT_WORDS;
  const candidates = Array.from(document.querySelectorAll(
      'button, a, [role="button"], input[type="button"], input[type="submit"], [tabindex]'
  ));
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
    return true;
  }
  function inBanner(el) {
    let e = el;
    for (let i = 0; i < 8 && e; i++) {
      const t = (e.id + ' ' + e.className + ' ' + (e.getAttribute('aria-label') || '')).toLowerCase();
      if (t.match(/cookie|consent|gdpr|privacy|banner|notice|onetrust|cookiebot|sp_message|truste|usercentrics|didomi/)) return true;
      e = e.parentElement;
    }
    return false;
  }
  for (const w of wanted) {
    for (const el of candidates) {
      if (!visible(el)) continue;
      const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
      if (!label) continue;
      if (label === w || label.startsWith(w + ' ') || label === (w + '.') || label === (w + '!')) {
        if (inBanner(el) || label.length < 25) {
          el.click();
          return {clicked: true, label: label, word: w};
        }
      }
    }
  }
  return {clicked: false};
})()
"""

CAPTCHA_DETECT_JS = r"""
(function() {
  const hits = [];
  if (document.querySelector('iframe[src*="recaptcha"], .g-recaptcha, #recaptcha, [data-sitekey][data-callback]')) hits.push('reCAPTCHA');
  if (document.querySelector('iframe[src*="hcaptcha"], .h-captcha')) hits.push('hCaptcha');
  if (document.querySelector('iframe[src*="challenges.cloudflare.com"], .cf-turnstile, #challenge-form')) hits.push('Cloudflare Turnstile');
  if (document.querySelector('iframe[src*="arkoselabs"], #FunCaptcha, [data-pkey]')) hits.push('Arkose/FunCaptcha');
  if (document.querySelector('iframe[src*="geetest"], .geetest_holder')) hits.push('GeeTest');
  const bodyText = (document.body && document.body.innerText || '').toLowerCase().slice(0, 4000);
  const phrases = ["i'm not a robot","verify you are human","verify you're human",
                   "checking your browser","just a moment","press and hold","prove you are human",
                   "are you a human","security check","unusual traffic"];
  for (const p of phrases) {
    if (bodyText.includes(p)) { hits.push('Text: "' + p + '"'); break; }
  }
  return { detected: hits.length > 0, kinds: hits, url: location.href, title: document.title };
})()
"""

DOM_SUMMARY_JS = r"""
(function(maxItems, visibleOnly) {
  const SEL = 'a, button, input, select, textarea, summary, ' +
              '[role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="radio"], ' +
              '[role="menuitem"], [role="option"], [role="treeitem"], [onclick], [contenteditable="true"]';
  function getCssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid').replace(/"/g,'\\"') + '"]';
    if (el.getAttribute('aria-label')) return el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label').replace(/"/g,'\\"') + '"]';
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    let path = [];
    let n = el;
    for (let i = 0; i < 6 && n && n.nodeType === 1; i++) {
      let part = n.tagName.toLowerCase();
      if (n.className && typeof n.className === 'string') {
        const cls = n.className.trim().split(/\s+/).slice(0,2).filter(c=>c).map(c=>'.'+CSS.escape(c)).join('');
        part += cls;
      }
      const sib = n.parentElement ? Array.from(n.parentElement.children).filter(c=>c.tagName===n.tagName) : [];
      if (sib.length > 1) part += ':nth-of-type(' + (sib.indexOf(n)+1) + ')';
      path.unshift(part);
      n = n.parentElement;
    }
    return path.join(' > ');
  }
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (out.length >= maxItems) break;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    if (visibleOnly) {
      if (r.bottom < 0 || r.top > window.innerHeight) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.05) continue;
    }
    const tag = el.tagName.toLowerCase();
    let label = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') ||
                 el.getAttribute('title') || el.getAttribute('alt') || '').trim().slice(0, 140);
    if (!label && tag === 'input') label = '[' + (el.type || 'text') + ' input]';
    if (!label && tag === 'a' && el.href) label = '[link to ' + el.href.slice(0, 80) + ']';
    if (!label) continue;
    const sig = tag + '|' + label;
    if (seen.has(sig)) continue;
    seen.add(sig);
    out.push({
      tag: tag,
      role: el.getAttribute('role') || '',
      type: el.type || '',
      text: label,
      href: el.href || '',
      selector: getCssPath(el),
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.top + r.height / 2),
      w: Math.round(r.width),
      h: Math.round(r.height),
      enabled: !el.disabled,
      checked: el.checked || false,
    });
  }
  return {
    url: location.href,
    title: document.title,
    scroll: { x: window.scrollX, y: window.scrollY, max_y: Math.max(0, document.documentElement.scrollHeight - window.innerHeight) },
    viewport: { w: window.innerWidth, h: window.innerHeight },
    element_count: out.length,
    elements: out,
  };
})
"""

CLICK_BY_TEXT_JS = r"""
(function(query, mode) {
  query = (query || '').toLowerCase().trim();
  if (!query) return {ok:false, error:'empty query'};
  const SEL = 'a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="tab"], [onclick]';
  const matches = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const txt = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase().trim();
    if (!txt) continue;
    let score = 0;
    if (txt === query) score = 100;
    else if (txt.startsWith(query)) score = 80;
    else if (txt.includes(query)) score = 60 - Math.min(40, Math.abs(txt.length - query.length));
    if (score > 0) matches.push({el, score, txt});
  }
  if (matches.length === 0) return {ok:false, error:'no match for "' + query + '"'};
  matches.sort((a,b) => b.score - a.score);
  const winner = matches[0].el;
  winner.scrollIntoView({behavior:'instant', block:'center'});
  if (mode === 'right') {
    const evt = new MouseEvent('contextmenu', {bubbles:true, cancelable:true, view:window, button:2});
    winner.dispatchEvent(evt);
  } else if (mode === 'double') {
    winner.click(); winner.click();
  } else {
    winner.click();
  }
  return {ok:true, matched: matches[0].txt, score: matches[0].score, count: matches.length};
})
"""


class BrowserController:
    def __init__(self, port: int = 9222):
        self.port = port
        self._ws = None
        self._tab_info: dict | None = None
        self._msg_id = 0
        self._lock = threading.Lock()
        self._chrome_proc = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_running(self) -> bool:
        try:
            requests.get(f"{self.base_url}/json/version", timeout=1.0)
            return True
        except Exception:
            return False

    def _find_browser(self) -> str | None:
        if sys.platform == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Arc.app/Contents/MacOS/Arc",
            ]
        else:
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]
        for p in candidates:
            if p and Path(p).exists():
                return p
        return None

    def _kill_existing_debug_browser(self):
        """Kill any Chrome/Edge instance that's using OUR profile dir or our debug port.
        Safe: it leaves the user's normal browser alone since it filters by command-line."""
        killed = 0
        try:
            import psutil
            profile_marker = str(PROFILE_DIR).lower()
            port_marker = f"--remote-debugging-port={self.port}"
            for p in psutil.process_iter(["name", "cmdline"]):
                try:
                    name = (p.info["name"] or "").lower()
                    if not ("chrome" in name or "msedge" in name or "edge" in name):
                        continue
                    cmd = " ".join(p.info["cmdline"] or []).lower()
                    if profile_marker in cmd or port_marker in cmd:
                        p.kill()
                        killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        if killed:
            time.sleep(0.6)
        return killed

    def launch(self, force_relaunch: bool = False) -> dict:
        if self.is_running() and not force_relaunch:
            return {"ok": True, "already_running": True}
        if force_relaunch:
            self._kill_existing_debug_browser()
        binary = self._find_browser()
        if not binary:
            return {"ok": False, "error": "Chrome/Edge not found in standard paths"}
        PROFILE_DIR.mkdir(exist_ok=True)
        args = [
            binary,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={PROFILE_DIR}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            "--new-window",
            "about:blank",
        ]
        try:
            self._chrome_proc = subprocess.Popen(args, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception as e:
            return {"ok": False, "error": f"launch failed: {e}"}
        for _ in range(40):
            if self.is_running():
                return {"ok": True, "launched": True, "binary": binary}
            time.sleep(0.25)
        return {"ok": False, "error": "browser did not open debug port in 10s"}

    def list_tabs(self) -> list[dict]:
        r = requests.get(f"{self.base_url}/json", timeout=2.0)
        return [t for t in r.json() if t.get("type") == "page"]

    def attach(self, tab_id: str | None = None) -> dict:
        if websocket is None:
            return {"ok": False, "error": "websocket-client not installed"}
        if not self.is_running():
            r = self.launch()
            if not r.get("ok"):
                return r
        tabs = self.list_tabs()
        if not tabs:
            try:
                resp = requests.put(f"{self.base_url}/json/new?about:blank", timeout=2.0)
                if resp.status_code == 200:
                    tabs = [resp.json()]
            except Exception:
                pass
            if not tabs:
                return {"ok": False, "error": "no tab available and could not create one"}
        if tab_id:
            tab = next((t for t in tabs if t.get("id") == tab_id), tabs[0])
        else:
            tab = tabs[0]
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        def _connect(target_tab):
            return websocket.create_connection(
                target_tab["webSocketDebuggerUrl"], timeout=10,
                origin="ember://localhost",
                suppress_origin=True,
            )

        try:
            self._ws = _connect(tab)
        except Exception as e:
            err_str = str(e)
            # 403 means the running Chrome was started WITHOUT --remote-allow-origins=*.
            # Kill the existing instance and relaunch with the correct flag.
            if "403" in err_str or "Forbidden" in err_str:
                self._kill_existing_debug_browser()
                r = self.launch(force_relaunch=True)
                if not r.get("ok"):
                    return {"ok": False, "error": f"relaunch after 403 failed: {r.get('error')}"}
                tabs = self.list_tabs()
                if not tabs:
                    try:
                        resp = requests.put(f"{self.base_url}/json/new?about:blank", timeout=2.0)
                        if resp.status_code == 200:
                            tabs = [resp.json()]
                    except Exception:
                        pass
                if not tabs:
                    return {"ok": False, "error": "no tab available after relaunch"}
                tab = tabs[0]
                try:
                    self._ws = _connect(tab)
                except Exception as e2:
                    return {"ok": False, "error": f"WebSocket still failed after relaunch: {str(e2)[:300]}"}
            else:
                return {"ok": False, "error": f"websocket connect failed: {err_str[:300]}"}
        self._tab_info = tab
        try:
            self.call("Runtime.enable")
            self.call("Page.enable")
        except Exception:
            pass
        return {"ok": True, "tab_id": tab["id"], "title": tab.get("title"), "url": tab.get("url")}

    def call(self, method: str, **params) -> dict:
        if self._ws is None:
            r = self.attach()
            if not r.get("ok"):
                raise CDPError(r.get("error") or "attach failed")
        with self._lock:
            self._msg_id += 1
            mid = self._msg_id
            msg = {"id": mid, "method": method, "params": params or {}}
            self._ws.send(json.dumps(msg))
            deadline = time.time() + 30
            while time.time() < deadline:
                raw = self._ws.recv()
                data = json.loads(raw)
                if data.get("id") == mid:
                    if "error" in data:
                        raise CDPError(data["error"].get("message", "CDP error"))
                    return data.get("result", {})
            raise CDPError(f"timeout waiting for {method}")

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        r = self.call("Runtime.evaluate", expression=expression,
                      returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise CDPError(str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")

    def navigate(self, url: str) -> dict:
        r = self.call("Page.navigate", url=url)
        return {"ok": True, "frame_id": r.get("frameId"), "loader_id": r.get("loaderId")}

    def wait_for_load(self, timeout: float = 8.0) -> dict:
        try:
            self.evaluate(
                "new Promise(r => { if (document.readyState === 'complete') r('complete');"
                "else window.addEventListener('load', () => r('complete'), {once:true}); "
                f"setTimeout(() => r('timeout'), {int(timeout * 1000)}); }})",
                await_promise=True,
            )
            return {"ok": True, "ready": True}
        except Exception as e:
            return {"ok": True, "ready": False, "note": str(e)[:200]}

    def get_dom_summary(self, max_items: int = 80, visible_only: bool = True) -> dict:
        expr = f"({DOM_SUMMARY_JS})({int(max_items)}, {str(bool(visible_only)).lower()})"
        try:
            val = self.evaluate(expr)
            return {"ok": True, **(val if isinstance(val, dict) else {"raw": val})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_by_text(self, query: str, mode: str = "left") -> dict:
        expr = f"({CLICK_BY_TEXT_JS})({json.dumps(query)}, {json.dumps(mode)})"
        try:
            return self.evaluate(expr) or {"ok": False, "error": "no result"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def click_selector(self, selector: str, mode: str = "left") -> dict:
        try:
            expr = (
                f"(function(){{const el=document.querySelector({json.dumps(selector)});"
                f"if(!el) return {{ok:false,error:'no element'}};"
                f"el.scrollIntoView({{behavior:'instant',block:'center'}});"
                f"const m={json.dumps(mode)};"
                f"if(m==='right'){{el.dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,view:window,button:2}}));}}"
                f"else if(m==='double'){{el.click();el.click();}}"
                f"else{{el.click();}}"
                f"return {{ok:true,selector:{json.dumps(selector)},tag:el.tagName.toLowerCase()}};}})()"
            )
            return self.evaluate(expr)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def fill(self, selector: str, value: str) -> dict:
        try:
            expr = (
                "(function(){"
                f"const el=document.querySelector({json.dumps(selector)});"
                "if(!el) return {ok:false,error:'no element'};"
                "el.focus();"
                f"const v={json.dumps(value)};"
                # contenteditable editors (Slate, ProseMirror, Notion-likes, etc.)
                "if(el.isContentEditable){el.textContent=v;"
                "el.dispatchEvent(new InputEvent('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));"
                "return {ok:true,len:v.length,mode:'contenteditable'};}"
                # Use the NATIVE value setter so React/Vue controlled inputs actually register the
                # change — a plain `el.value=v` is ignored by their value trackers.
                "const proto=el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;"
                "const d=Object.getOwnPropertyDescriptor(proto,'value');"
                "if(d&&d.set){d.set.call(el,v);}else{el.value=v;}"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));"
                "el.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true}));"
                "el.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true}));"
                "el.dispatchEvent(new Event('blur',{bubbles:true}));"
                "return {ok:true,len:(el.value!=null?String(el.value).length:v.length)};"
                "})()"
            )
            return self.evaluate(expr)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def scroll(self, direction: str = "down", pixels: int = 800) -> dict:
        dy = pixels * (1 if direction == "down" else -1)
        try:
            self.evaluate(f"window.scrollBy(0, {int(dy)})")
            return {"ok": True, "scrolled": dy}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def go_back(self) -> dict:
        try:
            self.evaluate("history.back()")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def go_forward(self) -> dict:
        try:
            self.evaluate("history.forward()")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reload(self) -> dict:
        try:
            self.call("Page.reload")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def screenshot(self) -> dict:
        try:
            r = self.call("Page.captureScreenshot", format="png")
            data = base64.b64decode(r.get("data", ""))
            return {
                "ok": True,
                "image_b64": r.get("data"),
                "mime_type": "image/png",
                "bytes": len(data),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def dismiss_cookies(self, mode: str = "accept") -> dict:
        try:
            self.evaluate(f"window._ember_cookie_pref={json.dumps(mode)}")
            res = self.evaluate(f"({COOKIE_DISMISS_JS})")
            return {"ok": True, **(res if isinstance(res, dict) else {"raw": res})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def check_captcha(self) -> dict:
        try:
            res = self.evaluate(f"({CAPTCHA_DETECT_JS})")
            return {"ok": True, **(res if isinstance(res, dict) else {"raw": res})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def new_tab(self, url: str = "about:blank") -> dict:
        try:
            r = requests.put(f"{self.base_url}/json/new?{url}", timeout=3)
            if r.status_code == 200:
                return self.attach(tab_id=r.json().get("id"))
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def switch_tab(self, tab_id: str) -> dict:
        return self.attach(tab_id=tab_id)

    def close_tab(self, tab_id: str | None = None) -> dict:
        tid = tab_id or (self._tab_info or {}).get("id")
        if not tid:
            return {"ok": False, "error": "no tab id"}
        try:
            requests.get(f"{self.base_url}/json/close/{tid}", timeout=2)
            if tid == (self._tab_info or {}).get("id"):
                self._ws = None
                self._tab_info = None
            return {"ok": True, "closed": tid}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_url(self) -> str:
        try:
            return str(self.evaluate("location.href") or "")
        except Exception:
            return ""

    def get_title(self) -> str:
        try:
            return str(self.evaluate("document.title") or "")
        except Exception:
            return ""


_browser: BrowserController | None = None


def get_browser() -> BrowserController:
    global _browser
    if _browser is None:
        _browser = BrowserController()
    return _browser
