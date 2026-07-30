"""Turn-level scheduling and caching for the agent loop.

Two things made a turn slower than it needed to be, and both are decided *before* any tool
runs, so they live here as pure functions the loop calls into rather than as logic tangled
into the executor.

**Mixed batches serialised completely.** The loop ran a batch concurrently only when every
call in it was read-only. One write in a batch of six reads dropped the whole round back to
sequential, which is the common shape — the model reads several files and writes one.
:func:`partition_batch` splits a batch into runs instead, so the reads still overlap.

**Identical reads re-executed.** Models re-read the same file within a turn constantly.
:class:`TurnCache` returns the earlier result instead, and — importantly — drops everything
the moment a mutating tool runs, because after a write the cached read is a lie.

Both are pure and unit-tested (``test_agent_speed.py``); the agent supplies the read-only
tool set so there is one source of truth for what is safe.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple


def cache_key(name: str, args: Optional[dict]) -> str:
    """Stable key for a tool call. Argument order must not create a cache miss."""
    try:
        payload = json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        payload = repr(args)
    return f"{name}\x00{payload}"


class TurnCache:
    """Memoises read-only tool results for the duration of one turn.

    Invalidation is deliberately blunt: *any* mutating call clears everything. Tracking
    which read a given write invalidates would mean modelling every tool's side effects,
    and getting that wrong means serving stale data — far worse than re-reading a file.
    """

    def __init__(self, readonly: Iterable[str]):
        self._readonly = frozenset(readonly)
        self._store: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self.invalidations = 0

    def cacheable(self, name: str) -> bool:
        return name in self._readonly

    def get(self, name: str, args: Optional[dict]):
        """Cached result, or None. A miss and an uncacheable call look the same to callers."""
        if not self.cacheable(name):
            return None
        hit = self._store.get(cache_key(name, args))
        if hit is not None:
            self.hits += 1
            # Returned as a copy: the loop mutates results (compaction, image stripping),
            # and a shared dict would let one round corrupt a later round's cached value.
            return dict(hit)
        self.misses += 1
        return None

    def put(self, name: str, args: Optional[dict], result: Any) -> None:
        if not self.cacheable(name) or not isinstance(result, dict):
            return
        # Never cache a failure: the next attempt may well succeed, and pinning an error
        # for the rest of the turn would strand the agent on a transient fault.
        if result.get("ok") is False:
            return
        self._store[cache_key(name, args)] = dict(result)

    def note_call(self, name: str) -> None:
        """Record that a call ran; clears the cache if it could have changed the world."""
        if not self.cacheable(name):
            self.invalidate()

    def invalidate(self) -> None:
        if self._store:
            self.invalidations += 1
        self._store.clear()

    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses,
                "invalidations": self.invalidations, "entries": len(self._store)}


def partition_batch(names: Sequence[str], readonly: Iterable[str]) -> List[Tuple[bool, List[int]]]:
    """Split a batch into consecutive runs of ``(parallel_ok, [indices])``.

    Order is preserved: a read that the model issued *after* a write still runs after it,
    because the model may well have been reading back what it just wrote. Only adjacent
    read-only calls are grouped, and a run of one is not worth a thread pool.
    """
    readonly = frozenset(readonly)
    runs: List[Tuple[bool, List[int]]] = []
    for i, name in enumerate(names):
        safe = name in readonly
        if runs and runs[-1][0] == safe and safe:
            runs[-1][1].append(i)
        else:
            runs.append((safe, [i]))
    # A single-item "parallel" run is really a sequential one.
    return [(safe and len(idx) > 1, idx) for safe, idx in runs]


def plan_batch(names: Sequence[str], readonly: Iterable[str]) -> dict:
    """Describe how a batch will run — used for the speed note shown to the user."""
    runs = partition_batch(names, readonly)
    parallel = sum(len(idx) for ok, idx in runs if ok)
    return {"runs": runs, "parallel_calls": parallel, "sequential_calls": len(names) - parallel,
            "rounds": len(runs)}


def execute_batch(names: Sequence[str], calls: Sequence[Any],
                  run_one: Callable[[Any], Tuple[str, dict]],
                  run_many: Callable[[Sequence[Any]], List[Tuple[str, dict]]],
                  readonly: Iterable[str],
                  cache: Optional[TurnCache] = None,
                  stop: Optional[Callable[[], bool]] = None) -> List[Tuple[str, dict]]:
    """Run a batch with the partitioning and cache applied, preserving call order.

    ``run_one``/``run_many`` are injected so this stays free of Qt, threads, and the
    agent's own state, which is what makes the scheduling testable.
    """
    results: List[Optional[Tuple[str, dict]]] = [None] * len(calls)
    for parallel_ok, indices in partition_batch(names, readonly):
        if stop is not None and stop():
            break
        pending = []
        for i in indices:
            cached = cache.get(names[i], _args_of(calls[i])) if cache else None
            if cached is not None:
                results[i] = (names[i], cached)
            else:
                pending.append(i)
        if not pending:
            continue
        if parallel_ok and len(pending) > 1:
            for i, out in zip(pending, run_many([calls[i] for i in pending])):
                results[i] = out
        else:
            for i in pending:
                if stop is not None and stop():
                    break
                results[i] = run_one(calls[i])
        if cache:
            for i in pending:
                cache.note_call(names[i])
                if results[i] is not None:
                    cache.put(names[i], _args_of(calls[i]), results[i][1])
    return [r for r in results if r is not None]


def _args_of(call: Any) -> dict:
    args = getattr(call, "args", None)
    if isinstance(args, dict):
        return args
    try:
        return dict(args) if args else {}
    except Exception:
        return {}
