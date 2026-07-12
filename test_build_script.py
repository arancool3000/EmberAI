"""Regression tests for the macOS builder's honest progress UI."""
import os
import subprocess


SCRIPT = os.path.join(os.path.dirname(__file__), "BUILD_DESKTOP_APP.command")
SOURCE = open(SCRIPT, encoding="utf-8").read()


def _bash(code: str) -> str:
    return subprocess.run(
        ["bash", "-c", code], capture_output=True, text=True, timeout=20).stdout.strip()


def test_script_is_valid_bash():
    result = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_progress_eta_and_jokes_wrap_current_build_commands():
    assert "run_with_progress()" in SOURCE and "ETA" in SOURCE
    assert "JOKES=(" in SOURCE and SOURCE.count('"') > 30
    assert "el - lastj >= 10" in SOURCE
    assert 'run_with_progress 180 "Installing dependencies" uv pip install' in SOURCE
    assert 'run_with_progress 240 "Building Ember.app" "$PYBIN" -m PyInstaller' in SOURCE
    assert "pct > 99" in SOURCE and "100%" in SOURCE


def test_helpers_compute_time_and_bar():
    helpers = "\n".join(line for line in SOURCE.splitlines()
                        if line.startswith("_secs()") or line.startswith("_repeat()"))
    out = _bash(f'{helpers}\necho "$(_secs 125)|$(_repeat 5 "#")|$(_secs 0)"')
    assert out == "02:05|#####|00:00", out


def test_non_tty_path_preserves_command_exit_code():
    function = SOURCE.split("run_with_progress()", 1)[1].split("\n}", 1)[0]
    helpers = "\n".join(line for line in SOURCE.splitlines()
                        if line.startswith("_secs()") or line.startswith("_repeat()"))
    jokes = SOURCE.split("JOKES=(", 1)[1].split(")", 1)[0]
    program = f'{helpers}\nJOKES=({jokes})\nrun_with_progress() {function}\n}}\n'
    assert _bash(program + 'run_with_progress 5 T bash -c "exit 0"; echo rc=$?').endswith("rc=0")
    assert _bash(program + 'run_with_progress 5 T bash -c "exit 7"; echo rc=$?').endswith("rc=7")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} build-script tests passed")
