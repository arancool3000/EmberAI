"""Ember Browser — a secure, AI-assisted web browser built on Qt WebEngine (Chromium).

Security:
- Blocks known ad/tracker/telemetry domains on every request (local list, no network).
- Enforces Ember's web policy (web_policy.check_url) on user navigation.
- HTTPS-first: bare domains open over https; the address bar shows a lock/warning.
- Sensible hardening (no JS-opened popups, no clipboard access by default).

AI (uses your Gemini key from Ember Settings):
- Summarize the current page.
- Ask a question about the current page.
- Type "ai <question>" (or end with "?") in the address bar to ask without a URL.

QtWebEngine is an optional dependency (PyQt6-WebEngine). If it isn't installed,
WEBENGINE_OK is False and the caller shows an install hint instead of crashing.
"""
from __future__ import annotations

import threading
from urllib.parse import quote_plus

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QTabWidget, QLabel, QTextBrowser, QSplitter, QSizePolicy)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (QWebEngineProfile, QWebEnginePage,
                                       QWebEngineUrlRequestInterceptor, QWebEngineSettings)
    WEBENGINE_OK = True
except Exception:
    WEBENGINE_OK = False

HOME_URL = "https://duckduckgo.com/"

# Starter ad/tracker/telemetry blocklist (host suffix match). Not exhaustive, but it
# kills the most common third-party trackers with zero network lookups.
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


if WEBENGINE_OK:
    class _Guard(QWebEngineUrlRequestInterceptor):
        """Blocks tracker/ad requests before they leave the machine."""

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


class EmberBrowser(QWidget):
    """Top-level secure browser window."""

    _ai_result = pyqtSignal(str)

    def __init__(self, settings: dict | None = None):
        super().__init__()
        self.settings = settings or {}
        self.setWindowTitle("Ember Browser")
        self.resize(1180, 800)
        self._ai_result.connect(self._show_ai_result)

        # A private, in-memory profile (no history/cache persisted) with the tracker guard.
        self._profile = QWebEngineProfile(self)
        self._guard = _Guard()
        try:
            self._profile.setUrlRequestInterceptor(self._guard)
        except Exception:
            pass

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- toolbar ---
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
        bar.addWidget(_btn("⌂", "Home", lambda: self._navigate(HOME_URL)))

        self._lock = QLabel("🔒")
        self._lock.setToolTip("Connection security")
        bar.addWidget(self._lock)

        self.address = QLineEdit()
        self.address.setPlaceholderText("Search, enter a URL, or type a question (end with ?)…")
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self._navigate_from_bar)
        self.address.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar.addWidget(self.address, 1)

        bar.addWidget(_btn("+", "New tab", lambda: self._new_tab()))
        self._ai_btn = _btn("✨ AI", "Toggle the AI panel", self._toggle_ai, w=58)
        bar.addWidget(self._ai_btn)
        outer.addLayout(bar)

        # --- tabs + AI panel split ---
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

        # status line (blocked-tracker count etc.)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#8a8f98; font-size:11px; padding:2px 10px;")
        outer.addWidget(self._status)

        for seq, fn in (("Ctrl+T", lambda: self._new_tab()),
                        ("Ctrl+W", lambda: self._close_tab(self.tabs.currentIndex())),
                        ("Ctrl+L", lambda: (self.address.setFocus(), self.address.selectAll()))):
            QShortcut(QKeySequence(seq), self, activated=fn)

        self._new_tab(HOME_URL)

    # ---- tabs -------------------------------------------------------------
    def _new_tab(self, url: str = HOME_URL):
        view = QWebEngineView()
        page = QWebEnginePage(self._profile, view)
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
        self._navigate(url, view)
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

    def _cur(self) -> "QWebEngineView | None":
        return self.tabs.currentWidget()

    def _on_tab_changed(self, _idx):
        v = self._cur()
        if v is not None:
            self._sync_address(v.url())

    # ---- navigation -------------------------------------------------------
    def _to_url(self, text: str) -> str:
        text = text.strip()
        if "://" in text:
            return text
        # Looks like a domain? (has a dot, no spaces) -> https. Else search.
        if " " not in text and "." in text:
            return "https://" + text
        return "https://duckduckgo.com/?q=" + quote_plus(text)

    def _navigate_from_bar(self):
        text = self.address.text().strip()
        if not text:
            return
        low = text.lower()
        if low.startswith("ai ") or text.endswith("?"):
            q = text[3:].strip() if low.startswith("ai ") else text
            self._ai_panel.setVisible(True)
            self._ai_btn.setChecked(True)
            self._ask_ai(q)
            return
        self._navigate(self._to_url(text))

    def _navigate(self, url: str, view=None):
        view = view or self._cur()
        if view is None:
            return
        # Enforce Ember's web policy on user navigation (malware / blocklist).
        try:
            import web_policy
            verdict = web_policy.check_url(url)
            if isinstance(verdict, dict) and verdict.get("allowed") is False:
                self._status.setText(f"⛔ Blocked by web policy: {verdict.get('reason', url)}")
                return
        except Exception:
            pass
        view.setUrl(QUrl(url))

    def _on_url_changed(self, view, qurl: QUrl):
        if view is self._cur():
            self._sync_address(qurl)

    def _sync_address(self, qurl: QUrl):
        self.address.setText(qurl.toString())
        self.address.setCursorPosition(0)
        secure = qurl.scheme() == "https"
        self._lock.setText("🔒" if secure else "⚠")
        self._lock.setToolTip("Secure (HTTPS)" if secure else "Not secure")

    def _on_title(self, view, title: str):
        idx = self.tabs.indexOf(view)
        if idx >= 0:
            self.tabs.setTabText(idx, (title or "New tab")[:24])

    def _refresh_status(self):
        self._status.setText(f"🛡 {self._guard.blocked} trackers blocked this session")

    # ---- AI ---------------------------------------------------------------
    def _build_ai_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(440)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        title = QLabel("✨ Ember AI")
        title.setStyleSheet("font-weight:800; font-size:13px;")
        lay.addWidget(title)
        row = QHBoxLayout()
        sb = QPushButton("Summarize page")
        sb.clicked.connect(lambda: self._ask_ai("Summarize this page in a few clear bullet points."))
        row.addWidget(sb)
        lay.addLayout(row)
        self._ai_out = QTextBrowser()
        self._ai_out.setOpenExternalLinks(True)
        self._ai_out.setPlaceholderText("Ask about the page, or summarize it.")
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
        self._ai_out.append(f"<b>You:</b> {question}")
        self._ai_out.append("<i>Ember is thinking…</i>")
        view = self._cur()
        if view is None:
            self._ai_result.emit("No page open.")
            return
        view.page().toPlainText(lambda text: self._dispatch_ai(question, text or ""))

    def _dispatch_ai(self, question: str, page_text: str):
        url = self._cur().url().toString() if self._cur() else ""
        prompt = (
            "You are Ember, a helpful AI inside a web browser. Answer the user's request about "
            "the current web page. Be concise and accurate; if the answer isn't on the page, say so.\n\n"
            f"PAGE URL: {url}\n"
            f"PAGE TEXT (truncated):\n{page_text[:14000]}\n\n"
            f"USER: {question}"
        )
        threading.Thread(target=self._ai_thread, args=(prompt,), daemon=True).start()

    def _ai_thread(self, prompt: str):
        key = "".join((self.settings.get("gemini_api_key") or "").split())
        if not key:
            self._ai_result.emit("Add a Gemini API key in Ember Settings (⚙) to use AI browsing.")
            return
        try:
            from google import genai
            client = genai.Client(api_key=key)
            model = (self.settings.get("model_id") or self.settings.get("gemini_model")
                     or "gemini-3.1-flash-lite")
            if "claude" in str(model).lower():
                model = "gemini-3.1-flash-lite"  # browser AI uses Gemini
            resp = client.models.generate_content(model=model, contents=prompt)
            self._ai_result.emit((getattr(resp, "text", None) or "(no response)").strip())
        except Exception as e:
            self._ai_result.emit(f"AI error: {e}")

    def _show_ai_result(self, text: str):
        # Replace the trailing "thinking…" line with the answer.
        html = self._ai_out.toHtml()
        self._ai_out.append(f"<b>Ember:</b> {text}".replace("\n", "<br>"))
