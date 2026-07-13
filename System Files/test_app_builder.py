"""Regression tests for app_builder — the AI building STANDALONE third-party software.

app_builder is stdlib-only, so its logic runs for real here: building/validating/rejecting/
running/listing/removing standalone apps, and keep_awake picking the right OS power tool. All
on-disk state goes to a throwaway EMBER_APPS_DIR, and the launcher/keep-awake spawns are injected
so nothing is actually executed. The agent + safety wiring is checked via source (agent.py imports
google-genai and can't load in CI).

Run: python test_app_builder.py
"""
import ast
import os
import tempfile
from pathlib import Path

os.environ["EMBER_APPS_DIR"] = tempfile.mkdtemp(prefix="ember_apps_test_")

import app_builder as ab                 # noqa: E402

_HERE = os.path.dirname(__file__)
_AGENT = open(os.path.join(_HERE, "agent.py"), encoding="utf-8").read()
_SAFETY = open(os.path.join(_HERE, "safety.py"), encoding="utf-8").read()
_APPS = Path(os.environ["EMBER_APPS_DIR"])


def test_build_validates_saves_and_makes_a_launcher():
    r = ab.build_app("Hello Timer", "print('tick')\n", kind="python", description="demo")
    assert r["ok"]
    d = Path(r["dir"])
    assert (d / "main.py").exists() and (d / "app.json").exists()
    assert r["launcher"]                       # a double-click launcher was created
    if not os.name == "nt":
        assert os.access(d / "main.py", os.X_OK)   # runnable


def test_broken_program_is_rejected_and_not_left_on_disk():
    bad = ab.build_app("Broken", "def (:\n bad", kind="python")
    assert not bad["ok"] and "error" in bad
    assert not (_APPS / "Broken").exists()     # no half-built software left behind


def test_shell_app_gets_a_shebang_and_is_syntax_checked():
    sh = ab.build_app("Say Hi", "echo hello\n", kind="shell")
    assert sh["ok"] and Path(sh["dir"], "main.sh").read_text().startswith("#!/bin/bash")
    # a syntactically broken shell script is caught by `bash -n` (when bash is present)
    import shutil
    if shutil.which("bash"):
        assert not ab.build_app("BadSh", "if then fi done\n", kind="shell")["ok"]


def test_bad_name_and_kind_rejected():
    assert not ab.build_app("", "print(1)")["ok"]
    assert not ab.build_app("x/y", "print(1)")["ok"]        # illegal chars
    assert not ab.build_app("ok name", "print(1)", kind="ruby")["ok"]


def test_no_overwrite_without_flag():
    ab.build_app("Dup", "print(1)\n", overwrite=True)
    assert not ab.build_app("Dup", "print(2)\n")["ok"]      # exists
    assert ab.build_app("Dup", "print(2)\n", overwrite=True)["ok"]


def test_run_app_uses_the_manifest_run_argv():
    ab.build_app("Runner", "print(1)\n", kind="python", overwrite=True)
    seen = {}
    ab._RUNNER = lambda argv, cwd: (seen.update(argv=argv, cwd=cwd) or 4242)
    try:
        r = ab.run_app("Runner")
        assert r["ok"] and r["pid"] == 4242
        assert seen["argv"] == (["python", "main.py"] if os.name == "nt" else ["python3", "main.py"])
    finally:
        ab._RUNNER = None
    assert not ab.run_app("does-not-exist")["ok"]


def test_list_and_remove():
    ab.build_app("KeepMe", "print(1)\n", overwrite=True)
    assert "KeepMe" in {a["name"] for a in ab.list_apps()["apps"]}
    assert ab.remove_app("KeepMe")["ok"] and not (_APPS / "KeepMe").exists()
    assert not ab.remove_app("KeepMe")["ok"]


def test_keep_awake_uses_the_os_power_tool_and_stops():
    spawned = {}

    class _H:
        pid = 999

        def poll(self):
            return None

    ab._SPAWN = lambda argv: (spawned.update(argv=argv) or _H())
    try:
        r = ab.keep_awake(0)
        assert r["ok"]
        assert spawned["argv"][0] in ("caffeinate", "powershell", "systemd-inhibit")
        assert ab.keep_awake(0).get("already")     # idempotent while running
    finally:
        assert ab.stop_keep_awake()["ok"]
        ab._SPAWN = None


def test_keep_awake_timed_passes_a_duration():
    import sys
    spawned = {}
    ab._SPAWN = lambda argv: (spawned.update(a=argv) or type("H", (), {"pid": 1, "poll": lambda s: None})())
    try:
        ab.keep_awake(30)
        joined = " ".join(spawned["a"])
        if sys.platform == "darwin":
            assert "-t" in spawned["a"] and "1800" in spawned["a"]
        else:
            assert "1800" in joined                # 30 min -> 1800s somewhere in the argv
    finally:
        ab.stop_keep_awake()
        ab._SPAWN = None


# ---- source: agent + safety wiring ------------------------------------------------------

def test_module_merged_and_build_app_is_high_risk():
    assert "import app_builder" in _AGENT
    assert "self_extend, song_id, app_builder" in _AGENT
    assert "app_builder" in _AGENT and 'getattr(app_builder, "READONLY_TOOLS"' in _AGENT
    # build_app writes runnable software -> HIGH -> user approves the code once
    assert '"create_python_tool", "self_edit_source", "build_app"' in _SAFETY


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
