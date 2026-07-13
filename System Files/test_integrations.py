"""Tests for Ember integrations (integrations.py). Offline via injected _HTTP."""
import os
import tempfile

os.environ["EMBER_SUPPORT_DIR"] = tempfile.mkdtemp(prefix="ember_intg_test_")

import integrations as ig

_CALLS = []


def _fake_http(method, url, **kw):
    _CALLS.append({"method": method, "url": url, "json": kw.get("json")})
    return 200, "ok"


def _setup():
    ig._HTTP = _fake_http
    ig._save({})
    _CALLS.clear()


def test_set_validates_required_fields():
    _setup()
    assert ig.set_integration("slack")["ok"] is False           # missing webhook_url
    assert ig.set_integration("bogus", url="x")["ok"] is False   # unknown channel
    assert ig.set_integration("slack", webhook_url="https://hooks.slack.com/abc")["ok"]


def test_notify_slack_payload():
    _setup()
    ig.set_integration("slack", webhook_url="https://hooks.slack.com/abc")
    r = ig.notify("hello world")
    assert r["ok"] and r["sent"] == ["slack"], r
    assert _CALLS[-1]["url"].startswith("https://hooks.slack.com/")
    assert _CALLS[-1]["json"] == {"text": "hello world"}


def test_notify_telegram_uses_bot_api_and_chat_id():
    _setup()
    ig.set_integration("telegram", bot_token="123:ABC", chat_id="555")
    ig.notify("ping", channel="telegram")
    call = _CALLS[-1]
    assert "/bot123:ABC/sendMessage" in call["url"]
    assert call["json"]["chat_id"] == "555" and call["json"]["text"] == "ping"


def test_notify_discord_uses_content_key():
    _setup()
    ig.set_integration("discord", webhook_url="https://discord.com/api/webhooks/x")
    ig.notify("yo", channel="discord")
    assert _CALLS[-1]["json"] == {"content": "yo"}


def test_notify_all_configured_channels():
    _setup()
    ig.set_integration("slack", webhook_url="https://hooks.slack.com/abc")
    ig.set_integration("webhook", url="https://example.com/hook")
    r = ig.notify("broadcast")
    assert set(r["sent"]) == {"slack", "webhook"}, r
    assert len(_CALLS) == 2


def test_notify_without_config_errors():
    _setup()
    assert ig.notify("nothing")["ok"] is False


def test_list_masks_secrets():
    _setup()
    ig.set_integration("telegram", bot_token="1234567890:SECRETTOKEN", chat_id="9")
    lst = ig.list_integrations()
    tg = next(c for c in lst["channels"] if c["channel"] == "telegram")
    assert "SECRETTOKEN" not in tg["config"]["bot_token"]    # masked
    assert tg["config"]["chat_id"] == "9"                    # non-secret shown
    assert any(a["channel"] == "slack" for a in lst["available"])


def test_remove_integration():
    _setup()
    ig.set_integration("slack", webhook_url="https://hooks.slack.com/abc")
    assert ig.remove_integration("slack")["ok"]
    assert ig.remove_integration("slack")["ok"] is False
    assert ig.is_configured() is False


def test_failed_status_reported():
    _setup()
    ig._HTTP = lambda method, url, **kw: (500, "err")
    ig.set_integration("slack", webhook_url="https://hooks.slack.com/abc")
    r = ig.notify("x")
    assert r["ok"] is False and r["errors"], r


def test_tool_wiring():
    assert set(ig.TOOL_DISPATCH) == {d["name"] for d in ig.TOOL_DECLARATIONS}
    assert "notify" in ig.INTERACTION_TOOLS and "integration_list" in ig.READONLY_TOOLS


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn(); print(f"PASS  {fn.__name__}"); passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
