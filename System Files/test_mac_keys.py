"""Hermetic tests for mac_keys — the macOS atomic key-combo sender that fixes shortcuts like
'cmd+w' being split into 'command' then 'w' (so the chord never fires). The script builder is
pure; send_combo's runner is injected. No OS / no osascript.
Run: python test_mac_keys.py"""
import mac_keys as mk


def test_simple_cmd_combo_holds_modifier():
    s = mk.build_combo_script(["cmd", "w"])
    assert s == 'tell application "System Events" to keystroke "w" using {command down}'


def test_multiple_modifiers_all_held_together():
    s = mk.build_combo_script(["command", "shift", "t"])
    # both modifiers in ONE `using {…}` chord (the whole point — pressed simultaneously)
    assert "using {command down, shift down}" in s
    assert 'keystroke "t"' in s


def test_modifier_aliases_normalise():
    assert "control down" in mk.build_combo_script(["ctrl", "c"])
    assert "option down" in mk.build_combo_script(["alt", "a"])
    assert "command down" in mk.build_combo_script(["win", "l"])


def test_special_key_uses_key_code():
    assert mk.build_combo_script(["cmd", "left"]) == \
        'tell application "System Events" to key code 123 using {command down}'
    assert "key code 51" in mk.build_combo_script(["cmd", "delete"])   # cmd+delete
    assert "key code 36" in mk.build_combo_script(["cmd", "enter"])


def test_quotes_and_backslashes_are_escaped():
    assert mk.build_combo_script(["cmd", '"']) == \
        'tell application "System Events" to keystroke "\\"" using {command down}'


def test_no_modifier_returns_none():
    assert mk.build_combo_script(["w"]) is None
    assert mk.build_combo_script([]) is None


def test_unknown_multichar_key_returns_none():
    # a multi-char key that isn't a known special can't be expressed -> caller falls back
    assert mk.build_combo_script(["cmd", "frobnicate"]) is None


def test_send_combo_runs_built_script_via_injected_runner():
    seen = {}
    ok = mk.send_combo(["cmd", "c"], _runner=lambda script: seen.setdefault("s", script) or True)
    assert ok is True
    assert seen["s"] == 'tell application "System Events" to keystroke "c" using {command down}'


def test_send_combo_false_when_unexpressible():
    called = {"n": 0}
    ok = mk.send_combo(["w"], _runner=lambda s: called.__setitem__("n", called["n"] + 1) or True)
    assert ok is False and called["n"] == 0   # never even tried to run


def test_send_combo_contains_runner_exception():
    def boom(_s):
        raise RuntimeError("osascript missing")
    assert mk.send_combo(["cmd", "w"], _runner=boom) is False


def test_send_combo_false_when_runner_reports_failure():
    assert mk.send_combo(["cmd", "w"], _runner=lambda s: False) is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} mac_keys tests passed")
