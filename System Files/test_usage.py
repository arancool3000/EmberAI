"""Tests for the usage dashboard module."""
import importlib

import pytest

import usage


@pytest.fixture(autouse=True)
def _fresh_usage_file(tmp_path, monkeypatch):
    """Point USAGE_FILE at a per-test temp file so tests never touch the real data dir
    and start from a clean slate."""
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.json")
    yield


def test_record_call_counts_and_tokens():
    usage.record_call("gemini-2.0-flash", prompt_tokens=10, output_tokens=5)
    usage.record_call("gemini-2.0-flash", prompt_tokens=20, output_tokens=0)
    usage.record_call("gemini-1.5-pro", prompt_tokens=1, output_tokens=2)

    s = usage.summary()
    assert s["ok"] is True
    assert s["calls_today"] == 3
    assert s["tokens_today"] == 10 + 5 + 20 + 0 + 1 + 2  # 38
    assert s["by_model"] == {"gemini-2.0-flash": 2, "gemini-1.5-pro": 1}


def test_calls_last_minute_reflects_window():
    for _ in range(4):
        usage.record_call("m")
    s = usage.summary()
    assert s["calls_last_minute"] == 4
    assert s["calls_today"] == 4


def test_percentages_and_remaining_math():
    # 3 calls this minute against a 15/min limit -> 20%, 12 remaining.
    for _ in range(3):
        usage.record_call("m")
    s = usage.summary()
    assert s["limit_per_minute"] == 15
    assert s["limit_per_day"] == 500
    assert s["minute_pct"] == pytest.approx(20.0)
    assert s["day_pct"] == pytest.approx(0.6)  # 3/500 -> 0.6%
    assert s["minute_remaining"] == 15 - 3
    assert s["day_remaining"] == 500 - 3


def test_percentages_cap_and_remaining_floor():
    # More than the per-minute limit must clamp pct to 100 and remaining to 0.
    for _ in range(20):
        usage.record_call("m")
    s = usage.summary()
    assert s["minute_pct"] == 100.0
    assert s["minute_remaining"] == 0
    assert s["calls_last_minute"] == 20  # raw count is still truthful


def test_last_7_days_shape():
    usage.record_call("m", 1, 1)
    s = usage.summary()
    assert len(s["last_7_days"]) == 7
    last = s["last_7_days"][-1]
    assert last["date"] == s["date"]
    assert last["calls"] == 1
    assert last["tokens"] == 2
    # Earlier (empty) days are zero-filled.
    assert s["last_7_days"][0]["calls"] == 0


def test_usage_reset_zeroes_everything():
    for _ in range(5):
        usage.record_call("m", 3, 3)
    assert usage.summary()["calls_today"] == 5

    r = usage.usage_reset()
    assert r["ok"] is True
    assert "message" in r

    s = usage.summary()
    assert s["calls_today"] == 0
    assert s["tokens_today"] == 0
    assert s["calls_last_minute"] == 0
    assert s["by_model"] == {}
    assert s["minute_pct"] == 0.0
    assert s["day_pct"] == 0.0
    assert s["minute_remaining"] == 15
    assert s["day_remaining"] == 500


def test_record_call_never_raises_with_weird_args():
    # None model, negative/None tokens, wrong types -> must not raise, must still count.
    usage.record_call()  # all defaults
    usage.record_call(model=None, prompt_tokens=None, output_tokens=None)
    usage.record_call(model=None, prompt_tokens=-5, output_tokens=-10)
    usage.record_call(model="x", prompt_tokens="not-a-number", output_tokens=None)

    s = usage.summary()
    assert s["ok"] is True
    assert s["calls_today"] == 4
    # None model coerced to a stable key; bad/negative tokens don't blow up.
    assert s["by_model"].get("unknown", 0) >= 2


def test_usage_summary_tool_matches_summary():
    usage.record_call("m", 2, 3)
    assert usage.usage_summary() == usage.summary()


def test_persistence_across_reload(tmp_path, monkeypatch):
    path = tmp_path / "usage.json"
    monkeypatch.setattr(usage, "USAGE_FILE", path)
    usage.record_call("m", 1, 1)

    reloaded = importlib.reload(usage)
    monkeypatch.setattr(reloaded, "USAGE_FILE", path)
    s = reloaded.summary()
    assert s["calls_today"] == 1
    assert s["tokens_today"] == 2


def test_tool_dispatch_matches_declarations():
    assert set(usage.TOOL_DISPATCH) == {d["name"] for d in usage.TOOL_DECLARATIONS}
    assert usage.READONLY_TOOLS == {"usage_summary"}
    assert usage.INTERACTION_TOOLS == {"usage_reset"}
