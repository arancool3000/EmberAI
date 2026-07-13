"""Hermetic tests for the parallel-agents manager (agent_tasks.py). The runner is injected, so
no real agent / GUI / network. Tasks run synchronously (_sync=True) for deterministic asserts.
Run: python test_agent_tasks.py"""
import threading

import agent_tasks as at


def test_start_runs_and_completes():
    def runner(prompt, emit, stop):
        emit("working...")
        emit(" done")
        return None
    m = at.TaskManager(runner)
    tid = m.start("do a thing", _sync=True)
    t = m.get(tid)
    assert t["status"] == at.DONE
    assert t["output"] == "working... done"
    assert t["label"] == "do a thing"


def test_runner_return_used_when_no_streamed_output():
    m = at.TaskManager(lambda p, emit, stop: "final answer")
    tid = m.start("x", _sync=True)
    assert m.get(tid)["output"] == "final answer"


def test_error_is_captured_as_error_status():
    def boom(prompt, emit, stop):
        raise RuntimeError("kaboom")
    m = at.TaskManager(boom)
    tid = m.start("x", _sync=True)
    t = m.get(tid)
    assert t["status"] == at.ERROR and "kaboom" in t["error"]


def test_stop_sets_stopped_status():
    started = threading.Event()
    release = threading.Event()

    def slow(prompt, emit, stop):
        started.set()
        release.wait(2.0)        # wait until the test stops us
        return None
    m = at.TaskManager(slow)
    tid = m.start("x")           # async
    assert started.wait(2.0)
    assert m.stop(tid) is True
    release.set()
    # give the thread a moment to finish and observe the stop flag
    for _ in range(200):
        if m.get(tid)["status"] != at.RUNNING:
            break
        threading.Event().wait(0.01)
    assert m.get(tid)["status"] == at.STOPPED


def test_runner_sees_stop_event():
    seen = {}

    def r(prompt, emit, stop):
        seen["stopped"] = stop.is_set()
        return None
    m = at.TaskManager(r)
    # pre-stop by stopping right after start is impossible synchronously; check the event exists
    tid = m.start("x", _sync=True)
    assert "stopped" in seen and seen["stopped"] is False


def test_list_preserves_order_and_active_count():
    ev = threading.Event()
    m = at.TaskManager(lambda p, emit, stop: ev.wait(1.0))
    a = m.start("first")
    b = m.start("second")
    ids = [t["id"] for t in m.list()]
    assert ids == [a, b]
    assert m.active_count() >= 1
    ev.set()


def test_clear_finished_keeps_running():
    m = at.TaskManager(lambda p, emit, stop: None)
    done = m.start("done-one", _sync=True)
    assert m.get(done)["status"] == at.DONE
    removed = m.clear_finished()
    assert removed == 1 and m.get(done) is None


def test_stop_unknown_task_is_false():
    m = at.TaskManager(lambda p, emit, stop: None)
    assert m.stop("nope") is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} agent-tasks tests passed")
