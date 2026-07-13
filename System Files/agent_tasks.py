"""Parallel agent tasks — run several Ember jobs at once and track them in one dashboard
(beats Nimbalyst's single-track flow: kick off N tasks, watch their status + streamed output
side by side, stop any of them).

`TaskManager` is a PURE, dependency-light coordinator: the actual work is done by an injected
`runner(prompt, emit, stop_event)` callable, so the whole lifecycle (start → stream → done /
error / stopped) is unit-tested with a fake runner — no real agent, no GUI, no network. The UI
provides a runner that drives a real Ember agent.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

RUNNING = "running"
DONE = "done"
ERROR = "error"
STOPPED = "stopped"


class TaskManager:
    """Tracks parallel jobs. `runner(prompt, emit, stop_event) -> final_text|None` does the work:
    call emit(str) to stream output, check stop_event.is_set() to abort early, return the final
    text (or raise to mark the task errored)."""

    def __init__(self, runner: Callable[[str, Callable[[str], None], threading.Event], Optional[str]]):
        self._runner = runner
        self._tasks: dict = {}
        self._order: list = []
        self._counter = 0
        self._lock = threading.RLock()

    def start(self, prompt: str, label: str = "", _sync: bool = False) -> str:
        """Begin a task and return its id. `_sync=True` runs it inline (used by tests for
        determinism); otherwise it runs on a daemon thread."""
        prompt = (prompt or "").strip()
        with self._lock:
            self._counter += 1
            tid = f"task{self._counter}"
            self._tasks[tid] = {
                "id": tid, "prompt": prompt, "label": (label or prompt[:40] or tid),
                "status": RUNNING, "output": "", "error": "",
                "stop": threading.Event(),
            }
            self._order.append(tid)
        if _sync:
            self._run(tid)
        else:
            threading.Thread(target=self._run, args=(tid,), daemon=True).start()
        return tid

    def _run(self, tid: str) -> None:
        t = self._tasks[tid]
        stop = t["stop"]

        def emit(s: str) -> None:
            if not s:
                return
            with self._lock:
                t["output"] += str(s)

        try:
            result = self._runner(t["prompt"], emit, stop)
            with self._lock:
                if t["status"] == RUNNING:
                    if result and not t["output"]:
                        t["output"] = str(result)
                    t["status"] = STOPPED if stop.is_set() else DONE
        except Exception as e:
            with self._lock:
                t["status"] = ERROR
                t["error"] = f"{type(e).__name__}: {e}"

    def stop(self, tid: str) -> bool:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return False
            t["stop"].set()
            if t["status"] == RUNNING:
                t["status"] = STOPPED
            return True

    def stop_all(self) -> int:
        with self._lock:
            ids = list(self._tasks)
        return sum(1 for tid in ids if self.stop(tid))

    def _snapshot(self, t: dict) -> dict:
        return {"id": t["id"], "prompt": t["prompt"], "label": t["label"],
                "status": t["status"], "output": t["output"], "error": t["error"]}

    def get(self, tid: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(tid)
            return self._snapshot(t) if t else None

    def list(self) -> list:
        with self._lock:
            return [self._snapshot(self._tasks[tid]) for tid in self._order]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values() if t["status"] == RUNNING)

    def clear_finished(self) -> int:
        """Drop completed/errored/stopped tasks; keep running ones. Returns how many removed."""
        with self._lock:
            keep = [tid for tid in self._order if self._tasks[tid]["status"] == RUNNING]
            removed = len(self._order) - len(keep)
            self._tasks = {tid: self._tasks[tid] for tid in keep}
            self._order = keep
            return removed
