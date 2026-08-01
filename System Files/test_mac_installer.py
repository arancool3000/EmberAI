"""Tests for 'macOS Install/Install Ember.command'.

The installer is shell, and its job is a sequence of filesystem effects, so these run it for
real against a throwaway tree with the PyInstaller build stubbed out. What is verified is the
installer's own logic — build, copy, verify, fall back — not PyInstaller's.

The reported bug: double-clicking "Install Ember.command" ran Ember from source in a Terminal
window under the venv's python3.12 and installed nothing. It `exec`-ed Ember.command, which
launches from source by design. Nothing ever reached /Applications.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "macOS Install" / "Install Ember.command"


def _tree(tmp_path, build="ok"):
    """A miniature copy of the shipped layout, with the heavy build stubbed.

    `build` is "ok" (produces a bundle), "fail" (non-zero exit) or "empty" (exits 0 but
    produces nothing — PyInstaller really can do this).
    """
    root = tmp_path / "EmberAI"
    (root / "macOS Install").mkdir(parents=True)
    sysd = root / "System Files"
    sysd.mkdir()
    shutil.copy(INSTALLER, root / "macOS Install" / "Install Ember.command")
    os.chmod(root / "macOS Install" / "Install Ember.command", 0o755)
    (root / "macOS Install" / "Installation Guide.html").write_text("<html></html>")

    if build == "fail":
        body = 'echo "boom" >&2\nexit 1\n'
    elif build == "empty":
        body = 'echo "built nothing"\nexit 0\n'
    else:
        body = ('mkdir -p "$(dirname "$0")/dist/Ember.app/Contents/MacOS"\n'
                'printf "#!/bin/sh\\n" > "$(dirname "$0")/dist/Ember.app/Contents/MacOS/Ember"\n'
                'chmod +x "$(dirname "$0")/dist/Ember.app/Contents/MacOS/Ember"\n'
                'echo "built"\nexit 0\n')
    stub = sysd / "BUILD_DESKTOP_APP.command"
    stub.write_text("#!/bin/bash\n" + body)
    os.chmod(stub, 0o755)
    (sysd / "Ember.command").write_text("#!/bin/bash\necho from-source\n")
    os.chmod(sysd / "Ember.command", 0o755)
    return root


def _run(root, apps_dir, extra_env=None):
    env = dict(os.environ)
    env.update({"EMBER_NONINTERACTIVE": "1", "EMBER_APPS_DIR": str(apps_dir),
                "HOME": str(root.parent / "home")})
    (root.parent / "home").mkdir(exist_ok=True)
    env.update(extra_env or {})
    return subprocess.run(["bash", str(root / "macOS Install" / "Install Ember.command")],
                          capture_output=True, text=True, env=env, timeout=120)


# --- the bug -------------------------------------------------------------------
def test_the_installer_actually_installs(tmp_path):
    """The whole point: an app in the applications folder, not a Terminal session."""
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    r = _run(root, apps)
    assert r.returncode == 0, r.stderr
    assert (apps / "Ember.app").is_dir()
    assert (apps / "Ember.app" / "Contents" / "MacOS" / "Ember").exists()


def test_it_does_not_just_run_from_source(tmp_path):
    """The old behaviour was `exec System Files/Ember.command`. If that ever comes back, the
    installer would print 'from-source' and install nothing."""
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    r = _run(root, apps)
    assert "from-source" not in r.stdout
    assert (apps / "Ember.app").is_dir()


def test_it_says_where_the_app_went(tmp_path):
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    r = _run(root, apps)
    assert str(apps / "Ember.app") in r.stdout
    assert "Launchpad or Spotlight" in r.stdout


# --- failure paths --------------------------------------------------------------
def test_a_failed_build_does_not_claim_success(tmp_path):
    root = _tree(tmp_path, build="fail")
    apps = tmp_path / "Applications"
    r = _run(root, apps)
    assert r.returncode != 0
    assert not (apps / "Ember.app").exists()
    assert "was not installed" in r.stdout
    # And it points at the thing that still works.
    assert "Ember.command" in r.stdout


def test_a_build_that_produces_nothing_is_caught(tmp_path):
    """PyInstaller can exit 0 having produced no bundle. Installing 'nothing' must not read as
    a success."""
    root = _tree(tmp_path, build="empty")
    apps = tmp_path / "Applications"
    r = _run(root, apps)
    assert r.returncode != 0
    assert "no Ember.app" in r.stdout
    assert not (apps / "Ember.app").exists()


def test_a_missing_system_files_folder_is_reported(tmp_path):
    root = _tree(tmp_path)
    shutil.rmtree(root / "System Files")
    r = _run(root, tmp_path / "Applications")
    assert r.returncode != 0
    assert "System Files" in r.stdout


# --- upgrading and permissions ---------------------------------------------------
def test_reinstalling_replaces_the_old_app(tmp_path):
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    apps.mkdir()
    stale = apps / "Ember.app"
    stale.mkdir()
    (stale / "STALE").write_text("old version")
    r = _run(root, apps)
    assert r.returncode == 0
    assert not (stale / "STALE").exists()          # genuinely replaced, not merged into
    assert (stale / "Contents" / "MacOS" / "Ember").exists()


def test_it_falls_back_to_a_personal_applications_folder(tmp_path):
    """No admin rights for /Applications is the common case on a managed Mac. Falling back
    beats failing.

    The unwritable destination is simulated with a regular FILE where the folder should be,
    rather than a read-only directory: root ignores directory permissions, so a chmod-based
    test would silently pass without exercising the fallback at all.
    """
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    apps.write_text("not a directory")
    r = _run(root, apps)
    home = root.parent / "home"
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / "Applications" / "Ember.app").is_dir()
    assert "installing to" in r.stdout.lower()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_permission_failure_is_explained_not_swallowed(tmp_path):
    root = _tree(tmp_path)
    apps = tmp_path / "Applications"
    apps.mkdir()
    (apps / "Ember.app").mkdir()
    os.chmod(apps, 0o555)
    try:
        r = _run(root, apps)
        assert "permission denied" in (r.stdout + r.stderr).lower()
    finally:
        os.chmod(apps, 0o755)


# --- the build script it drives ---------------------------------------------------
def test_build_script_can_run_unattended():
    """Install drives BUILD_DESKTOP_APP.command; if that still blocked on `read`, the install
    would hang forever with nobody to press a key."""
    src = (REPO / "System Files" / "BUILD_DESKTOP_APP.command").read_text()
    assert "EMBER_NONINTERACTIVE" in src
    assert "hold()" in src
    # No bare interactive reads left on the failure paths.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "read _" not in stripped and "read -r _" not in stripped or "hold" in stripped, line


def test_installer_is_executable():
    assert os.access(INSTALLER, os.X_OK), "the .command must be executable to double-click"
