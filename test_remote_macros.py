"""Hermetic tests for remote_server's quick-macros (Lock PC / Mute / Mic Off / Sleep / custom
command) fired from the phone remote. No GUI, no network, no real OS actions — the heavy deps
(pyautogui/tools) are stubbed and macro implementations are injected via set_macro_hooks.
Run: python test_remote_macros.py"""
import sys
import types

# remote_server imports pyautogui + tools at module load (GUI/native deps). Stub them so this
# test runs in CI with only the standard library.
if "pyautogui" not in sys.modules:
    pg = types.ModuleType("pyautogui")
    pg.FAILSAFE = False
    pg.PAUSE = 0
    pg.size = lambda: (1920, 1080)
    sys.modules["pyautogui"] = pg
if "tools" not in sys.modules:
    t = types.ModuleType("tools")
    t.run_powershell = lambda cmd, timeout=60: {"ok": True, "ran": cmd}
    t.press_key = lambda *a, **k: None
    t.type_text = lambda *a, **k: None
    sys.modules["tools"] = t

import remote_server as rs


def setup_function(_=None):
    rs._MACRO_HOOKS.clear()


def test_macro_names_match_table():
    assert rs.MACRO_NAMES == {n for n, _ in rs.MACROS}
    assert "lock" in rs.MACRO_NAMES and "mute_mic" in rs.MACRO_NAMES


def test_unknown_macro_is_rejected():
    rs._MACRO_HOOKS.clear()
    r = rs._run_macro("self_destruct")
    assert r["ok"] is False and "unknown" in r["detail"].lower()


def test_injected_hook_runs_and_is_reported():
    rs._MACRO_HOOKS.clear()
    calls = []
    rs.set_macro_hooks(lock=lambda: calls.append("lock") or {"ok": True, "detail": "locked!"})
    r = rs._run_macro("lock")
    assert calls == ["lock"]
    assert r == {"ok": True, "detail": "locked!", "macro": "lock"}


def test_hook_bool_and_none_results():
    rs._MACRO_HOOKS.clear()
    rs.set_macro_hooks(mute=lambda: True, unmute=lambda: None, mute_mic=lambda: False)
    assert rs._run_macro("mute")["ok"] is True
    assert rs._run_macro("unmute")["ok"] is True        # None = "did it, no detail"
    assert rs._run_macro("mute_mic")["ok"] is False


def test_macro_name_is_case_insensitive():
    rs._MACRO_HOOKS.clear()
    seen = []
    rs.set_macro_hooks(sleep_display=lambda: seen.append(1) or True)
    assert rs._run_macro("SLEEP_DISPLAY")["ok"] is True
    assert seen == [1]


def test_hook_exception_is_contained():
    rs._MACRO_HOOKS.clear()

    def boom():
        raise RuntimeError("nope")

    rs.set_macro_hooks(lock=boom)
    r = rs._run_macro("lock")
    assert r["ok"] is False and "nope" in r["detail"]


def test_apply_locked_routes_macro_events():
    rs._MACRO_HOOKS.clear()
    hits = []
    rs.set_macro_hooks(lock=lambda: hits.append("x") or {"ok": True})
    out = rs._apply({"t": "macro", "name": "lock"})   # goes through the input lock too
    assert hits == ["x"]
    assert out["ok"] is True and out["macro"] == "lock"


def test_shell_macro_empty_is_noop():
    rs._MACRO_HOOKS.clear()
    assert rs._run_shell_macro("")["ok"] is False
    assert rs._run_shell_macro("   ")["ok"] is False


def test_shell_macro_uses_hook_then_tools():
    rs._MACRO_HOOKS.clear()
    captured = {}
    rs.set_macro_hooks(shell=lambda c: captured.setdefault("cmd", c) or {"ok": True, "cmd": c})
    r = rs._run_shell_macro("echo hi")
    assert captured["cmd"] == "echo hi" and r["ok"] is True
    # without a hook it falls back to tools.run_powershell (stubbed)
    rs._MACRO_HOOKS.clear()
    r2 = rs._apply({"t": "macro_cmd", "cmd": "ls"})
    assert r2.get("ran") == "ls"


def test_phone_page_exposes_macro_buttons():
    page = rs.PAGE
    assert "macro('lock')" in page and "macro('mute_mic')" in page
    assert "function macro(" in page and "function runcmd(" in page
    assert 'post({t:"macro"' in page and 'post({t:"macro_cmd"' in page


def test_phone_page_button_and_fullscreen_fixes():
    page = rs.PAGE
    # toolbar buttons (Balanced/Fast) must size to content so the label can't overflow
    assert "button.small{flex:0 0 auto" in page and "white-space:nowrap" in page
    # #kb buttons (Run/Send) must not shrink below their text either
    assert "#kb button{flex:0 0 auto" in page
    # ping-pong double-buffer: the frame loads into the hidden image and only swaps visibility
    # once fully decoded, so the VISIBLE image's src is never reassigned (kills the Kindle
    # black-flash - a preload-then-swap-src approach still flashed on old WebKit).
    assert 'id=screenA class=screenimg' in page and 'id=screenB class=screenimg' in page
    assert "id=screenhit" in page
    assert "back.decode" in page and "front.style.display" in page
    # landscape fake-fullscreen for devices without (or that lie about) the Fullscreen API
    assert "body.fakefs" in page and "function fakeFS(" in page
    assert "x=ry;y=1-rx" in page and "id=fsexit" in page
    # toggleFS must self-verify real fullscreen actually engaged, not just feature-detect it
    # (Kindle Silk exposes requestFullscreen but silently no-ops it)
    fs_fn = page.split("function toggleFS(){", 1)[1].split("\nfunction ", 1)[0]
    assert "setTimeout(()=>" in fs_fn and "fakeFS(true)" in fs_fn


def test_phone_page_has_no_decorative_emoji_on_macro_buttons():
    page = rs.PAGE
    # quick-action buttons must be plain text (matches every other button label in the app -
    # Copy/Paste/Cut/Undo/etc. are all plain text already; the macro buttons used to be the
    # only ones with colourful emoji, which render poorly on Kindle e-ink)
    for bad in ("🔒", "🔇", "🔊", "🎙", "🌙"):
        assert bad not in page, f"unexpected decorative emoji {bad!r} still in the phone page"
    assert ">Lock PC<" in page and ">Mic Off<" in page and ">Sleep<" in page
    assert ">Mute<" in page and ">Unmute<" in page


def test_macros_table_labels_are_plain_text():
    for _name, label in rs.MACROS:
        assert all(ord(c) < 0x2000 for c in label), f"MACROS label {label!r} has a non-plain-text char"


def test_phone_page_is_installable_pwa():
    page = rs.PAGE
    assert 'rel="manifest"' in page and "apple-mobile-web-app-capable" in page
    assert "apple-touch-icon" in page
    assert "@media(min-width:760px)" in page          # iPad / tablet layout
    import json
    m = json.loads(rs.MANIFEST)
    assert m["display"] == "standalone" and m["icons"]
    assert isinstance(rs._icon_bytes(), (bytes, bytearray))   # icon endpoint has data to serve


def test_manifest_and_icon_routes_constants():
    # the manifest must be valid JSON and reference the icon route the page links to
    import json
    m = json.loads(rs.MANIFEST)
    assert any(i["src"] == "/icon.png" for i in m["icons"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        setup_function()
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} remote-macro tests passed")
