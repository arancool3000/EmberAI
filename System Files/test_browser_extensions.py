"""Hermetic tests for browser_extensions — the AI-built userscript store the Ember
Browser injects into pages. Pure logic: storage (redirected to a temp file via
EMBER_EXT_FILE), URL matching, prompt building and model-output cleaning. No Qt, no LLM.

Run: python test_browser_extensions.py
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ember_ext_test_")
os.environ["EMBER_EXT_FILE"] = os.path.join(_TMP, "exts.json")

import browser_extensions as bx


def _reset():
    try:
        os.remove(os.environ["EMBER_EXT_FILE"])
    except FileNotFoundError:
        pass


def test_save_list_get_roundtrip():
    _reset()
    e = bx.save_extension("Hide ads", "youtube.com", "document.title='x';", description="d")
    assert e["id"]
    items = bx.list_extensions()
    assert len(items) == 1
    got = bx.get_extension(e["id"])
    assert got is not None and got["name"] == "Hide ads"
    assert got["match"] == "youtube.com"
    assert got["enabled"] is True


def test_update_in_place_by_id():
    _reset()
    e = bx.save_extension("v1", "*", "a();")
    bx.save_extension("v2", "example.com", "b();", ext_id=e["id"], enabled=False)
    items = bx.list_extensions()
    assert len(items) == 1, "updating by id must not create a duplicate"
    got = bx.get_extension(e["id"])
    assert got["name"] == "v2" and got["match"] == "example.com" and got["enabled"] is False


def test_delete():
    _reset()
    e = bx.save_extension("x", "*", "a();")
    assert bx.delete_extension(e["id"]) is True
    assert bx.list_extensions() == []
    assert bx.delete_extension("nope") is False


def test_set_enabled():
    _reset()
    e = bx.save_extension("x", "*", "a();")
    assert bx.set_enabled(e["id"], False) is True
    assert bx.get_extension(e["id"])["enabled"] is False
    assert bx.set_enabled("missing", True) is False


def test_match_wildcard_and_empty():
    assert bx.match_url("*", "https://anything.com/x") is True
    assert bx.match_url("", "https://anything.com/x") is True
    assert bx.match_url("*", "") is True
    assert bx.match_url("example.com", "") is False


def test_match_bare_domain_includes_subdomains():
    assert bx.match_url("youtube.com", "https://www.youtube.com/watch?v=1") is True
    assert bx.match_url("youtube.com", "https://youtube.com/") is True
    assert bx.match_url("youtube.com", "https://example.com/youtube.com") is False  # host, not path
    assert bx.match_url("example.com", "https://notexample.com/") is False


def test_match_glob():
    assert bx.match_url("https://*.example.com/*", "https://a.example.com/page") is True
    assert bx.match_url("*youtube*", "https://m.youtube.com/feed") is True
    assert bx.match_url("https://site.com/admin*", "https://site.com/admin/panel") is True
    assert bx.match_url("https://site.com/admin*", "https://site.com/home") is False


def test_scripts_for_url_filters_enabled_matching_nonempty():
    _reset()
    a = bx.save_extension("on-match", "example.com", "a();")
    bx.save_extension("off", "example.com", "b();", enabled=False)
    bx.save_extension("other-site", "other.com", "c();")
    bx.save_extension("empty-js", "example.com", "   ")
    got = bx.scripts_for_url("https://example.com/page")
    names = {e["name"] for e in got}
    assert names == {"on-match"}, names


def test_wrap_for_injection_is_guarded_iife():
    w = bx.wrap_for_injection("doStuff();")
    assert w.startswith("(function(){try{")
    assert "doStuff();" in w
    assert "catch" in w and w.strip().endswith("})();")


def test_build_prompt_mentions_constraints():
    p = bx.build_userscript_prompt("hide comments", "https://x.com")
    assert "hide comments" in p
    assert "https://x.com" in p
    assert "ONLY JavaScript" in p
    assert "network" in p.lower()  # the no-exfiltration / no-network rule


def test_extract_js_strips_fences():
    fenced = "Here you go:\n```js\nconsole.log(1);\n```\nEnjoy!"
    assert bx.extract_js(fenced) == "console.log(1);"
    fenced2 = "```javascript\nalert(2);\n```"
    assert bx.extract_js(fenced2) == "alert(2);"
    plain = "console.log(3);"
    assert bx.extract_js(plain) == "console.log(3);"


def test_load_tolerates_garbage_file():
    _reset()
    with open(os.environ["EMBER_EXT_FILE"], "w") as f:
        f.write("{not json")
    assert bx.list_extensions() == []
    with open(os.environ["EMBER_EXT_FILE"], "w") as f:
        f.write('{"a": 1}')  # not a list
    assert bx.list_extensions() == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} browser_extensions tests passed")
