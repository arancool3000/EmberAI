"""Regression tests for Ember Browser opening links in new tabs.

ember_browser.py hard-imports PyQt6-WebEngine at load and a real browser needs a WebEngine +
display, so (like test_ember_browser_fullscreen.py) this verifies the wiring via ast/source.
It guards the exact bug "hyperlinks don't open in a new tab or open at all": QtWebEngine routes
target="_blank" links, window.open(), and ctrl/middle-click through QWebEnginePage.createWindow,
and the default implementation returns None — so with no override those links are silently
dropped and nothing happens.

Run: python test_ember_browser_newtab.py
"""
import ast
import os

_SRC_PATH = os.path.join(os.path.dirname(__file__), "ember_browser.py")
_SRC = open(_SRC_PATH, encoding="utf-8").read()
_TREE = ast.parse(_SRC)


def _class(name):
    for n in ast.walk(_TREE):          # walk: _Page is nested inside `if WEBENGINE_OK:`
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    raise AssertionError(f"class {name} not found")


def _methods(cls):
    return {n.name for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _method_src(cls, name):
    fn = next(f for f in ast.walk(cls) if isinstance(f, ast.FunctionDef) and f.name == name)
    return ast.get_source_segment(_SRC, fn)


def test_page_overrides_createwindow():
    # The whole fix: without this override, new-tab links do nothing.
    assert "createWindow" in _methods(_class("_Page")), "_Page must override createWindow"
    src = _method_src(_class("_Page"), "createWindow")
    assert "_new_tab_page" in src            # routes to a real new tab
    assert "_browser" in src                 # via the page's back-reference to the browser


def test_browser_has_new_tab_page_and_backref():
    win = _class("EmberBrowser")
    assert "_new_tab_page" in _methods(win)
    newtab = _method_src(win, "_new_tab")
    assert "page._browser = self" in newtab   # so createWindow can reach the browser
    # blank tabs (for createWindow) must NOT load the home page over the incoming URL
    assert "blank" in newtab and "elif not blank" in newtab


def test_js_can_open_windows_is_enabled():
    # window.open() must be allowed (routed to a tab via createWindow); previously it was False,
    # so JS-opened links did nothing.
    assert "JavascriptCanOpenWindows, True" in _SRC
    assert "JavascriptCanOpenWindows, False" not in _SRC


def test_new_tab_page_returns_a_page_not_a_view():
    src = _method_src(_class("EmberBrowser"), "_new_tab_page")
    assert ".page()" in src                   # createWindow needs the QWebEnginePage, not the view
    assert "blank=True" in src                 # opened empty for the engine to fill


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
