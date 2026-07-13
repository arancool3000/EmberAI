"""Tests for the Ember plugin system (plugin_system.py)."""
from pathlib import Path

import pytest

import plugin_system as plugins


GOOD_PLUGIN = '''\
def good_tool(x=""):
    return {"ok": True, "echo": x}

def writer_tool(x=""):
    return {"ok": True, "wrote": x}

EMBER_TOOLS = [
    {
        "name": "good_tool",
        "description": "Echo x back.",
        "parameters": {"type": "OBJECT",
            "properties": {"x": {"type": "STRING"}}, "required": []},
        "handler": good_tool,
        "read_only": True,
    },
    {
        "name": "writer_tool",
        "description": "A non read-only tool.",
        "parameters": {"type": "OBJECT",
            "properties": {"x": {"type": "STRING"}}, "required": []},
        "handler": writer_tool,
        # no read_only -> defaults to medium-risk
    },
]
'''

BROKEN_PLUGIN = '''\
raise RuntimeError("boom — this plugin is intentionally broken")

EMBER_TOOLS = []
'''


@pytest.fixture
def tmp_plugins(tmp_path, monkeypatch):
    """Point PLUGINS_DIR at an empty tmp dir for the duration of a test."""
    monkeypatch.setattr(plugins, "PLUGINS_DIR", tmp_path)
    return tmp_path


def test_loads_good_skips_broken(tmp_plugins):
    (tmp_plugins / "good.py").write_text(GOOD_PLUGIN)
    (tmp_plugins / "broken.py").write_text(BROKEN_PLUGIN)

    summary = plugins.load_plugins()  # must not crash

    loaded_names = {item["name"] for item in summary["loaded"]}
    assert "good_tool" in loaded_names
    assert "writer_tool" in loaded_names

    # broken plugin recorded under errors, by filename
    err_files = {e["plugin"] for e in summary["errors"]}
    assert "broken.py" in err_files
    assert summary["ok"] is True


def test_declarations_are_clean_and_dispatch_callable(tmp_plugins):
    (tmp_plugins / "good.py").write_text(GOOD_PLUGIN)
    summary = plugins.load_plugins()

    for decl in summary["declarations"]:
        assert set(decl.keys()) == {"name", "description", "parameters"}
        assert "handler" not in decl
        assert "read_only" not in decl

    handler = summary["dispatch"]["good_tool"]
    assert callable(handler)
    result = handler(x="hi")
    assert result["ok"] is True and result["echo"] == "hi"


def test_read_only_names(tmp_plugins):
    (tmp_plugins / "good.py").write_text(GOOD_PLUGIN)
    summary = plugins.load_plugins()
    assert "good_tool" in summary["read_only_names"]
    assert "writer_tool" not in summary["read_only_names"]


def test_invalid_entry_skipped(tmp_plugins):
    bad = '''\
EMBER_TOOLS = [
    {"name": "missing_handler", "description": "d",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "not_callable", "description": "d",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []},
     "handler": "nope"},
]
'''
    (tmp_plugins / "bad.py").write_text(bad)
    summary = plugins.load_plugins()
    assert summary["dispatch"] == {}
    assert len(summary["errors"]) >= 2


def test_duplicate_names_skipped(tmp_plugins):
    p = '''\
def t(): return {"ok": True}
EMBER_TOOLS = [
    {"name": "dup", "description": "first",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}, "handler": t},
    {"name": "dup", "description": "second",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}, "handler": t},
]
'''
    (tmp_plugins / "dups.py").write_text(p)
    summary = plugins.load_plugins()
    names = [i["name"] for i in summary["loaded"]]
    assert names.count("dup") == 1
    assert any("duplicate" in e["error"].lower() for e in summary["errors"])


def test_skips_underscore_and_init(tmp_plugins):
    (tmp_plugins / "__init__.py").write_text("EMBER_TOOLS = []")
    (tmp_plugins / "_disabled.py").write_text(GOOD_PLUGIN)
    summary = plugins.load_plugins()
    assert summary["loaded"] == []
    assert summary["errors"] == []


def test_list_plugins(tmp_plugins):
    (tmp_plugins / "good.py").write_text(GOOD_PLUGIN)
    out = plugins.list_plugins()
    assert out["ok"] is True
    assert out["tool_count"] == 2
    files = {p["file"] for p in out["plugins"]}
    assert "good.py" in files


def test_reload_plugins(tmp_plugins):
    (tmp_plugins / "good.py").write_text(GOOD_PLUGIN)
    out = plugins.reload_plugins()
    assert out["ok"] is True
    assert out["loaded"] == 2
    assert "good_tool" in out["tools"]


def test_create_plugin_template_then_loads(tmp_plugins):
    out = plugins.create_plugin_template("My Cool Plugin")
    assert out["ok"] is True
    path = Path(out["path"])
    assert path.exists()

    summary = plugins.load_plugins()
    names = {i["name"] for i in summary["loaded"]}
    assert "my_cool_plugin_demo" in names

    handler = summary["dispatch"]["my_cool_plugin_demo"]
    assert handler(text="ping")["ok"] is True

    # calling again with the same name refuses
    again = plugins.create_plugin_template("My Cool Plugin")
    assert again["ok"] is False
    assert "exist" in again["error"].lower()


def test_shipped_example_hello():
    """Point PLUGINS_DIR at the real shipped folder; example_hello must load and run."""
    real_dir = Path(plugins.__file__).parent / "plugins"
    import importlib
    # use monkeypatch-free direct set + restore to avoid touching other tests
    orig = plugins.PLUGINS_DIR
    try:
        plugins.PLUGINS_DIR = real_dir
        summary = plugins.load_plugins()
        names = {i["name"] for i in summary["loaded"]}
        assert "hello_plugin" in names
        handler = summary["dispatch"]["hello_plugin"]
        result = handler(who="Ada")
        assert result["ok"] is True
        assert "Ada" in result["message"]
    finally:
        plugins.PLUGINS_DIR = orig


def test_exports_consistent():
    """The exported dispatch and declarations must agree (host wiring sanity)."""
    assert set(plugins.TOOL_DISPATCH) == {d["name"] for d in plugins.TOOL_DECLARATIONS}
    assert plugins.READONLY_TOOLS == {"list_plugins"}
    assert plugins.INTERACTION_TOOLS == {"reload_plugins", "create_plugin_template"}
