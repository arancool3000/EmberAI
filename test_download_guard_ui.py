"""Regression tests for the download-protection review popup + shared AI malware judge.

Two layers, mirroring the ember_browser tests:

* Behavioural — ai_detect.judge_harmful / ai_available are importable in CI (their google/
  anthropic imports are lazy), so we exercise them for real against a fake model, and check
  the exact wiring the app uses (antivirus.set_ai_judge(lambda items:
  ai_detect.judge_harmful(items, settings))) actually drives ai_assess_file.

* Source/AST — ui.py hard-imports PyQt6 at load, so the UI is verified structurally: the new
  DownloadGuardDialog (Scan-with-AI / Quarantine / Delete), the window-surfacing on alert, and
  the app-wide judge registration. Guards the exact complaint: the old alert "just warned" —
  no scan action and it didn't bring Ember to the front.

Run: python test_download_guard_ui.py
"""
import ast
import os

_UI_PATH = os.path.join(os.path.dirname(__file__), "ui.py")
_UI = open(_UI_PATH, encoding="utf-8").read()
_TREE = ast.parse(_UI)


def _class(name):
    for n in ast.walk(_TREE):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    raise AssertionError(f"class {name} not found in ui.py")


def _methods(cls):
    return {n.name for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _method_src(cls, name):
    fn = next(f for f in ast.walk(cls) if isinstance(f, ast.FunctionDef) and f.name == name)
    return ast.get_source_segment(_UI, fn)


# ---- behavioural: the shared AI judge ---------------------------------------------------

def test_judge_harmful_parses_model_verdicts():
    import ai_detect
    orig = ai_detect._ask_model
    ai_detect._ask_model = lambda prompt, settings: "Sure: [false, true]"
    try:
        items = [{"name": "a.py", "reasons": [], "excerpt": "print(1)"},
                 {"name": "b.sh", "reasons": ["reverse shell"], "excerpt": "bash -i >& /dev/tcp"}]
        assert ai_detect.judge_harmful(items, {}) == [False, True]
    finally:
        ai_detect._ask_model = orig


def test_judge_harmful_uncertain_defaults_to_flagged():
    import ai_detect
    orig = ai_detect._ask_model
    try:
        # No parseable array (e.g. no API key -> "") must fail SAFE: keep everything flagged.
        ai_detect._ask_model = lambda prompt, settings: ""
        assert ai_detect.judge_harmful([{"name": "x"}], {}) == [True]
        ai_detect._ask_model = lambda prompt, settings: "I cannot help with that."
        assert ai_detect.judge_harmful([{"name": "x"}, {"name": "y"}], {}) == [True, True]
    finally:
        ai_detect._ask_model = orig


def test_judge_harmful_pads_a_short_reply():
    import ai_detect
    orig = ai_detect._ask_model
    ai_detect._ask_model = lambda prompt, settings: "[false]"   # only one verdict for two files
    try:
        out = ai_detect.judge_harmful([{"name": "a"}, {"name": "b"}], {})
        assert out == [False, True]     # missing verdict padded True (safe)
    finally:
        ai_detect._ask_model = orig


def test_judge_harmful_empty_is_empty():
    import ai_detect
    assert ai_detect.judge_harmful([], {}) == []


def test_ai_available_reflects_configured_keys():
    import ai_detect
    assert ai_detect.ai_available({}) is False
    assert ai_detect.ai_available({"gemini_api_key": "x"}) is True
    assert ai_detect.ai_available({"anthropic_api_key": "y"}) is True
    assert ai_detect.ai_available({"gemini_api_key": "   "}) is False   # whitespace-only != a key


def test_app_wiring_drives_ai_assess_file():
    # The exact lambda ui.py registers must make antivirus.ai_assess_file return a verdict.
    import tempfile
    import ai_detect
    import antivirus
    orig = ai_detect._ask_model
    ai_detect._ask_model = lambda prompt, settings: "[true]"
    antivirus.set_ai_judge(lambda items: ai_detect.judge_harmful(items, {"gemini_api_key": "k"}))
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("import os\n")
            p = f.name
        r = antivirus.ai_assess_file(p, ["heuristic"])
        assert r["available"] is True and r["verdict"] == "malicious", r
        os.unlink(p)
    finally:
        ai_detect._ask_model = orig
        antivirus.set_ai_judge(None)


# ---- source/AST: the UI popup + wiring --------------------------------------------------

def test_download_guard_dialog_has_scan_and_actions():
    cls = _class("DownloadGuardDialog")
    m = _methods(cls)
    for meth in ("_start_scan", "_on_scan_done", "_quarantine", "_delete", "_do_quarantine"):
        assert meth in m, f"DownloadGuardDialog missing {meth}"
    src = ast.get_source_segment(_UI, cls)
    assert "_scan_done" in src and "pyqtSignal" in src          # threaded scan marshals back
    assert "Scan" in src                                        # a Scan button label
    # the scan uses the real engines + the AI second opinion
    assert "scan_file" in src and "ai_assess_file" in src
    assert "quarantine_file" in src                             # can block by quarantining


def test_scan_runs_off_ui_thread():
    # The scan must not run on the UI thread (it hits disk + the network model) — it spawns a
    # thread and reports back via the _scan_done signal.
    src = _method_src(_class("DownloadGuardDialog"), "_start_scan")
    assert "threading.Thread" in src and "_scan_done.emit" in src


def test_alert_surfaces_window_and_opens_dialog():
    win = _class("EmberWindow")
    assert "_surface_window" in _methods(win) and "_on_download_alert" in _methods(win)
    alert = _method_src(win, "_on_download_alert")
    assert "self._surface_window()" in alert            # bring Ember to the front
    assert "DownloadGuardDialog(" in alert              # blocking review popup, not a passive toast
    surf = _method_src(win, "_surface_window")
    # actually raise + activate the window (and un-minimize it)
    assert "raise_()" in surf and "activateWindow()" in surf
    assert "WindowMinimized" in surf and "requestActivate" in surf


def test_ai_judge_registered_app_wide_not_just_in_antivirus_window():
    win = _class("EmberWindow")
    assert "_register_av_ai_judge" in _methods(win)
    reg = _method_src(win, "_register_av_ai_judge")
    assert "set_ai_judge" in reg and "judge_harmful" in reg
    # and it's actually invoked when protection autostarts
    auto = _method_src(win, "_autostart_download_protection")
    assert "_register_av_ai_judge()" in auto


def test_antivirus_dialog_delegates_to_shared_judge():
    src = _method_src(_class("AntivirusDialog"), "_ai_judge")
    assert "judge_harmful" in src      # no duplicated prompt logic — one shared implementation


def test_downloadguard_self_calls_resolve():
    cls = _class("DownloadGuardDialog")
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
    assert not missing, f"DownloadGuardDialog calls undefined self methods: {missing}"


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
