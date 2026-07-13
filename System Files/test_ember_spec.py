"""Static guard against a recurring PyInstaller packaging bug: a module gets listed in
Ember.spec's `hiddenimports` (because Ember needs it bundled) while ALSO staying in the
`excludes` list (because someone added it there as "unused" without checking) - that
contradiction is exactly how PyQt6.QtWebChannel ended up excluded even though
QtWebEngineCore depends on it internally, breaking Ember Browser at runtime with
"ModuleNotFoundError: No module named 'PyQt6.QtWebChannel'".

Parses Ember.spec with ast (no PyInstaller needed - the file isn't importable directly since it
uses PyInstaller-injected globals like Analysis/PYZ/EXE).
Run: python test_ember_spec.py"""
import ast
import os

_SPEC = os.path.join(os.path.dirname(__file__), "Ember.spec")


def _tree():
    return ast.parse(open(_SPEC, encoding="utf-8").read())


def _string_list_literal(node):
    """Extract string constants from an ast.List node; ignores non-string elements (e.g. calls)."""
    return {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}


def _hiddenimports_literals():
    """Every string literal added to `hiddenimports` via `hiddenimports = [...]` or
    `hiddenimports += [...]` anywhere in the file (collect_all()/collect_submodules() results
    aren't literals and are skipped - this only covers the explicit hand-written entries)."""
    names = set()
    for node in ast.walk(_tree()):
        target = None
        value = None
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "hiddenimports" and isinstance(node.value, ast.List):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "hiddenimports" \
                and isinstance(node.value, ast.List):
            target, value = node.targets[0], node.value
        if value is not None:
            names |= _string_list_literal(value)
    return names


def _excludes_literal():
    """The `excludes=[...]` keyword argument of the Analysis(...) call."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis":
            for kw in node.keywords:
                if kw.arg == "excludes" and isinstance(kw.value, ast.List):
                    return _string_list_literal(kw.value)
    raise AssertionError("Analysis(...) call with an excludes=[...] kwarg not found in Ember.spec")


def test_no_module_is_both_a_required_hidden_import_and_excluded():
    hidden = _hiddenimports_literals()
    excluded = _excludes_literal()
    overlap = sorted(hidden & excluded)
    assert not overlap, (
        f"Ember.spec lists {overlap} in BOTH hiddenimports and excludes - PyInstaller will drop "
        "the compiled module, breaking anything that needs it at runtime with a "
        "ModuleNotFoundError even though Ember explicitly asked for it to be bundled.")


def test_qtwebchannel_is_bundled_not_excluded():
    # The concrete regression this file exists for: QtWebEngineCore needs QtWebChannel
    # internally even though no Ember source file imports it directly.
    assert "PyQt6.QtWebChannel" in _hiddenimports_literals()
    assert "PyQt6.QtWebChannel" not in _excludes_literal()


def test_webengine_modules_are_hidden_imports():
    hidden = _hiddenimports_literals()
    assert {"PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore"} <= hidden


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
