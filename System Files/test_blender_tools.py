"""Hermetic tests for blender_tools.py — the Blender MCP addon link. No Blender, no GUI:
a fake addon socket server speaks the blender-mcp wire protocol on an ephemeral loopback
port. Run: pytest test_blender_tools.py"""
import json
import os
import socket
import threading
import time

import blender_tools as bt
import safety


# ---- a tiny fake blender-mcp addon ------------------------------------------------------

class FakeAddon:
    """Speaks the addon's protocol: read until the buffer parses as JSON, send one JSON reply."""

    def __init__(self, chunked=False):
        self.chunked = chunked
        self.requests = []
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(4)
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                buf = b""
                while True:
                    data = conn.recv(8192)
                    if not data:
                        break
                    buf += data
                    try:
                        req = json.loads(buf.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self.requests.append(req)
                    body = json.dumps(self._respond(req)).encode("utf-8")
                    if self.chunked:
                        conn.sendall(body[:10])
                        time.sleep(0.05)
                        conn.sendall(body[10:])
                    else:
                        conn.sendall(body)
                    break

    def _respond(self, req):
        t, params = req.get("type"), req.get("params") or {}
        if t == "get_scene_info":
            return {"status": "success",
                    "result": {"name": "Scene", "object_count": 2,
                               "objects": [{"name": "Cube", "type": "MESH"},
                                           {"name": "Light", "type": "LIGHT"}]}}
        if t == "get_object_info":
            if params.get("name") != "Cube":
                return {"status": "error", "message": f"Object not found: {params.get('name')}"}
            return {"status": "success", "result": {"name": "Cube", "type": "MESH",
                                                    "location": [0, 0, 0]}}
        if t == "execute_code":
            return {"status": "success", "result": {"executed": True, "result": "done"}}
        if t == "get_viewport_screenshot":
            with open(params["filepath"], "wb") as f:   # pretend Blender wrote the PNG
                f.write(b"\x89PNG fake")
            return {"status": "success", "result": {"width": 800, "height": 600}}
        return {"status": "error", "message": f"unknown command {t}"}

    def close(self):
        self._stop = True
        try:
            self.srv.close()
        except OSError:
            pass


def _point_at(monkeypatch, port):
    monkeypatch.setenv("BLENDER_HOST", "127.0.0.1")
    monkeypatch.setenv("BLENDER_PORT", str(port))


def _dead_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---- wiring ------------------------------------------------------------------------------

def test_declarations_match_dispatch():
    decl_names = [d["name"] for d in bt.TOOL_DECLARATIONS]
    assert decl_names == list(bt.TOOL_DISPATCH)
    for d in bt.TOOL_DECLARATIONS:
        assert d["description"]
        assert d["parameters"]["type"] == "OBJECT"
        for req in d["parameters"].get("required", []):
            assert req in d["parameters"]["properties"]


def test_readonly_set_sane():
    assert bt.READONLY_TOOLS <= set(bt.TOOL_DISPATCH)
    assert "blender_run_python" not in bt.READONLY_TOOLS


def test_run_python_is_high_risk():
    risk, _ = safety.classify("blender_run_python", {"code": "import bpy"})
    assert risk == "high"
    assert safety.needs_confirmation(risk)


def test_readonly_tools_classified_safe():
    for name in bt.READONLY_TOOLS:
        risk, _ = safety.classify(name, {})
        assert risk == "low", name
        assert safety.mode_allows(name, risk)[0]   # usable even in read-only mode


# ---- protocol ----------------------------------------------------------------------------

def test_scene_info_round_trip(monkeypatch):
    srv = FakeAddon()
    try:
        _point_at(monkeypatch, srv.port)
        out = bt.blender_scene_info()
        assert out["ok"] is True
        assert out["scene"]["name"] == "Scene"
        assert srv.requests[-1] == {"type": "get_scene_info", "params": {}}
    finally:
        srv.close()


def test_status_connected_and_down(monkeypatch):
    srv = FakeAddon()
    try:
        _point_at(monkeypatch, srv.port)
        up = bt.blender_status()
        assert up["connected"] is True and up["objects"] == 2
    finally:
        srv.close()
    _point_at(monkeypatch, _dead_port())
    down = bt.blender_status()
    assert down["connected"] is False
    assert "Blender" in down["error"] and "addon" in down["error"]


def test_object_info_params_and_addon_error(monkeypatch):
    srv = FakeAddon()
    try:
        _point_at(monkeypatch, srv.port)
        ok = bt.blender_object_info("Cube")
        assert ok["ok"] is True and ok["object"]["name"] == "Cube"
        assert srv.requests[-1]["params"] == {"name": "Cube"}
        missing = bt.blender_object_info("Nope")
        assert missing["ok"] is False and "Object not found" in missing["error"]
    finally:
        srv.close()
    assert bt.blender_object_info("")["ok"] is False   # no name -> no connection attempt


def test_run_python_and_chunked_reply(monkeypatch):
    srv = FakeAddon(chunked=True)   # reply split mid-JSON: client must buffer until it parses
    try:
        _point_at(monkeypatch, srv.port)
        out = bt.blender_run_python("bpy.ops.mesh.primitive_cube_add()")
        assert out["ok"] is True and out["executed"] is True
        assert srv.requests[-1]["type"] == "execute_code"
    finally:
        srv.close()
    assert bt.blender_run_python("")["ok"] is False


def test_screenshot_returns_written_path(monkeypatch):
    srv = FakeAddon()
    try:
        _point_at(monkeypatch, srv.port)
        out = bt.blender_screenshot(max_size=640)
        assert out["ok"] is True and os.path.exists(out["path"])
        assert srv.requests[-1]["params"]["max_size"] == 640
        os.remove(out["path"])
    finally:
        srv.close()


def test_unreachable_gives_setup_help(monkeypatch):
    _point_at(monkeypatch, _dead_port())
    out = bt.blender_scene_info()
    assert out["ok"] is False
    assert "not reachable" in out["error"] and "BlenderMCP" in out["error"]
