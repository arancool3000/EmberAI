"""Regression tests for Ember's self-extension + song identification.

Two layers:
  * Behavioural — self_extend.py and song_id.py are stdlib-only at import, so their logic runs
    for real here: authoring/validating/registering/loading/removing runtime tools, source-edit
    with backup + auto-revert + path-escape guard, and song ID with an injected recorder/backend
    (no mic, no network). All on-disk state is redirected to a throwaway dir.
  * Source/AST — agent.py imports google-genai (blocked in CI) and can't be imported, so the
    wiring is checked structurally: the modules are merged, the runtime registrar + dirty-reinit
    exist and are called, ai_tools are loaded at startup, and safety classifies the code-writing
    tools HIGH (so they hit the approval gate).

Run: python test_self_extend.py
"""
import ast
import os
import tempfile

os.environ["EMBER_SUPPORT_DIR"] = tempfile.mkdtemp(prefix="ember_selfext_test_")

import self_extend as se        # noqa: E402
import song_id                  # noqa: E402

_ORIG_SOURCE_ROOT = se._source_root   # restored after tests that redirect it

_AGENT = open(os.path.join(os.path.dirname(__file__), "agent.py"), encoding="utf-8").read()
_SAFETY = open(os.path.join(os.path.dirname(__file__), "safety.py"), encoding="utf-8").read()
_AGENT_TREE = ast.parse(_AGENT)


# ---- behavioural: runtime tool authoring -------------------------------------------------

def test_author_register_and_run_a_tool():
    reg = {}
    se.set_registrar(lambda decl, fn, ro: reg.__setitem__(decl["name"], (decl, fn, ro)))
    code = ("PARAMETERS={'type':'OBJECT','properties':{'a':{'type':'INTEGER'},"
            "'b':{'type':'INTEGER'}},'required':['a','b']}\n"
            "def add_two(a=0,b=0):\n return {'sum':a+b}\n")
    r = se.create_python_tool("add_two", "add", code, overwrite=True)
    assert r["ok"] and r["live"] and "add_two" in reg
    _decl, fn, _ro = reg["add_two"]
    assert fn(a=2, b=40) == {"ok": True, "sum": 42}      # wrapper adds ok=True
    assert _decl["parameters"]["required"] == ["a", "b"]
    se.set_registrar(None)


def test_broken_tool_is_rejected_and_leaves_no_file():
    r = se.create_python_tool("broken_x", "x", "def broken_x(:\n pass")
    assert not r["ok"] and "syntax" in r["error"].lower()
    assert not (se._tools_dir() / "broken_x.py").exists()


def test_reserved_and_bad_names_rejected():
    assert not se.create_python_tool("run_shell", "x", "def run_shell():\n return 1")["ok"]
    assert not se.create_python_tool("write_file", "x", "def write_file():\n return 1")["ok"]
    assert not se.create_python_tool("X", "x", "def X():\n return 1")["ok"]          # too short/upper
    assert not se.create_python_tool("has space", "x", "def f():\n return 1")["ok"]


def test_authored_tool_that_raises_is_caught():
    reg = {}
    se.set_registrar(lambda decl, fn, ro: reg.__setitem__(decl["name"], fn))
    se.create_python_tool("kaboom", "b", "def kaboom():\n raise ValueError('no')", overwrite=True)
    assert reg["kaboom"]()["ok"] is False       # never raises into the agent loop
    se.set_registrar(None)


def test_load_list_and_remove():
    se.set_registrar(lambda decl, fn, ro: None)
    se.create_python_tool("keeper", "k", "def keeper():\n return {'ok':True}", overwrite=True)
    names = {d["name"] for d in se.load_ai_tools()["declarations"]}
    assert "keeper" in names
    assert "keeper" in {t["name"] for t in se.list_ai_tools()["tools"]}
    assert se.remove_ai_tool("keeper")["ok"]
    assert not se.remove_ai_tool("keeper")["ok"]     # already gone
    se.set_registrar(None)


# ---- behavioural: source editing (isolated to a temp 'source root') ----------------------

def _with_temp_root():
    d = tempfile.mkdtemp(prefix="ember_src_")
    se._source_root = lambda: __import__("pathlib").Path(d)
    return d


def test_source_edit_backup_revert_and_undo():
    import pathlib
    root = _with_temp_root()
    try:
        (pathlib.Path(root) / "probe.py").write_text("VALUE = 1\n")
        assert se.self_edit_source("probe.py", find="VALUE = 1", replace="VALUE = 2")["ok"]
        # a syntax-breaking edit auto-reverts and does NOT clobber the backup
        bad = se.self_edit_source("probe.py", find="VALUE = 2", replace="VALUE = (")
        assert not bad["ok"] and bad.get("reverted")
        assert "VALUE = 2" in (pathlib.Path(root) / "probe.py").read_text()
        # undo restores the ORIGINAL (last good state before the successful edit)
        assert se.self_edit_undo("probe.py")["ok"]
        assert "VALUE = 1" in (pathlib.Path(root) / "probe.py").read_text()
    finally:
        se._source_root = _ORIG_SOURCE_ROOT


def test_source_edit_rejects_paths_outside_ember():
    _with_temp_root()
    assert not se.self_edit_source("../../etc/passwd", new_content="x")["ok"]
    assert not se.read_own_source("/etc/hosts")["ok"]


# ---- behavioural: song ID (injected recorder + backend) ----------------------------------

def test_song_id_found_and_clamped():
    seen = {}
    song_id._RECORDER = lambda seconds, path: (seen.__setitem__("s", seconds) or path)
    song_id._IDENTIFY = lambda wav: {"ok": True, "found": True, "provider": "shazam",
                                     "title": "Yellow", "artist": "Coldplay", "album": "", "url": ""}
    r = song_id.identify_song(12)
    assert r["ok"] and r["found"] and r["title"] == "Yellow" and "Coldplay" in r["message"]
    song_id.identify_song(999); assert seen["s"] == 30      # clamp high
    song_id.identify_song(1);   assert seen["s"] == 3       # clamp low
    song_id._RECORDER = song_id._IDENTIFY = None


def test_song_id_not_found_and_no_backend():
    song_id._RECORDER = lambda seconds, path: path
    song_id._IDENTIFY = lambda wav: {"ok": True, "found": False}
    nf = song_id.identify_song()
    assert nf["ok"] and not nf["found"] and "couldn't recognize" in nf["message"]
    song_id._IDENTIFY = None                                # force real backend probe -> none present
    r = song_id._identify("x.wav")
    assert not r["ok"] and r.get("no_backend")
    song_id._RECORDER = None


# ---- source/AST: agent + safety wiring ---------------------------------------------------

def _agent_method_src(name):
    for n in ast.walk(_AGENT_TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(_AGENT, n)
    raise AssertionError(f"agent.py has no {name}")


def test_modules_merged_into_agent():
    assert "import self_extend" in _AGENT and "import song_id" in _AGENT
    # both appear in the feature merge tuple
    assert "self_extend, song_id" in _AGENT


def test_runtime_registrar_and_startup_load_wired():
    assert "def _register_runtime_tool" in _AGENT
    assert "self_extend.set_registrar(_register_runtime_tool)" in _AGENT
    assert "self_extend.load_ai_tools()" in _AGENT
    assert "_TOOLS_GEN" in _AGENT


def test_new_tools_visible_via_chat_reinit():
    # A tool authored mid-conversation is only callable if the chat is rebuilt (schema is frozen
    # at init). _run_turn must refresh when the generation counter advanced.
    assert "def _refresh_tools_if_dirty" in _AGENT
    run_turn = _agent_method_src("_run_turn")
    assert "self._refresh_tools_if_dirty()" in run_turn
    refresh = _agent_method_src("_refresh_tools_if_dirty")
    assert "_init_chat" in refresh and "_capture_history" in refresh


def test_readonly_tools_merged_for_safety():
    # self_extend's read-only tools (list/read) must reach safety.SAFE_READONLY, scoped to this
    # module (not a blanket reclassification of every feature module).
    assert "safety.SAFE_READONLY |= set(getattr(self_extend" in _AGENT
    assert "read_own_source" in se.READONLY_TOOLS and "list_ai_tools" in se.READONLY_TOOLS


def test_code_writing_tools_are_high_risk():
    # HIGH -> needs_confirmation -> the user approves the code once (approve-first-time-only).
    assert '"create_python_tool", "self_edit_source"' in _SAFETY
    assert 'return "high"' in _SAFETY.split("create_python_tool")[1][:120]


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
