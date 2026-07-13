"""Source-level guards that Ember surfaces its thinking process.

agent.py needs google-genai and ui.py needs PyQt6 (both absent in CI), so these read source.
Run: python test_thinking.py
"""
import os

_D = os.path.dirname(__file__)
AGENT = open(os.path.join(_D, "agent.py"), encoding="utf-8").read()
UI = open(os.path.join(_D, "ui.py"), encoding="utf-8").read()
STYLES = open(os.path.join(_D, "styles.py"), encoding="utf-8").read()


def test_agent_requests_and_separates_thought_summaries():
    assert "include_thoughts=True" in AGENT                 # ask Gemini for reasoning summaries
    assert "thought_parts" in AGENT                         # kept separate from the answer
    assert 'getattr(part, "thought", False)' in AGENT
    assert 'AgentEvent("thinking"' in AGENT                 # emitted as its own event


def test_ui_shows_thinking_collapsibly_and_respects_the_setting():
    assert "def _add_thinking" in UI
    assert 'ev.kind == "thinking"' in UI                    # routed in the event dispatcher
    assert 'self.settings.get("show_thinking"' in UI        # honoured
    assert '"show_thinking": True' in UI                    # default on
    assert "show_thinking_check" in UI                      # settings toggle exists
    assert 'self.settings["show_thinking"]' in UI           # persisted on save
    assert "QFrame#bubbleThinking" in STYLES                # styled


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print("  ok ", t.__name__)
        passed += 1
    print(f"\n{passed}/{len(tests)} thinking tests passed")
