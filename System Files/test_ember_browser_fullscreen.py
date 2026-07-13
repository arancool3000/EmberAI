"""Regression tests for Ember Browser's fullscreen support.

ember_browser.py hard-imports PyQt6-WebEngine at module load (absent in CI), and constructing an
EmberBrowser needs a real WebEngine + display — so this verifies the wiring via ast/source, the
same approach test_ember_browser_adblock.py uses. It guards the exact gap that made "fullscreen
doesn't work": the engine attribute was never enabled and the page's fullscreen request was
never accepted, so clicking fullscreen on a video did nothing.
Run: python test_ember_browser_fullscreen.py
"""
import ast
import os

_SRC_PATH = os.path.join(os.path.dirname(__file__), "ember_browser.py")
_SRC = open(_SRC_PATH, encoding="utf-8").read()
_TREE = ast.parse(_SRC)


def _class(name):
    for n in _TREE.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    raise AssertionError(f"class {name} not found")


def _methods(cls):
    return {n.name for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_fullscreen_support_attribute_is_enabled():
    # Without FullScreenSupportEnabled the engine refuses HTML5 fullscreen entirely.
    assert "FullScreenSupportEnabled, True" in _SRC


def test_page_fullscreen_request_is_accepted_and_handled():
    # QtWebEngine won't enter fullscreen on its own; the request must be accepted AND acted on.
    assert "fullScreenRequested.connect" in _SRC
    m = _methods(_class("EmberBrowser"))
    assert "_on_fullscreen_requested" in m
    assert "_enter_page_fullscreen" in m and "_exit_page_fullscreen" in m
    # the handler must actually accept() the request and make the window fullscreen
    assert "request.accept()" in _SRC and "showFullScreen()" in _SRC


def test_has_window_fullscreen_toggle_and_escape_exit():
    m = _methods(_class("EmberBrowser"))
    assert "_toggle_window_fullscreen" in m
    assert "_exit_any_fullscreen" in m
    assert '"F11"' in _SRC and '"Escape"' in _SRC


def test_toolbar_is_wrapped_so_it_can_be_hidden_in_fullscreen():
    # The chrome must be a hideable widget (not a bare layout) or a fullscreen video can't fill
    # the screen.
    assert "self._chrome = QWidget()" in _SRC
    assert "outer.addWidget(self._chrome)" in _SRC


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
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
