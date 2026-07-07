"""Hermetic tests for mcp_setup.py — the one-click 'wire Ember into Claude Desktop' logic.
Only the config-writing/merging is exercised (never the real pip install). HOME is redirected to
a temp dir so no real Claude config is touched.

Run: python test_mcp_setup.py
"""
import json
import os
import sys
import tempfile

import mcp_setup as m


def _with_temp_home():
    d = tempfile.mkdtemp(prefix="mcp-setup-test-")
    os.environ["HOME"] = d
    os.environ["APPDATA"] = d
    os.environ["XDG_CONFIG_HOME"] = os.path.join(d, ".config")
    return d


def test_config_path_per_platform():
    _with_temp_home()
    p = m.claude_desktop_config_path()
    assert p.name == "claude_desktop_config.json"


def test_configure_creates_config_with_ember():
    _with_temp_home()
    ok, where = m.configure_claude_desktop()
    assert ok, where
    data = json.loads(open(where).read())
    assert "ember" in data["mcpServers"]
    ember = data["mcpServers"]["ember"]
    assert ember["command"] == sys.executable          # Ember's own Python (source run)
    assert ember["args"][0].endswith("ember_mcp_server.py")


def test_configure_preserves_existing_keys_and_servers():
    _with_temp_home()
    cfg = m.claude_desktop_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "preferences": {"theme": "dark"},
        "mcpServers": {"other": {"command": "x", "args": []}},
    }))
    ok, where = m.configure_claude_desktop()
    assert ok
    data = json.loads(open(where).read())
    # existing content survives...
    assert data["preferences"] == {"theme": "dark"}
    assert "other" in data["mcpServers"]
    # ...and ember is added alongside
    assert "ember" in data["mcpServers"]


def test_configure_backs_up_corrupt_config():
    _with_temp_home()
    cfg = m.claude_desktop_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ this is not valid json ,,, ")
    ok, where = m.configure_claude_desktop()
    assert ok
    # a .bak was kept, and the new file is valid with ember present
    assert cfg.with_suffix(".json.bak").exists()
    data = json.loads(open(where).read())
    assert "ember" in data["mcpServers"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} mcp setup tests passed")
