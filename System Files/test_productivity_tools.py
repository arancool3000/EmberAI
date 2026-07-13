"""Tests for productivity_tools.

These run WITHOUT mss / Pillow / imageio installed: the screen tools must degrade to a
friendly {"ok": False, "error": "<dep> not installed ..."} mentioning the missing dependency.
No real network requests are made (the breach test only exercises the validation guard).
"""
import productivity_tools as pt


# ---------------------------------------------------------------------------
# Snippet expander (pure, JSON-persisted) — monkeypatch SNIPPETS_FILE to tmp.
# ---------------------------------------------------------------------------
def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(pt, "SNIPPETS_FILE", tmp_path / "snippets.json")


def test_snippet_save_and_get(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    r = pt.snippet_save("addr", "123 St")
    assert r["ok"] and r["keyword"] == "addr"
    g = pt.snippet_get("addr")
    assert g["ok"] and g["text"] == "123 St"


def test_snippet_list_and_count(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    pt.snippet_save("a", "alpha")
    pt.snippet_save("b", "beta")
    r = pt.snippet_list()
    assert r["ok"] and r["count"] == 2
    assert set(r["snippets"]) == {"a", "b"}


def test_snippet_list_preview_truncates(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    long = "x" * 200
    pt.snippet_save("big", long)
    r = pt.snippet_list()
    assert r["snippets"]["big"].endswith("...")
    assert len(r["snippets"]["big"]) <= pt._PREVIEW_LEN + 3


def test_snippet_get_not_found(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    r = pt.snippet_get("nope")
    assert r["ok"] is False and "nope" in r["error"]


def test_snippet_delete(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    pt.snippet_save("tmp", "value")
    d = pt.snippet_delete("tmp")
    assert d["ok"] and d["deleted"] is True
    d2 = pt.snippet_delete("tmp")
    assert d2["ok"] and d2["deleted"] is False
    assert pt.snippet_get("tmp")["ok"] is False


def test_snippet_expand_basic(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    pt.snippet_save("addr", "123 St")
    r = pt.snippet_expand("go to ;addr now")
    assert r["ok"] and r["result"] == "go to 123 St now"
    assert r["expansions"] == 1


def test_snippet_expand_multiple_and_longest_first(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    pt.snippet_save("sig", "Best, Sam")
    pt.snippet_save("sign", "SHOULD NOT WIN")
    out = pt.snippet_expand(";sign here and ;sig")
    # ;sign matched as a whole token (longest-first), then ;sig.
    assert out["result"] == "SHOULD NOT WIN here and Best, Sam"
    assert out["expansions"] == 2


def test_snippet_expand_no_snippets(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    r = pt.snippet_expand("nothing ;here")
    assert r["ok"] and r["result"] == "nothing ;here" and r["expansions"] == 0


def test_snippet_save_requires_keyword(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert pt.snippet_save("", "x")["ok"] is False


# ---------------------------------------------------------------------------
# Email breach monitor — validation guard (NO network), then a faked response.
# ---------------------------------------------------------------------------
def test_email_breach_invalid_no_network(monkeypatch):
    # If validation fails first, requests.get must never be called.
    def _boom(*a, **k):
        raise AssertionError("network call must not happen for an invalid email")
    monkeypatch.setattr(pt.requests, "get", _boom)
    r = pt.email_breach_check("not-an-email")
    assert r["ok"] is False and "invalid" in r["error"].lower()


def test_email_breach_empty(monkeypatch):
    monkeypatch.setattr(pt.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    assert pt.email_breach_check("")["ok"] is False


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_email_breach_clean_via_404(monkeypatch):
    monkeypatch.setattr(pt.requests, "get", lambda *a, **k: _FakeResp(404, {}))
    r = pt.email_breach_check("clean@example.com")
    assert r["ok"] and r["breached"] is False and r["count"] == 0


def test_email_breach_hit(monkeypatch):
    payload = {"breaches": [["Adobe", "LinkedIn"]]}
    monkeypatch.setattr(pt.requests, "get", lambda *a, **k: _FakeResp(200, payload))
    r = pt.email_breach_check("victim@example.com")
    assert r["ok"] and r["breached"] is True
    assert set(r["breaches"]) == {"Adobe", "LinkedIn"} and r["count"] == 2


def test_email_breach_clean_via_error_field(monkeypatch):
    monkeypatch.setattr(pt.requests, "get",
                        lambda *a, **k: _FakeResp(200, {"Error": "Not found"}))
    r = pt.email_breach_check("clean@example.com")
    assert r["ok"] and r["breached"] is False


# ---------------------------------------------------------------------------
# Screen tools — must report the missing optional dep in THIS environment.
# ---------------------------------------------------------------------------
def _missing_dep(err: str) -> bool:
    e = err.lower()
    return "not installed" in e and ("mss" in e or "pillow" in e or "pil" in e)


def test_screen_record_start_friendly_without_deps():
    r = pt.screen_record_start(seconds=2, fps=4)
    if r["ok"]:
        # mss + a writer happened to be present; stop cleanly so we don't leak a thread.
        pt.screen_record_stop()
    else:
        assert _missing_dep(r["error"]), r


def test_pick_screen_color_friendly_without_deps():
    r = pt.pick_screen_color(0, 0)
    if not r["ok"]:
        assert _missing_dep(r["error"]), r


def test_screenshot_monitor_friendly_without_deps():
    r = pt.screenshot_monitor(1)
    if not r["ok"]:
        # Either mss missing, or present-with-index-out-of-range; both are friendly.
        e = r["error"].lower()
        assert _missing_dep(r["error"]) or "out of range" in e, r


def test_screen_record_status_always_ok():
    r = pt.screen_record_status()
    assert r["ok"] and "recording" in r and "frames" in r


# ---------------------------------------------------------------------------
# Wiring integrity.
# ---------------------------------------------------------------------------
def test_dispatch_matches_declarations():
    assert set(pt.TOOL_DISPATCH) == {d["name"] for d in pt.TOOL_DECLARATIONS}


def test_tool_names_unique():
    names = [d["name"] for d in pt.TOOL_DECLARATIONS]
    assert len(names) == len(set(names))


def test_no_collision_with_existing_tools():
    existing = {"list_monitors", "color_at", "clipboard_history_get"}
    assert set(pt.TOOL_DISPATCH).isdisjoint(existing)


def test_readonly_and_interaction_partition():
    ro = pt.READONLY_TOOLS
    inter = pt.INTERACTION_TOOLS
    assert ro.isdisjoint(inter)
    assert ro | inter == set(pt.TOOL_DISPATCH)


def test_declaration_types_uppercase():
    allowed = {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "OBJECT", "ARRAY"}
    for d in pt.TOOL_DECLARATIONS:
        params = d["parameters"]
        assert params["type"] == "OBJECT"
        for prop in params["properties"].values():
            assert prop["type"] in allowed, prop
