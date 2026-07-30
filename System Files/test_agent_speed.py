"""Tests for turn-level scheduling and caching (agent_speed.py).

Pure logic — no model, no threads, no tools. Runnable:
    pytest test_agent_speed.py
    python test_agent_speed.py
"""
import agent_speed as sp

READONLY = frozenset({"read_file", "list_directory", "grep_files", "take_screenshot"})


class Call:
    """Stands in for a model function call."""
    def __init__(self, name, **args):
        self.name = name
        self.args = args


# --- cache keys ---------------------------------------------------------------

def test_key_is_stable_regardless_of_argument_order():
    assert sp.cache_key("read_file", {"a": 1, "b": 2}) == sp.cache_key("read_file", {"b": 2, "a": 1})


def test_different_args_are_different_keys():
    assert sp.cache_key("read_file", {"p": "a"}) != sp.cache_key("read_file", {"p": "b"})
    assert sp.cache_key("read_file", {}) != sp.cache_key("list_directory", {})


def test_key_survives_unserialisable_arguments():
    assert sp.cache_key("read_file", {"f": object()})      # must not raise


# --- the turn cache -----------------------------------------------------------

def test_a_repeated_read_is_served_from_cache():
    c = sp.TurnCache(READONLY)
    assert c.get("read_file", {"p": "x"}) is None
    c.put("read_file", {"p": "x"}, {"ok": True, "text": "hello"})
    assert c.get("read_file", {"p": "x"})["text"] == "hello"
    assert c.hits == 1


def test_writes_are_never_cached():
    c = sp.TurnCache(READONLY)
    c.put("write_file", {"p": "x"}, {"ok": True})
    assert c.get("write_file", {"p": "x"}) is None


def test_a_mutating_call_invalidates_everything():
    # After a write, a cached read describes a world that no longer exists.
    c = sp.TurnCache(READONLY)
    c.put("read_file", {"p": "x"}, {"ok": True, "text": "before"})
    c.note_call("write_file")
    assert c.get("read_file", {"p": "x"}) is None
    assert c.stats()["invalidations"] == 1


def test_a_read_does_not_invalidate():
    c = sp.TurnCache(READONLY)
    c.put("read_file", {"p": "x"}, {"ok": True, "text": "v"})
    c.note_call("list_directory")
    assert c.get("read_file", {"p": "x"}) is not None


def test_failures_are_not_cached():
    # Pinning a transient error for the rest of the turn would strand the agent.
    c = sp.TurnCache(READONLY)
    c.put("read_file", {"p": "x"}, {"ok": False, "error": "locked"})
    assert c.get("read_file", {"p": "x"}) is None


def test_cached_results_are_copies():
    # The loop mutates results in place; a shared dict would let one round corrupt another.
    c = sp.TurnCache(READONLY)
    c.put("read_file", {"p": "x"}, {"ok": True, "text": "v"})
    got = c.get("read_file", {"p": "x"})
    got["text"] = "tampered"
    assert c.get("read_file", {"p": "x"})["text"] == "v"


def test_non_dict_results_are_ignored():
    c = sp.TurnCache(READONLY)
    c.put("read_file", {"p": "x"}, "not a dict")
    assert c.get("read_file", {"p": "x"}) is None


# --- batch partitioning -------------------------------------------------------

def test_all_reads_form_one_parallel_run():
    runs = sp.partition_batch(["read_file", "read_file", "grep_files"], READONLY)
    assert runs == [(True, [0, 1, 2])]


def test_one_write_no_longer_serialises_the_whole_batch():
    # The actual regression: reads either side of a write used to all run sequentially.
    runs = sp.partition_batch(
        ["read_file", "grep_files", "write_file", "read_file", "list_directory"], READONLY)
    assert runs == [(True, [0, 1]), (False, [2]), (True, [3, 4])]


def test_order_is_preserved_across_a_write():
    # A read issued after a write may be reading back what was written, so it must not be
    # hoisted into the earlier parallel run.
    runs = sp.partition_batch(["read_file", "write_file", "read_file"], READONLY)
    assert [idx for _, idx in runs] == [[0], [1], [2]]


def test_a_lone_read_is_not_worth_a_thread_pool():
    assert sp.partition_batch(["read_file"], READONLY) == [(False, [0])]


def test_all_writes_are_fully_sequential():
    runs = sp.partition_batch(["write_file", "run_shell"], READONLY)
    assert runs == [(False, [0]), (False, [1])]


def test_empty_batch():
    assert sp.partition_batch([], READONLY) == []


def test_plan_reports_what_will_overlap():
    plan = sp.plan_batch(["read_file", "read_file", "write_file"], READONLY)
    assert plan["parallel_calls"] == 2 and plan["sequential_calls"] == 1
    assert plan["rounds"] == 2


# --- end-to-end scheduling ----------------------------------------------------

def _harness(calls, cache=None, stop=None):
    order = []

    def run_one(call):
        order.append(("one", call.name))
        return (call.name, {"ok": True, "who": call.name})

    def run_many(batch):
        order.append(("many", tuple(c.name for c in batch)))
        return [(c.name, {"ok": True, "who": c.name}) for c in batch]

    out = sp.execute_batch([c.name for c in calls], calls, run_one, run_many,
                           READONLY, cache=cache, stop=stop)
    return out, order


def test_reads_go_through_the_parallel_runner():
    calls = [Call("read_file", p="a"), Call("grep_files", q="b")]
    out, order = _harness(calls)
    assert order == [("many", ("read_file", "grep_files"))]
    assert [n for n, _ in out] == ["read_file", "grep_files"]


def test_mixed_batch_keeps_result_order():
    calls = [Call("read_file", p="a"), Call("write_file", p="w"), Call("grep_files", q="c")]
    out, order = _harness(calls)
    assert [n for n, _ in out] == ["read_file", "write_file", "grep_files"]
    assert ("one", "write_file") in order


def test_every_call_produces_exactly_one_result():
    calls = [Call("read_file", p=str(i)) for i in range(4)] + [Call("write_file", p="w")]
    out, _ = _harness(calls)
    assert len(out) == len(calls)


def test_a_duplicate_read_is_not_executed_twice():
    cache = sp.TurnCache(READONLY)
    calls = [Call("read_file", p="same"), Call("write_file", p="w"),
             Call("read_file", p="same")]
    out, order = _harness(calls, cache=cache)
    # The write invalidates, so the second read DOES re-run — correctness over speed.
    assert len(out) == 3
    assert cache.stats()["invalidations"] == 1

    cache2 = sp.TurnCache(READONLY)
    calls2 = [Call("read_file", p="same")]
    _harness(calls2, cache=cache2)
    out2, order2 = _harness([Call("read_file", p="same")], cache=cache2)
    assert cache2.hits == 1                  # second turn-round served from cache
    assert order2 == []                      # nothing executed at all


def test_cache_hit_returns_the_same_payload():
    cache = sp.TurnCache(READONLY)
    _harness([Call("read_file", p="x")], cache=cache)
    out, order = _harness([Call("read_file", p="x")], cache=cache)
    assert out == [("read_file", {"ok": True, "who": "read_file"})]
    assert order == []


def test_stop_flag_halts_the_batch():
    calls = [Call("write_file", p="a"), Call("write_file", p="b")]
    out, _ = _harness(calls, stop=lambda: True)
    assert out == []


def test_scheduling_works_without_a_cache():
    calls = [Call("read_file", p="a"), Call("read_file", p="a")]
    out, _ = _harness(calls, cache=None)
    assert len(out) == 2                     # no memoisation, but still correct


def test_calls_with_no_args_are_handled():
    cache = sp.TurnCache(READONLY)
    out, _ = _harness([Call("take_screenshot")], cache=cache)
    assert len(out) == 1
    out2, order2 = _harness([Call("take_screenshot")], cache=cache)
    assert order2 == []                      # cached on the second call


def _run():
    failures = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in sorted(names):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - failures}/{len(names)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
