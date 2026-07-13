"""Tests for the background agent scheduler (agent_scheduler.py). Hermetic."""
import os
import tempfile
import time

os.environ["EMBER_SUPPORT_DIR"] = tempfile.mkdtemp(prefix="ember_sched_test_")

import agents
import agent_scheduler as sched


def _reset():
    sched.scheduler_stop()
    sched._RUNNER = None
    with sched._LOCK:
        sched._events.clear()
        sched._runs = 0
    agents._save({})


def test_run_due_now_requires_runner():
    _reset()
    agents.create_agent("a", schedule={"every_minutes": 5})
    r = sched.run_due_now()
    assert r["ok"] is False and "no agent runner" in r["error"], r


def test_run_due_now_runs_and_marks():
    _reset()
    calls = []
    sched.set_runner(lambda name: calls.append(name) or {"ok": True, "summary": f"did {name}"})
    agents.create_agent("poller", schedule={"every_minutes": 10})
    assert "poller" in agents.due_agents()
    r = sched.run_due_now()
    assert r["ok"] and r["ran"] == ["poller"], r
    assert calls == ["poller"]
    assert "poller" not in agents.due_agents()        # marked ran -> no longer due
    evs = sched.scheduler_events()["events"]
    assert evs and evs[-1]["agent"] == "poller" and evs[-1]["ok"]
    _reset()


def test_runner_exception_is_recorded_not_raised():
    _reset()
    def boom(name):
        raise RuntimeError("kaboom")
    sched.set_runner(boom)
    agents.create_agent("x", schedule={"every_minutes": 1})
    r = sched.run_due_now()
    assert r["ok"] and r["ran"] == ["x"], r
    assert sched.scheduler_events()["events"][-1]["ok"] is False
    _reset()


def test_only_due_agents_run():
    _reset()
    ran = []
    sched.set_runner(lambda n: ran.append(n) or {"ok": True})
    agents.create_agent("due", schedule={"every_minutes": 5})
    agents.create_agent("noplan")                       # no schedule -> never due
    sched.run_due_now()
    assert ran == ["due"], ran
    _reset()


def test_daemon_lifecycle_runs_due_agent():
    _reset()
    ran = []
    sched.set_runner(lambda n: ran.append(n) or {"ok": True, "summary": "ok"})
    agents.create_agent("ticker", schedule={"every_minutes": 60})
    sched._TICK = 0.05
    try:
        assert sched.scheduler_start()["ok"]
        deadline = time.time() + 3.0
        while time.time() < deadline and not ran:
            time.sleep(0.05)
        assert ran == ["ticker"], ran
        st = sched.scheduler_status()
        assert st["running"] and st["has_runner"] and st["total_runs"] >= 1
    finally:
        sched.scheduler_stop()
        sched._TICK = 30.0
        _reset()


def test_start_idempotent():
    _reset()
    sched.set_runner(lambda n: {"ok": True})
    a = sched.scheduler_start(); b = sched.scheduler_start()
    assert a["ok"] and b["ok"] and "already" in b["message"]
    sched.scheduler_stop()
    _reset()


def test_tool_wiring():
    assert set(sched.TOOL_DISPATCH) == {d["name"] for d in sched.TOOL_DECLARATIONS}
    assert "scheduler_status" in sched.READONLY_TOOLS


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
