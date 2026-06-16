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

Plus: tabs, bookmarks, find-in-page (Ctrl+F), zoom (Ctrl+ +/-), Ctrl+T/W/L.

QtWebEngine is optional (PyQt6-WebEngine). If unavailable, WEBENGINE_OK is False and
the caller shows the import error in WEBENGINE_ERROR.
"""
from __future__ import annotations

import html as _html
import json
import threading
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QTabWidget, QLabel, QTextBrowser, QSplitter, QSizePolicy, QMenu)

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

_CSS = """
  body{margin:0;background:#0e0f13;color:#e9eaf0;font:15px -apple-system,Segoe UI,sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:48px 24px}
  .logo{font-size:40px;font-weight:800;text-align:center;
        background:linear-gradient(90deg,#f0a13c,#e2562a);-webkit-background-clip:text;
        -webkit-text-fill-color:transparent;margin:10vh 0 22px}
  form{display:flex;gap:8px}
  input{flex:1;padding:14px 18px;border-radius:26px;border:1px solid #2a2d39;background:#181a22;
        color:#fff;font-size:16px;outline:none}
  button{padding:0 22px;border-radius:26px;border:none;background:#e2562a;color:#fff;
         font-weight:700;cursor:pointer}
  .ans{background:#181a22;border:1px solid #2a2d39;border-radius:14px;padding:18px 20px;margin:18px 0}
  .ans h3{margin:0 0 8px;color:#f0a13c;font-size:13px;text-transform:uppercase;letter-spacing:.5px}
  .res{margin:14px 0}
  .res a{color:#7aa2ff;font-size:17px;text-decoration:none}
  .res a:hover{text-decoration:underline}
  .res .u{color:#5b8a4f;font-size:12px}
  .hint{color:#8a8f98;text-align:center;font-size:13px;margin-top:14px}
"""

if WEBENGINE_OK:
    class _Guard(QWebEngineUrlRequestInterceptor):
        def __init__(self):
            super().__init__()
            self.blocked = 0

        def interceptRequest(self, info):
            try:
                host = (info.requestUrl().host() or "").lower()
                if host:
                    for d in _TRACKERS:
                        if host == d or host.endswith("." + d):
                            info.block(True)
                            self.blocked += 1
                            return
            except Exception:
                pass

    class _Page(QWebEnginePage):
        """Page that intercepts Ember Search submissions instead of navigating to them."""
        searchRequested = pyqtSignal(str)

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            s = url.toString()
            if SEARCH_HOST in s and ("?q=" in s or "&q=" in s):
                q = parse_qs(urlparse(s).query).get("q", [""])[0]
                self.searchRequested.emit(q)
                return False
            return super().acceptNavigationRequest(url, nav_type, is_main_frame)


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


class EmberBrowser(QWidget):
    _ai_result = pyqtSignal(str)
    _search_result = pyqtSignal(str, str)

    def __init__(self, settings: dict | None = None):
        super().__init__()
        self.settings = settings or {}
        self.setWindowTitle("Ember Browser")
        self.resize(1180, 800)
        self.setMinimumSize(640, 480)
        self._ai_result.connect(self._show_ai_result)
        self._search_result.connect(self._load_search_results)
        self._bookmarks = self._load_bookmarks()

        self._profile = QWebEngineProfile(self)
        self._guard = _Guard()
        try:
            self._profile.setUrlRequestInterceptor(self._guard)
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)

        def _btn(text, tip, fn, w=34):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(w)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            return b

        bar.addWidget(_btn("←", "Back", lambda: self._cur() and self._cur().back()))
        bar.addWidget(_btn("→", "Forward", lambda: self._cur() and self._cur().forward()))
        bar.addWidget(_btn("⟳", "Reload", lambda: self._cur() and self._cur().reload()))
        bar.addWidget(_btn("⌂", "Ember Search home", lambda: self._go_home()))
        self._lock = QLabel("🔒")
        bar.addWidget(self._lock)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Search Ember, enter a URL, or ask a question (end with ?)…")
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self._navigate_from_bar)
        self.address.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar.addWidget(self.address, 1)
        bar.addWidget(_btn("★", "Bookmark this page", self._bookmark_current))
        bar.addWidget(_btn("📑", "Bookmarks", self._show_bookmarks_menu))
        bar.addWidget(_btn("🔎", "Find on page (Ctrl+F)", self._toggle_find))
        bar.addWidget(_btn("✓AI", "Check if the page text is AI-generated", self._ai_check_page, w=50))
        bar.addWidget(_btn("+", "New tab", lambda: self._new_tab()))
        bar.addWidget(_btn("✨", "AI panel", self._toggle_ai))
        outer.addLayout(bar)

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
        self._split.addWidget(self.tabs)
        self._ai_panel = self._build_ai_panel()
        self._ai_panel.setVisible(False)
        self._split.addWidget(self._ai_panel)
        self._split.setStretchFactor(0, 1)
        outer.addWidget(self._split, 1)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#8a8f98; font-size:11px; padding:2px 10px;")
        outer.addWidget(self._status)

        for seq, fn in (("Ctrl+T", lambda: self._new_tab()),
                        ("Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex())),
                        ("Ctrl+L", lambda: (self.address.setFocus(), self.address.selectAll())),
                        ("Ctrl+F", self._toggle_find),
                        ("Ctrl+=", lambda: self._zoom(0.1)), ("Ctrl++", lambda: self._zoom(0.1)),
                        ("Ctrl+-", lambda: self._zoom(-0.1)), ("Ctrl+0", lambda: self._zoom(0))):
            QShortcut(QKeySequence(seq), self, activated=fn)

        self._new_tab()  # opens the Ember Search start page

    # ---- tabs ----
    def _new_tab(self, url: str | None = None):
        view = QWebEngineView()
        page = _Page(self._profile, view)
        page.searchRequested.connect(self._ember_search)
        view.setPage(page)
        s = view.settings()
        try:
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        except Exception:
            pass
        view.urlChanged.connect(lambda u, v=view: self._on_url_changed(v, u))
        view.titleChanged.connect(lambda t, v=view: self._on_title(v, t))
        view.loadFinished.connect(lambda ok: self._refresh_status())
        idx = self.tabs.addTab(view, "New tab")
        self.tabs.setCurrentIndex(idx)
        if url:
            self._navigate(url, view)
        else:
            view.setHtml(self._home_html(), QUrl(f"https://{SEARCH_HOST}/"))
        return view

    def _close_tab(self, index: int):
        if index < 0:
            return
        w = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if w is not None:
            w.deleteLater()
        if self.tabs.count() == 0:
            self._new_tab()

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
            self._ai_panel.setVisible(True)
            self._ask_ai(q)
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
        self._lock.setText("🔒" if secure else "⚠")
        self._lock.setToolTip("Secure (HTTPS)" if secure else "Not secure")

    def _on_title(self, view, title):
        i = self.tabs.indexOf(view)
        if i >= 0:
            self.tabs.setTabText(i, (title or "New tab")[:24])

    def _refresh_status(self):
        self._status.setText(f"🛡 {self._guard.blocked} trackers blocked this session"
                             f"   ·   {len(self._bookmarks)} bookmarks")

    # ---- Ember Search ----
    def _home_html(self) -> str:
        return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head>"
                f"<body><div class='wrap'><div class='logo'>Ember Search</div>"
                f"<form action='https://{SEARCH_HOST}/' method='get'>"
                f"<input name='q' autofocus placeholder='Search the web with AI…'>"
                f"<button type='submit'>Search</button></form>"
                f"<div class='hint'>AI answer + private web results · trackers blocked</div>"
                f"</div></body></html>")

    def _ember_search(self, query: str):
        query = (query or "").strip()
        if not query:
            return
        self.address.setText(query)
        v = self._cur() or self._new_tab()
        v.setHtml(f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head>"
                  f"<body><div class='wrap'><div class='logo'>Ember Search</div>"
                  f"<div class='ans'><h3>Searching…</h3>“{_html.escape(query)}”</div></div></body></html>",
                  QUrl(f"https://{SEARCH_HOST}/"))
        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query: str):
        answer = self._model_text(
            "Answer this search query concisely and factually in 2-4 sentences. "
            "If it needs current data you may not have, say so briefly.\n\nQuery: " + query)
        results = _ddg(query)
        self._search_result.emit(query, self._search_results_html(query, answer, results))

    def _search_results_html(self, query, answer, results):
        rows = ""
        for title, href in results:
            dom = urlparse(href).netloc
            rows += (f"<div class='res'><a href='{_html.escape(href)}'>{_html.escape(title)}</a>"
                     f"<div class='u'>{_html.escape(dom)}</div></div>")
        if not rows:
            rows = ("<div class='hint'>No web results fetched. "
                    f"<a href='https://duckduckgo.com/?q={quote_plus(query)}'>Open DuckDuckGo</a></div>")
        ans = _html.escape(answer or "(no AI answer — add an API key in Ember Settings)").replace("\n", "<br>")
        return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS}</style></head>"
                f"<body><div class='wrap'>"
                f"<form action='https://{SEARCH_HOST}/' method='get' style='margin-bottom:8px'>"
                f"<input name='q' value=\"{_html.escape(query)}\"><button type='submit'>Search</button></form>"
                f"<div class='ans'><h3>✨ Ember AI answer</h3>{ans}</div>{rows}</div></body></html>")

    def _load_search_results(self, query, html):
        v = self._cur()
        if v is not None:
            v.setHtml(html, QUrl(f"https://{SEARCH_HOST}/"))

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
        row.addWidget(sb)
        row.addWidget(cb)
        lay.addLayout(row)
        self._ai_out = QTextBrowser()
        self._ai_out.setOpenExternalLinks(True)
        lay.addWidget(self._ai_out, 1)
        self._ai_in = QLineEdit()
        self._ai_in.setPlaceholderText("Ask about this page…")
        self._ai_in.returnPressed.connect(lambda: self._ask_ai(self._ai_in.text().strip()))
        lay.addWidget(self._ai_in)
        return panel

    def _toggle_ai(self):
        self._ai_panel.setVisible(not self._ai_panel.isVisible())

    def _ask_ai(self, question: str):
        if not question:
            return
        self._ai_panel.setVisible(True)
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
        self._ai_panel.setVisible(True)
        v = self._cur()
        if v is None:
            return
        self._ai_out.append("<b>AI check:</b> analyzing page text…")

        def got(text):
            try:
                import ai_detect
                r = ai_detect.detect_text(text or "")
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            if r.get("ok"):
                self._ai_result.emit(f"🔎 AI-content check: <b>{r['verdict']}</b> "
                                     f"({r['ai_likelihood']}% AI-likelihood). {r.get('note','')}")
            else:
                self._ai_result.emit(f"AI check: {r.get('error', 'could not analyze')}")
        v.page().toPlainText(got)

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
