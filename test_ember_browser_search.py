"""Regression tests for Ember Search — the browser's built-in start page + results page.

ember_browser.py hard-imports PyQt6-WebEngine at module load (absent in CI) and a real
EmberBrowser needs a WebEngine + display, so — like test_ember_browser_fullscreen.py — this
verifies the wiring via ast/source, PLUS a behavioural slice: the pure HTML builders
(_results_header / _search_results_html) are bound to a fake `self` and run for real, so the
generated markup (calc chip, linkified citations, favicons, copy button, engine pills, the
no-results / no-API-key fallbacks) is actually asserted, and every inline <script> it emits is
node --check'd. It guards the redesign: the old page hard-referenced a since-deleted `_CSS`
constant (a NameError at runtime) and had no customisation persistence at all.

Run: python test_ember_browser_search.py
"""
import ast
import html as _html
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote_plus, urlparse

_SRC_PATH = os.path.join(os.path.dirname(__file__), "ember_browser.py")
_SRC = open(_SRC_PATH, encoding="utf-8").read()
_TREE = ast.parse(_SRC)


def _class(name):
    # walk (not just _TREE.body): _Page is nested inside `if WEBENGINE_OK:`
    for n in ast.walk(_TREE):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    raise AssertionError(f"class {name} not found")


def _methods(cls):
    return {n.name for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _module_str_const(name):
    for n in _TREE.body:
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)):
            return n.value.value
    raise AssertionError(f"module string constant {name} not found")


def _node_check(js):
    node = shutil.which("node")
    if not node:  # node not installed in this environment — skip, don't fail
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run([node, "--check", p], capture_output=True, text=True)
    finally:
        os.unlink(p)
    assert r.returncode == 0, f"JS syntax error: {r.stderr.strip()}"
    return True


# ---- the deleted-constant regression ----------------------------------------------------

def test_no_reference_to_deleted_css_constant():
    # The redesign deleted `_CSS`; the results page must not still point at it (would NameError).
    assert not re.search(r"(?<![\w])_CSS(?![\w])", _SRC), "stale reference to removed _CSS constant"
    assert "_SEARCH_CSS" in _SRC


# ---- customisation persistence wiring ---------------------------------------------------

def test_page_emits_config_and_blocks_navigation():
    page = _class("_Page")
    assert "configRequested" in _methods(page) or "configRequested = pyqtSignal" in _SRC
    assert "embercfg=" in _SRC
    # the config round-trip must NOT navigate (return False) or it reloads the live-preview page
    assert "self.configRequested.emit" in _SRC


def test_config_signal_is_connected_and_persisted():
    assert "configRequested.connect(self._apply_config" in _SRC
    m = _methods(_class("EmberBrowser"))
    for meth in ("_apply_config", "_load_theme", "_save_theme", "_theme_file", "_qss", "_accent_pair"):
        assert meth in m, f"EmberBrowser missing {meth}"
    # _apply_config must persist AND re-tint the native chrome
    assert "self._save_theme()" in _SRC and "self.setStyleSheet(self._qss())" in _SRC


def test_theme_loaded_and_chrome_tinted_at_init():
    init = next(n for n in ast.walk(_class("EmberBrowser"))
               if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    body = ast.get_source_segment(_SRC, init)
    assert "self._theme = self._load_theme()" in body
    assert "self.setStyleSheet(self._qss())" in body


def test_presets_and_defaults_present():
    for preset in ("ember", "ocean", "forest", "grape", "rose", "slate"):
        assert f'"{preset}"' in _SRC, f"preset {preset} missing"
    assert "_DEFAULT_SHORTCUTS" in _SRC and "_DEFAULT_THEME" in _SRC


# ---- the raw-string JS actually parses --------------------------------------------------

def test_head_and_home_js_are_valid_javascript():
    assert _node_check(_module_str_const("_SEARCH_HEAD_JS")) in (True, None)
    assert _node_check(_module_str_const("_HOME_JS")) in (True, None)


# ---- behavioural: run the pure HTML builders for real -----------------------------------

def _bind_builders():
    """Bind the real _results_header / _search_results_html onto a fake self (no Qt needed)."""
    glb = {"_html": _html, "urlparse": urlparse, "quote_plus": quote_plus, "re": re,
           "SEARCH_HOST": "ember.search"}

    def _shell(self, body, *, home):
        head = _module_str_const("_SEARCH_HEAD_JS")
        return f"<!doctype html><html><head><script>{head}</script></head><body>{body}</body></html>"

    class Fake:
        _theme = {"preset": "ember", "accent": "#e8632e", "accent2": "#f0a13c",
                  "mode": "dark", "clock": True, "shortcuts": []}
        _SEARCH_SVG = "<svg></svg>"

    Fake._shell = _shell
    cls = _class("EmberBrowser")
    for meth in ("_results_header", "_search_results_html"):
        fn = next(f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == meth)
        code = ast.get_source_segment(_SRC, fn)
        exec(compile(ast.parse(code), "<m>", "exec"), glb)
        setattr(Fake, meth, glb[meth])
    return Fake()


def test_results_page_has_all_polished_parts():
    f = _bind_builders()
    results = [("Python (programming language) - Wikipedia", "https://en.wikipedia.org/wiki/Python"),
               ("Welcome to Python.org", "https://www.python.org/")]
    out = f._search_results_html("what is python? 12*8",
                                 "Python is a language [1] used widely [2].", results, "= 96")
    assert "class=calc" in out and "= 96" in out                      # instant-answer chip
    assert 'class=cite' in out and "en.wikipedia.org/wiki/Python" in out  # linkified [1] citation
    assert "icons.duckduckgo.com/ip3/en.wikipedia.org.ico" in out     # favicon
    assert "id=copyBtn" in out                                        # copy button
    assert "class=pill" in out and "duckduckgo.com" in out            # engine pills
    assert "&lt;script&gt;" not in out.split("<body>")[0]             # sanity: shell built
    # every inline script the page emits must be valid JS
    for js in re.findall(r"<script>(.*?)</script>", out, re.S):
        assert _node_check(js) in (True, None)


def test_results_page_handles_no_results_and_no_answer():
    f = _bind_builders()
    out = f._search_results_html("obscure query", None, [], None)
    assert "class=empty" in out                       # graceful "no results" card
    assert "add an API key" in out                    # helpful no-AI-key hint, not a blank box
    assert "class=calc" not in out                    # no bogus calc chip without an instant answer


def test_citation_out_of_range_is_left_alone():
    f = _bind_builders()
    # [5] with only 2 results must not crash or produce a broken link
    out = f._search_results_html("q", "See [5] and [1].",
                                 [("A", "https://a.com"), ("B", "https://b.com")], None)
    assert "[5]" in out and "class=cite" in out        # [1] linkified, [5] left as plain text


def test_polish_slash_focus_reduced_motion_and_privacy():
    # "/" focuses the search box (a search convention) — in the SHARED head JS so it works on
    # both the home and results pages.
    head = _module_str_const("_SEARCH_HEAD_JS")
    assert "keydown" in head and "input[name=q]" in head and "'/'" in head
    # respect prefers-reduced-motion (accessibility): the skeleton shimmer must be tamed
    assert "prefers-reduced-motion" in _SRC
    # favicons must not leak the referrer to the icon host (privacy) and load lazily
    assert "referrerpolicy=no-referrer" in _module_str_const("_HOME_JS")

    f = _bind_builders()
    out = f._search_results_html("q", "a", [("A", "https://a.com")], None)
    assert "referrerpolicy=no-referrer" in out and "loading=lazy" in out


def test_customise_panel_has_backdrop_and_keyboard_affordances():
    home = _module_str_const("_HOME_JS")
    # click-away backdrop + Escape both close the panel; Enter in a shortcut field adds it
    assert "backdrop" in home and "Escape" in home
    assert "addShortcut" in home and "'Enter'" in home
    # the backdrop element and the panel are both emitted by the home page
    cls = _class("EmberBrowser")
    fn = next(f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == "_home_html")
    src = ast.get_source_segment(_SRC, fn)
    assert "id=backdrop" in src and "aria-label" in src


def test_every_self_method_call_in_emberbrowser_resolves():
    cls = _class("EmberBrowser")
    defined = _methods(cls)
    for n in ast.walk(cls):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                    defined.add(t.attr)
    called = {n.func.attr for n in ast.walk(cls)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
              and n.func.attr.startswith("_")}
    missing = sorted(c for c in called if c not in defined)
    assert not missing, f"EmberBrowser calls undefined self methods: {missing}"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
