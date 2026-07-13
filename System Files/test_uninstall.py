"""Hermetic tests for uninstall.py. Everything runs against temp dirs + monkeypatched
locations, so no real Ember install, data dir, or login item is ever touched.
Run: python test_uninstall.py
"""
import tempfile
from pathlib import Path

import uninstall as U


def _make_fake_install(root: Path) -> Path:
    """A minimal folder that passes the Ember-install marker check (old flat layout)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# ember main\n")
    (root / "ui.py").write_text("# ember ui\n")
    return root


def test_looks_like_ember_install_matches_both_layouts():
    with tempfile.TemporaryDirectory() as td:
        flat = _make_fake_install(Path(td) / "EmberFlat")
        assert U._looks_like_ember_install(flat)
        repo = Path(td) / "EmberAI"
        (repo / "System Files").mkdir(parents=True)
        (repo / "System Files" / "main.py").write_text("x")
        assert U._looks_like_ember_install(repo)               # System Files/ layout
        rnd = Path(td) / "random"
        rnd.mkdir()
        assert not U._looks_like_ember_install(rnd)


def test_safe_to_remove_refuses_system_home_and_non_ember():
    assert not U._safe_to_remove(Path("/"))
    assert not U._safe_to_remove(Path("/usr"))
    assert not U._safe_to_remove(Path.home())
    with tempfile.TemporaryDirectory() as td:
        rnd = Path(td) / "important_docs"
        rnd.mkdir()
        assert not U._safe_to_remove(rnd)                      # exists but isn't Ember
        inst = _make_fake_install(Path(td) / "Ember")
        assert U._safe_to_remove(inst)                         # a real install is allowed


def test_data_dir_is_safe_to_remove():
    with tempfile.TemporaryDirectory() as td:
        dd = Path(td) / "Ember-data"
        dd.mkdir()
        orig = U._data_dir
        U._data_dir = lambda: dd
        try:
            assert U._safe_to_remove(dd)
        finally:
            U._data_dir = orig


def test_uninstall_instance_dry_run_then_confirm_removes():
    with tempfile.TemporaryDirectory() as td:
        inst = _make_fake_install(Path(td) / "Ember")
        dry = U.uninstall_instance(str(inst), confirm=False)
        assert dry["ok"] and dry.get("dry_run") and inst.exists()   # nothing removed yet
        done = U.uninstall_instance(str(inst), confirm=True, use_trash=False)
        assert done["ok"] and not inst.exists()


def test_uninstall_instance_refuses_non_ember_path():
    with tempfile.TemporaryDirectory() as td:
        docs = Path(td) / "my_taxes"
        docs.mkdir()
        (docs / "return.txt").write_text("do not delete")
        r = U.uninstall_instance(str(docs), confirm=True, use_trash=False)
        assert r["ok"] is False and "refused" in r["error"]
        assert docs.exists() and (docs / "return.txt").exists()      # untouched


def test_find_and_uninstall_all():
    with tempfile.TemporaryDirectory() as td:
        inst = _make_fake_install(Path(td) / "Ember")
        dd = Path(td) / "data"
        dd.mkdir()
        orig_c, orig_d, orig_r = (U._candidate_install_roots, U._data_dir, U._running_install_root)
        U._candidate_install_roots = lambda: [inst]
        U._data_dir = lambda: dd
        U._running_install_root = lambda: Path(td) / "not_the_running_one"
        try:
            found = U.find_instances()
            assert found["count"] == 1
            assert found["installs"][0]["path"] == str(inst)
            assert found["data"][0]["path"] == str(dd)

            plan = U.uninstall_all(confirm=False)
            assert plan.get("dry_run")
            planned = {x["path"] for x in plan["would_remove"]}
            assert str(inst) in planned and str(dd) in planned
            assert inst.exists() and dd.exists()                     # still a dry run

            done = U.uninstall_all(confirm=True, use_trash=False)
            assert done["ok"] and not inst.exists() and not dd.exists()
        finally:
            U._candidate_install_roots, U._data_dir, U._running_install_root = orig_c, orig_d, orig_r


def test_uninstall_all_keeps_the_running_install_unless_forced():
    with tempfile.TemporaryDirectory() as td:
        inst = _make_fake_install(Path(td) / "Ember")
        orig_c, orig_d, orig_r = (U._candidate_install_roots, U._data_dir, U._running_install_root)
        U._candidate_install_roots = lambda: [inst]
        U._data_dir = lambda: Path(td) / "no-data-here"       # doesn't exist -> no data item
        U._running_install_root = lambda: inst                # THIS install is "running"
        try:
            plan = U.uninstall_all(confirm=False)
            assert plan["skipped_running"] == [str(inst)]     # running one is protected
            assert not plan["would_remove"]
            # Even with confirm, the running install is preserved unless include_running.
            done = U.uninstall_all(confirm=True, use_trash=False)
            assert inst.exists()
            # Force it:
            U.uninstall_all(confirm=True, use_trash=False, include_running=True)
            assert not inst.exists()
        finally:
            U._candidate_install_roots, U._data_dir, U._running_install_root = orig_c, orig_d, orig_r


def test_trash_fallback_deletes_when_send2trash_absent():
    with tempfile.TemporaryDirectory() as td:
        inst = _make_fake_install(Path(td) / "Ember")
        # use_trash=True; send2trash isn't installed in CI, so it must fall back to a real delete.
        U.uninstall_instance(str(inst), confirm=True, use_trash=True)
        assert not inst.exists()


def test_agent_and_safety_wire_the_uninstaller():
    import os
    d = os.path.dirname(__file__)
    agent_src = open(os.path.join(d, "agent.py"), encoding="utf-8").read()
    assert "import uninstall" in agent_src
    for line in ('"list_ember_installs": uninstall.find_instances',
                 '"uninstall_ember": uninstall.uninstall_all',
                 '"uninstall_ember_instance": uninstall.uninstall_instance'):
        assert line in agent_src, line
    for decl in ('"name": "uninstall_ember"', '"name": "uninstall_ember_instance"',
                 '"name": "list_ember_installs"'):
        assert decl in agent_src, decl
    safety_src = open(os.path.join(d, "safety.py"), encoding="utf-8").read()
    # A confirmed uninstall must be classified high-risk (so the user is prompted).
    assert '"uninstall_ember", "uninstall_ember_instance"' in safety_src
    assert "UNINSTALLS Ember" in safety_src
    assert "list_ember_installs" in safety_src   # the read-only lister is safe


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print("  ok ", t.__name__)
        passed += 1
    print(f"\n{passed}/{len(tests)} uninstall tests passed")
