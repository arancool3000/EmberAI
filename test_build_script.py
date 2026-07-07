"""Regression test for BUILD_DESKTOP_APP.command's build-time progress UI.

The Mac build script shows a live progress bar (with ETA) + a rotating joke every 10s while the
3-6 min PyInstaller build runs in the background. This guards that it stays valid bash and keeps
the progress/joke wiring, and exercises the pure helpers (_secs / _repeat) for real via bash.

Run: python test_build_script.py
"""
import os
import subprocess

_SCRIPT = os.path.join(os.path.dirname(__file__), "BUILD_DESKTOP_APP.command")
_SRC = open(_SCRIPT, encoding="utf-8").read()


def _bash(code: str) -> str:
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=20).stdout.strip()


def test_script_is_valid_bash():
    r = subprocess.run(["bash", "-n", _SCRIPT], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr


def test_progress_bar_eta_and_jokes_are_wired():
    assert "run_with_progress()" in _SRC          # the progress helper exists
    assert "ETA" in _SRC                           # shows an ETA
    assert "JOKES=(" in _SRC and _SRC.count('"') > 30   # a joke list with several entries
    # a NEW joke every 10 seconds
    assert "el - lastj >= 10" in _SRC
    # the long steps actually USE the progress bar (deps install + the PyInstaller build)
    assert "run_with_progress 240 \"Building Ember.app\" python3 -m PyInstaller" in _SRC
    assert "run_with_progress 120 \"Installing dependencies\"" in _SRC
    # progress is capped below 100% until the job truly finishes (never lies about being done)
    assert "pct > 99" in _SRC and "100%" in _SRC


def test_helpers_compute_time_and_bar():
    # Extract just the helper defs and run them, so we test the real code, not a copy.
    helpers = "\n".join(l for l in _SRC.splitlines()
                        if l.startswith("_secs()") or l.startswith("_repeat()"))
    assert "_secs()" in helpers and "_repeat()" in helpers
    out = _bash(f'{helpers}\n echo "$(_secs 125)|$(_repeat 5 "#")|$(_secs 0)"')
    assert out == "02:05|#####|00:00", out


def test_command_exit_code_is_preserved_off_tty():
    # Not a TTY (piped) -> it must still wait and return the command's real exit code, so a failed
    # build isn't silently treated as success.
    fn = _SRC.split("run_with_progress()")[1].split("\n}", 1)[0]
    helpers = "\n".join(l for l in _SRC.splitlines()
                        if l.startswith("_secs()") or l.startswith("_repeat()"))
    jokes = _SRC.split("JOKES=(", 1)[1].split(")", 1)[0]
    prog = f'{helpers}\nJOKES=({jokes})\nrun_with_progress() {fn}\n}}\n'
    assert _bash(prog + 'run_with_progress 5 T bash -c "exit 0"; echo rc=$?') .endswith("rc=0")
    assert _bash(prog + 'run_with_progress 5 T bash -c "exit 7"; echo rc=$?') .endswith("rc=7")


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
