"""Hermetic tests for the in-app terminal/code runner core (terminal.py): the persistent Python
REPL and the unified-input classifier. No GUI, no subprocess.
Run: python test_terminal.py"""
import terminal


def test_repl_prints_stdout():
    r = terminal.PyRepl()
    out = r.run("print('hello')")
    assert out["ok"] and out["stdout"].strip() == "hello" and out["stderr"] == ""


def test_repl_echoes_bare_expression():
    r = terminal.PyRepl()
    assert r.run("2 + 3")["stdout"].strip() == "5"
    # statements don't echo
    assert r.run("x = 41")["stdout"].strip() == ""


def test_repl_namespace_persists_between_runs():
    r = terminal.PyRepl()
    r.run("a = 10")
    r.run("b = a * 4")
    assert r.run("a + b")["stdout"].strip() == "50"


def test_repl_underscore_holds_last_value():
    r = terminal.PyRepl()
    r.run("7 * 6")
    assert r.run("_")["stdout"].strip() == "42"


def test_repl_captures_exception_without_raising():
    r = terminal.PyRepl()
    out = r.run("1/0")
    assert out["ok"] is False
    assert "ZeroDivisionError" in out["stderr"]
    assert out["stdout"] == ""


def test_repl_multiline_statements():
    r = terminal.PyRepl()
    out = r.run("for i in range(3):\n    print(i)")
    assert out["ok"] and out["stdout"].split() == ["0", "1", "2"]


def test_repl_reset_clears_namespace():
    r = terminal.PyRepl()
    r.run("z = 99")
    r.reset()
    assert r.run("z")["ok"] is False   # NameError after reset


def test_repl_systemexit_is_swallowed():
    r = terminal.PyRepl()
    out = r.run("raise SystemExit(0)")
    assert out["ok"] is True


def test_classify_and_strip_marker():
    assert terminal.classify("!ls -la") == "shell"
    assert terminal.classify("$pwd") == "shell"
    assert terminal.classify(">>> 1+1") == "python"
    assert terminal.classify("py print(1)") == "python"
    assert terminal.classify("git status") == "shell"   # default
    assert terminal.strip_marker("!ls") == "ls"
    assert terminal.strip_marker(">>> 1+1") == "1+1"
    assert terminal.strip_marker("py print(1)") == "print(1)"
    assert terminal.strip_marker("git status") == "git status"


def test_run_shell_empty_is_safe():
    assert terminal.run_shell("")["ok"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} terminal tests passed")
