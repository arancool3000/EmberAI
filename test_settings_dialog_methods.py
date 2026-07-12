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


def test_emberwindow_calls_are_defined():
    _assert_self_calls_defined("EmberWindow")


def test_adblocker_dialog_calls_are_defined():
    _assert_self_calls_defined("AdBlockerDialog")


def test_storage_inspector_calls_are_defined():
    _assert_self_calls_defined("StorageInspectorDialog")


def test_network_inspector_calls_are_defined():
    _assert_self_calls_defined("NetworkInspectorDialog")


def test_clipboard_history_calls_are_defined():
    _assert_self_calls_defined("ClipboardHistoryDialog")


def test_run_slash_feature_methods_resolve_on_emberwindow():
    """Regression guard: _run_slash's feature_methods dict resolves handlers dynamically via
    getattr(self, name), so a plain self._name() scan (the checks above) can't catch a broken
    entry - the string just has to spell a real EmberWindow method. __sandbox__ and
    __scan_folder__ both used to point at a same-named method that only existed on
    SettingsDialog/AntivirusDialog, so every Command Center click on them silently showed
    'That feature isn't available in this build' even though the method existed elsewhere."""
    tree = ast.parse(open(_UI, encoding="utf-8").read())
    ew = _class_node(tree, "EmberWindow")
    defined = _defined_names(ew)
    run_slash = next(n for n in ast.walk(ew)
                      if isinstance(n, ast.FunctionDef) and n.name == "_run_slash")
    dict_node = next(n.value for n in ast.walk(run_slash)
                      if isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "feature_methods")
    missing = [(k.value, v.value) for k, v in zip(dict_node.keys, dict_node.values)
               if v.value not in defined]
    assert not missing, (
        f"feature_methods entries point at methods EmberWindow doesn't define: {missing}")


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
