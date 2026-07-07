"""Regression test for the agent's model fallback preference.

When the primary Gemini model errors, Ember switches to a fallback. It used to drop to the older
2.5 models (and never listed gemini-3.1-flash-lite at all), so a smarter, newer 'lite' model was
skipped. The fallback chain must now lead with gemini-3.1-flash-lite.

agent.py imports google-genai (absent in CI), so the list is read from source via AST.
Run: python test_model_fallback.py
"""
import ast
import os

_AGENT = open(os.path.join(os.path.dirname(__file__), "agent.py"), encoding="utf-8").read()
_TREE = ast.parse(_AGENT)


def _default_fallbacks():
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.name == "Agent":
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "DEFAULT_FALLBACKS" for t in stmt.targets):
                    return list(ast.literal_eval(stmt.value))
    raise AssertionError("Agent.DEFAULT_FALLBACKS not found")


def test_fallback_leads_with_3_1_flash_lite():
    fb = _default_fallbacks()
    assert fb, "fallback chain is empty"
    assert fb[0] == "gemini-3.1-flash-lite", f"first fallback should be the smart lite model, got {fb[0]}"


def test_3_1_flash_lite_beats_every_older_2_5_model():
    fb = _default_fallbacks()
    i31 = fb.index("gemini-3.1-flash-lite")
    for m in fb:
        if m.startswith("gemini-2.5"):
            assert i31 < fb.index(m), f"{m} must not be tried before gemini-3.1-flash-lite"


def test_default_primary_model_is_3_1_flash_lite():
    # The default model the agent starts on is also the smart lite one.
    assert 'model_name: str = "gemini-3.1-flash-lite"' in _AGENT


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
