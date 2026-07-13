"""Tests for the new feature modules: local_ai, macros, creative, security_extras."""
import tempfile
from pathlib import Path

import local_ai
import macros
import creative
import security_extras


def test_macros_crud():
    d = tempfile.mkdtemp(prefix="macros_")
    macros._path = lambda: Path(d) / "m.json"  # isolate from the real user dir
    assert macros.save_macro("morning", "organize Downloads")["ok"]
    names = [m["name"] for m in macros.list_macros()["macros"]]
    assert "morning" in names
    assert macros.get_macro("morning")["task"] == "organize Downloads"
    assert macros.run_macro("morning")["task"] == "organize Downloads"
    assert macros.delete_macro("morning")["ok"]
    assert macros.get_macro("morning")["ok"] is False


def test_macros_validation():
    assert macros.save_macro("", "x")["ok"] is False
    assert macros.save_macro("n", "")["ok"] is False


def test_local_ai_status_graceful():
    r = local_ai.local_ai_status()
    assert r["ok"] and "running" in r


def test_local_ai_ask_graceful():
    assert local_ai.local_ai_ask("")["ok"] is False
    r = local_ai.local_ai_ask("hello")  # no ollama in test env -> graceful error
    assert "ok" in r


def test_creative_graceful_without_key():
    assert creative.generate_image("")["ok"] is False
    assert creative.describe_image("/nope/x.png")["ok"] is False
    assert creative.transcribe_audio("/nope/x.mp3")["ok"] is False


def test_security_checkup():
    r = security_extras.security_checkup()
    assert r["ok"] and "score" in r and "rating" in r and "recommendations" in r


def _run():
    import types
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
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
