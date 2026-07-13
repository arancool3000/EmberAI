"""Hermetic tests for octopus.py — Octopus Mode's cross-provider deliberation. No network and no
LLMs: a fake `complete(tentacle_key, prompt)` scripts each phase by looking at the prompt, so the
reach → discuss → head flow, head selection, discovery, formatting and resilience are all tested
directly. Run: python test_octopus.py"""
import octopus


# ---- fakes -------------------------------------------------------------------

def _phase_of(prompt: str) -> str:
    if "Final synthesized answer" in prompt:
        return "head"
    if "Your improved answer" in prompt:
        return "discuss"
    return "reach"


def _scripted(reply=None):
    """complete() that returns a per-phase reply and records (key, phase, prompt)."""
    calls = []

    def complete(key, prompt):
        phase = _phase_of(prompt)
        calls.append((key, phase))
        if reply:
            r = reply(key, phase, prompt)
            if r is not None:
                return r
        prov = key.split(":")[0]
        if phase == "head":
            return f"HEAD({prov}): the synthesized answer."
        if phase == "discuss":
            return f"{prov}-improved"
        return f"{prov}-draft"

    complete.calls = calls
    return complete


def _tent(provider, model="m", label=None):
    return {"key": f"{provider}:{model}", "provider": provider, "model": model,
            "label": label or f"{provider.title()} {model}"}


# ---- prompts -----------------------------------------------------------------

def test_prompts_frame_each_phase():
    assert "INDEPENDENT" in octopus.build_reach_prompt("Q")
    assert "Conversation so far" in octopus.build_reach_prompt("Q", history="hi")
    d = octopus.build_discuss_prompt("Q", "mine", [("Peer", "theirs")])
    assert "octopus" in d.lower() and "theirs" in d and "Your improved answer" in d
    h = octopus.build_head_prompt("Q", [("A", "x"), ("B", "y")])
    assert "HEAD" in h and "Tentacle 1 (A)" in h and "Final synthesized answer" in h


# ---- run_octopus core --------------------------------------------------------

def test_reach_discuss_head_flow_and_events():
    tents = [_tent("gemini"), _tent("openai"), _tent("ollama")]
    events = []
    c = _scripted()
    r = octopus.run_octopus("Q", tents, c, rounds=1, on_event=lambda p, l: events.append(p))
    assert r["ok"] and len(r["tentacles"]) == 3
    # 3 reach + 3 discuss + 1 head = 7 calls
    phases = [p for _k, p in c.calls]
    assert phases.count("reach") == 3 and phases.count("discuss") == 3 and phases.count("head") == 1
    assert events == ["reach", "reach", "reach", "discuss", "discuss", "discuss", "head"]
    assert r["answer"].startswith("HEAD(")
    # each tentacle keeps both its first reach and its post-discussion final
    t0 = r["tentacles"][0]
    assert t0["reach"].endswith("-draft") and t0["final"].endswith("-improved")


def test_head_prefers_strongest_vendor():
    # claude > openai > gemini > ollama, regardless of order.
    tents = [_tent("ollama"), _tent("gemini"), _tent("claude"), _tent("openai")]
    r = octopus.run_octopus("Q", tents, _scripted())
    assert r["head"]["provider"] if False else r["head"]["key"].startswith("claude:"), r["head"]


def test_explicit_head_override():
    tents = [_tent("claude"), _tent("gemini", label="Gem")]
    r = octopus.run_octopus("Q", tents, _scripted(), head="gemini:m")
    assert r["head"]["key"] == "gemini:m"


def test_rounds_zero_skips_discussion_but_still_synthesizes():
    tents = [_tent("gemini"), _tent("openai")]
    c = _scripted()
    r = octopus.run_octopus("Q", tents, c, rounds=0)
    phases = [p for _k, p in c.calls]
    assert "discuss" not in phases and phases.count("head") == 1
    # finals fall back to the reach drafts (no discussion happened)
    assert all(t["final"] == t["reach"] for t in r["tentacles"])
    assert r["ok"]


def test_single_tentacle_is_just_itself():
    c = _scripted()
    r = octopus.run_octopus("Q", [_tent("gemini")], c, rounds=2)
    phases = [p for _k, p in c.calls]
    assert phases == ["reach"]                 # no discuss, no head call for a lone arm
    assert r["ok"] and r["answer"] == "gemini-draft"
    assert r["head"]["key"] == "gemini:m"


def test_dead_tentacles_are_dropped_but_octopus_still_answers():
    def reply(key, phase, prompt):
        if key.startswith("openai"):
            return ""          # this arm is dead (bad key / offline)
        return None
    tents = [_tent("openai"), _tent("gemini"), _tent("claude")]
    r = octopus.run_octopus("Q", tents, _scripted(reply))
    provs = {t["provider"] for t in r["tentacles"]}
    assert r["ok"] and provs == {"gemini", "claude"}     # openai dropped
    assert r["head"]["key"].startswith("claude:")        # head still the strongest LIVE arm


def test_all_dead_fails_honestly():
    tents = [_tent("gemini"), _tent("openai")]
    r = octopus.run_octopus("Q", tents, lambda k, p: "")
    assert not r["ok"] and "empty" in r["error"].lower()


def test_no_tentacles_fails():
    r = octopus.run_octopus("Q", [], _scripted())
    assert not r["ok"] and "no ais" in r["error"].lower()


def test_head_empty_falls_back_to_fullest_draft():
    def reply(key, phase, prompt):
        if phase == "head":
            return ""          # head produced nothing
        if phase == "discuss":
            return "short" if key.startswith("gemini") else "a much longer improved answer here"
        return "d"
    tents = [_tent("gemini"), _tent("openai")]
    r = octopus.run_octopus("Q", tents, _scripted(reply))
    assert r["ok"] and r["answer"] == "a much longer improved answer here"


def test_stop_cancels_mid_run():
    tents = [_tent("gemini"), _tent("openai")]
    r = octopus.run_octopus("Q", tents, _scripted(), stop=lambda: True)
    assert not r["ok"] and r["error"] == "stopped"


# ---- formatting --------------------------------------------------------------

def test_format_result_is_tentacle_structured():
    tents = [_tent("claude", label="Claude Opus"), _tent("gemini", label="Gemini Flash")]
    r = octopus.run_octopus("Q", tents, _scripted())
    f = r["formatted"]
    assert f.startswith("🐙 **Octopus Mode** — 2 tentacles")
    assert "🧠 **Head — Claude Opus" in f
    assert "🐙①  **Claude Opus**" in f and "🐙②  **Gemini Flash**" in f


def test_short_trims_on_boundary():
    long = "First sentence is complete. " + "word " * 200
    s = octopus._short(long, n=60)
    assert len(s) <= 61 and s.startswith("First sentence is complete.")


# ---- discovery ---------------------------------------------------------------

def test_discover_empty_when_nothing_connected():
    assert octopus.discover_tentacles({}) == []


def test_discover_gemini_only_fills_with_free_gemini_minds():
    d = octopus.discover_tentacles({"gemini_api_key": "k"})
    assert 2 <= len(d) <= octopus.TENTACLE_MAX
    assert all(t["provider"] == "gemini" for t in d)
    assert d[0]["model"] == "gemini-3.5-flash"           # the strong flash leads
    assert len({t["key"] for t in d}) == len(d)          # no duplicates


def test_discover_one_tentacle_per_paid_provider():
    d = octopus.discover_tentacles(
        {"gemini_api_key": "g", "anthropic_api_key": "a", "openai_api_key": "o"},
        ollama_models=["llama3.1", "qwen2.5"])
    provs = [t["provider"] for t in d]
    assert provs.count("claude") == 1 and provs.count("openai") == 1   # paid never multiplied
    assert "ollama" in provs and "gemini" in provs
    assert len(d) <= octopus.TENTACLE_MAX


def test_discover_respects_max_arms():
    d = octopus.discover_tentacles({"gemini_api_key": "k"}, max_arms=3)
    assert len(d) == 3


def test_discover_uses_configured_claude_and_openai_models():
    d = octopus.discover_tentacles(
        {"anthropic_api_key": "a", "anthropic_model": "claude-sonnet-4-6",
         "openai_api_key": "o", "model_id": "gpt-5.1"})
    by_prov = {t["provider"]: t for t in d}
    assert by_prov["claude"]["model"] == "claude-sonnet-4-6"
    assert by_prov["openai"]["model"] == "gpt-5.1"        # honours the user's chosen OpenAI model


# ---- real completer routing (SDKs absent -> graceful "") ---------------------

def test_default_completer_routes_by_prefix(monkeypatched=None):
    seen = {}

    def _gc(s, m, p):
        seen["gemini"] = (m, p)
        return "g"

    def _cc(s, m, p, mt):
        seen["claude"] = (m, p)
        return "c"

    def _oc(s, m, p, mt):
        seen["openai"] = (m, p)
        return "o"

    octopus._gemini_complete, octopus._claude_complete, octopus._openai_complete = _gc, _cc, _oc
    try:
        c = octopus.default_completer({})
        assert c("gemini:gemini-3.5-flash", "hi") == "g"
        assert c("claude:claude-opus-4-8", "hi") == "c"
        assert c("openai:gpt-4o-mini", "hi") == "o"
        assert seen["gemini"][0] == "gemini-3.5-flash"
    finally:
        import importlib
        importlib.reload(octopus)


def test_default_completer_swallows_provider_errors():
    import importlib
    importlib.reload(octopus)
    def boom(*a, **k):
        raise RuntimeError("no SDK / bad key")
    octopus._gemini_complete = boom
    try:
        c = octopus.default_completer({"gemini_api_key": "k"})
        assert c("gemini:x", "hi") == ""     # a dead arm returns "" instead of raising
    finally:
        importlib.reload(octopus)


# ---- tool layer --------------------------------------------------------------

def test_octopus_discuss_requires_a_question():
    r = octopus.octopus_discuss("   ")
    assert not r["ok"] and "question" in r["error"].lower()


def test_octopus_discuss_reports_when_nothing_connected(monkeypatch=None):
    orig_load, orig_oll = octopus._load_settings, octopus._live_ollama_models
    octopus._load_settings = lambda: {}
    octopus._live_ollama_models = lambda s: []
    try:
        r = octopus.octopus_discuss("hello")
        assert not r["ok"] and "connect" in r["error"].lower()
    finally:
        octopus._load_settings, octopus._live_ollama_models = orig_load, orig_oll


def test_octopus_discuss_runs_end_to_end_with_fakes():
    orig = (octopus._load_settings, octopus._live_ollama_models, octopus.default_completer)
    octopus._load_settings = lambda: {"gemini_api_key": "g", "anthropic_api_key": "a"}
    octopus._live_ollama_models = lambda s: []
    octopus.default_completer = lambda s, **k: _scripted()
    try:
        r = octopus.octopus_discuss("What is 2+2?", rounds=1)
        assert r["ok"] and r["answer"].startswith("HEAD(")
        assert "🐙 **Octopus Mode**" in r["formatted"]
        assert r["head"]["key"].startswith("claude:")     # claude is the head
    finally:
        (octopus._load_settings, octopus._live_ollama_models, octopus.default_completer) = orig


def test_octopus_discuss_notes_single_arm():
    orig = (octopus._load_settings, octopus._live_ollama_models, octopus.default_completer)
    octopus._load_settings = lambda: {"anthropic_api_key": "a"}   # one provider, but discovery
    octopus._live_ollama_models = lambda s: []                    # gives a single claude arm
    octopus.default_completer = lambda s, **k: _scripted()
    try:
        r = octopus.octopus_discuss("hi")
        assert r["ok"] and "note" in r and "one" in r["note"].lower()
    finally:
        (octopus._load_settings, octopus._live_ollama_models, octopus.default_completer) = orig


def test_octopus_status_lists_available_tentacles():
    orig = (octopus._load_settings, octopus._live_ollama_models)
    octopus._load_settings = lambda: {"gemini_api_key": "g"}
    octopus._live_ollama_models = lambda s: []
    try:
        r = octopus.octopus_status()
        assert r["ok"] and r["count"] >= 2 and r["tentacles"]
    finally:
        (octopus._load_settings, octopus._live_ollama_models) = orig


def test_tool_exports_consistent():
    assert set(octopus.TOOL_DISPATCH) == {d["name"] for d in octopus.TOOL_DECLARATIONS}
    assert octopus.READONLY_TOOLS <= set(octopus.TOOL_DISPATCH)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} octopus tests passed")
