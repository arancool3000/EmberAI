"""Hermetic tests for ollama_council.py — the multi-model collaboration flow. A fake
`complete(model, prompt)` stands in for Ollama, so the whole propose → refine → synthesize
pipeline is verified with no models, no network.

Run: python test_ollama_council.py
"""
import ollama_council as oc


def _recording_completer(script=None):
    """complete() that records calls and returns a per-model canned answer (or echoes phase)."""
    calls = []
    script = script or {}

    def complete(model, prompt):
        calls.append({"model": model, "prompt": prompt})
        if "Final combined answer" in prompt:
            return f"SYNTH by {model}"
        if "improved answer" in prompt or "Your improved answer" in prompt:
            return f"{model}-refined"
        return script.get(model, f"{model}-draft")
    return complete, calls


def test_single_model_is_just_itself():
    complete, calls = _recording_completer()
    r = oc.run_council("hi?", ["m1"], complete, rounds=2)
    assert r["ok"] and r["final"] == "m1-draft"
    assert r["aggregator"] == "m1"
    # no refine/synthesize when there's only one model
    assert [c["model"] for c in calls] == ["m1"]


def test_three_models_propose_refine_synthesize():
    complete, calls = _recording_completer()
    events = []
    r = oc.run_council("q?", ["a", "b", "c"], complete, rounds=1,
                       on_event=lambda phase, m: events.append((phase, m)))
    assert r["ok"]
    # phases: 3 proposes, 3 refines, 1 synthesize
    phases = [p for p, _ in events]
    assert phases.count("propose") == 3
    assert phases.count("refine") == 3
    assert phases.count("synthesize") == 1
    # final comes from the aggregator (first model by default)
    assert r["final"] == "SYNTH by a" and r["aggregator"] == "a"
    # contributions recorded for round 0 (propose) + round 1 (refine)
    rounds = sorted({c["round"] for c in r["contributions"]})
    assert rounds == [0, 1]
    assert len([c for c in r["contributions"] if c["round"] == 0]) == 3


def test_aggregator_choice_respected():
    complete, _ = _recording_completer()
    r = oc.run_council("q?", ["a", "b"], complete, rounds=0, aggregator="b")
    assert r["aggregator"] == "b" and r["final"] == "SYNTH by b"


def test_refine_prompt_includes_peer_drafts():
    p = oc.build_refine_prompt("Q", "my draft", [("peerX", "peer draft text")])
    assert "peerX" in p and "peer draft text" in p and "my draft" in p and "Q" in p


def test_aggregator_prompt_lists_all_answers():
    p = oc.build_aggregator_prompt("Q", [("a", "answer A"), ("b", "answer B")])
    assert "answer A" in p and "answer B" in p and "final" in p.lower()


def test_no_models_errors():
    r = oc.run_council("q", [], lambda m, p: "x")
    assert r["ok"] is False


def test_stop_aborts_midway():
    complete, _ = _recording_completer()
    r = oc.run_council("q", ["a", "b", "c"], complete, stop=lambda: True)
    assert r["ok"] is False and r["error"] == "stopped"


def test_aggregator_empty_falls_back_to_longest_draft():
    def complete(model, prompt):
        if "Final combined answer" in prompt:
            return ""                       # aggregator yields nothing
        if "improved answer" in prompt:
            return "short" if model == "a" else "a much longer refined answer"
        return "d"
    r = oc.run_council("q", ["a", "b"], complete, rounds=1)
    assert r["ok"] and r["final"] == "a much longer refined answer"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ollama council tests passed")
