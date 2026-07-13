"""Tests for the local Ollama agent backend (no Ollama/genai needed)."""
import ollama_agent as oa


def test_parse_stream_line():
    assert oa._parse_stream_line('{"message":{"content":"hi"},"done":false}') == {
        "message": {"content": "hi"}, "done": False}
    assert oa._parse_stream_line(b'{"done":true}') == {"done": True}
    assert oa._parse_stream_line("not json") == {}
    assert oa._parse_stream_line(b"") == {}


def test_resolve_model_picks_preferred_and_first(monkeypatch):
    import local_ai
    monkeypatch.setattr(local_ai, "local_ai_status",
                        lambda: {"ok": True, "running": True, "models": ["llama3.2", "qwen2.5"]})
    assert oa.resolve_model("qwen2.5")["model"] == "qwen2.5"
    assert oa.resolve_model("")["model"] == "llama3.2"   # first installed when none preferred


def test_resolve_model_not_running(monkeypatch):
    import local_ai
    monkeypatch.setattr(local_ai, "local_ai_status",
                        lambda: {"ok": True, "running": False, "note": "Ollama not running."})
    r = oa.resolve_model("")
    assert r["ok"] is False and "Ollama" in r["error"]


def test_resolve_model_running_but_no_models(monkeypatch):
    import local_ai
    monkeypatch.setattr(local_ai, "local_ai_status",
                        lambda: {"ok": True, "running": True, "models": []})
    r = oa.resolve_model("")
    assert r["ok"] is False and "pull" in r["error"].lower()


def test_agent_interface_and_error_path(monkeypatch):
    events = []
    a = oa.OllamaAgent(model_name="")
    a.subscribe(lambda ev: events.append((ev.kind, ev.payload)))
    # Force resolve_model to fail so _run_turn takes the error path synchronously.
    monkeypatch.setattr(oa, "resolve_model", lambda *a, **k: {"ok": False, "error": "no ollama"})
    a._run_turn("hello")
    kinds = [k for k, _ in events]
    assert "error" in kinds and kinds[-1] == "done"
    # reset/stop/subscribe exist and don't raise
    a.reset(); a.stop()
