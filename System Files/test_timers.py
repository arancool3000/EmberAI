"""Hermetic tests for timers.py — duration parsing, the timer registry, firing, and the
exported tool wiring. No GUI, no native deps. Run: python test_timers.py"""
import time

import timers


def _reset():
    timers.clear_all()
    timers.set_fire_callback(None)


def test_parse_duration_units():
    assert timers.parse_duration("5m") == 300
    assert timers.parse_duration("90s") == 90
    assert timers.parse_duration("1h30m") == 5400
    assert timers.parse_duration("2 minutes") == 120
    assert timers.parse_duration("1h 1m 30s") == 3690
    assert timers.parse_duration("45") == 45            # bare number -> seconds
    assert timers.parse_duration(10) == 10              # numeric input
    assert timers.parse_duration("") is None
    assert timers.parse_duration("soon") is None
    assert timers.parse_duration("0s") is None          # zero/negative -> no timer


def test_human_duration():
    assert timers.human_duration(0) == "0s"
    assert timers.human_duration(90) == "1m 30s"
    assert timers.human_duration(3690) == "1h 1m 30s"
    assert timers.human_duration(3600) == "1h"


def test_set_list_cancel():
    _reset()
    r = timers.set_timer("1h", label="laundry")
    assert r["ok"] is True and r["id"].startswith("timer-")
    assert r["duration_seconds"] == 3600 and r["label"] == "laundry"

    listing = timers.list_timers()
    assert listing["ok"] is True and listing["active"] == 1
    entry = listing["timers"][0]
    assert entry["label"] == "laundry" and entry["status"] == "running"
    assert 3590 <= entry["remaining_seconds"] <= 3600

    c = timers.cancel_timer(r["id"])
    assert c["ok"] is True and c["count"] == 1
    assert timers.list_timers()["active"] == 0
    _reset()


def test_cancel_all_and_errors():
    _reset()
    timers.set_timer("10m")
    timers.set_timer("20m")
    assert timers.list_timers()["active"] == 2
    c = timers.cancel_timer("all")
    assert c["ok"] is True and c["count"] == 2
    assert timers.list_timers()["active"] == 0
    assert timers.cancel_timer("")["ok"] is False
    assert timers.cancel_timer("nope")["ok"] is False
    assert timers.set_timer("not-a-duration")["ok"] is False
    _reset()


def test_timer_actually_fires():
    _reset()
    fired = []
    timers.set_fire_callback(lambda info: fired.append(info))
    r = timers.set_timer("0.15s", label="tea")   # ~150ms so the test stays fast
    assert r["ok"] is True
    deadline = time.time() + 2.0
    while not fired and time.time() < deadline:
        time.sleep(0.02)
    assert fired, "timer callback never fired"
    assert fired[0]["label"] == "tea" and "time's up" in fired[0]["message"].lower()
    # After firing it is no longer 'running'.
    assert timers.list_timers()["active"] == 0
    _reset()


def test_exports_consistent():
    assert set(timers.TOOL_DISPATCH) == {d["name"] for d in timers.TOOL_DECLARATIONS}
    assert set(timers.TOOL_DISPATCH) == {"set_timer", "list_timers", "cancel_timer"}
    assert timers.READONLY_TOOLS == {"list_timers"}
    assert timers.INTERACTION_TOOLS == {"set_timer", "cancel_timer"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} timers tests passed")
