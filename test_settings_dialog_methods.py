"""Static guard against a recurring bug: a SettingsDialog handler calling a method that only
exists on EmberWindow (e.g. self._set_status / self._toggle_offline_mode), which blows up at
runtime with "'SettingsDialog' object has no attribute ...". Parses ui.py with ast (no PyQt6
needed) and asserts every self._private(...) call in SettingsDialog is defined on the class.
Run: python test_settings_dialog_methods.py"""
import ast
import os

_UI = os.path.join(os.path.dirname(__file__), "ui.py")


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in ui.py")


def _defined_names(cls):
    """Method names + self._attr = ... assignment targets defined on the class."""
    names = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    names.add(tgt.attr)
    return names


def _self_private_calls(cls):
    """Attr names X for every `self._X(...)` call inside the class."""
    calls = set()
    for node in ast.walk(cls):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
                and node.func.attr.startswith("_")):
            calls.add(node.func.attr)
    return calls


def _assert_self_calls_defined(cls_name: str):
    tree = ast.parse(open(_UI, encoding="utf-8").read())
    cls = _class_node(tree, cls_name)
    defined = _defined_names(cls)
    called = _self_private_calls(cls)
    missing = sorted(c for c in called if c not in defined)
    assert not missing, (
        f"{cls_name} calls self._<name>() that it doesn't define (crashes at runtime with "
        f"AttributeError): {missing}. Define them on {cls_name} or forward to the parent window.")


def test_settings_dialog_calls_are_defined():
    _assert_self_calls_defined("SettingsDialog")


def test_terminal_dialog_calls_are_defined():
    _assert_self_calls_defined("TerminalDialog")


def test_agents_dialog_calls_are_defined():
    _assert_self_calls_defined("AgentsDialog")


def test_remote_link_dialog_calls_are_defined():
    _assert_self_calls_defined("RemoteLinkDialog")


def test_features_dialog_calls_are_defined():
    _assert_self_calls_defined("FeaturesDialog")


def test_features_dialog_do_surfaces_errors_instead_of_swallowing_them():
    """Regression guard: FeaturesDialog._do() used to `except Exception: pass`, so a broken
    feature just closed the directory and did nothing else - indistinguishable from a dead
    button. Every except-handler in _do must actually do something observable (not bare pass)."""
    tree = ast.parse(open(_UI, encoding="utf-8").read())
    cls = _class_node(tree, "FeaturesDialog")
    do_fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_do")
    for node in ast.walk(do_fn):
        if isinstance(node, ast.ExceptHandler):
            assert not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)), (
                "FeaturesDialog._do() silently swallows exceptions again - a failing feature "
                "must surface an error, not just close the dialog and do nothing.")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} settings-dialog method tests passed")
