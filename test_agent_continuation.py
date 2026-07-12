"""Product guard: tool-driving agents continue to completion without arbitrary round caps."""
from pathlib import Path


ROOT = Path(__file__).parent
FILES = ["agent.py", "claude_agent.py", "openai_agent.py", "ollama_agent.py"]


def test_no_provider_has_a_fixed_tool_round_limit():
    for filename in FILES:
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "[step limit reached" not in source, filename
        assert "[stopped after several tool steps" not in source, filename
        assert "max_steps = 8" not in source, filename
        assert "for _ in range(12)" not in source, filename


def test_every_provider_has_repeat_loop_protection():
    for filename in FILES:
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "repeated_call_rounds" in source, filename
        assert "same operation four times" in source, filename


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} continuation tests passed")
