"""Static guards for the two user-facing installation entry points."""
from pathlib import Path


# The source now lives in <repo>/System Files/; the user-facing install folders + README stay
# at the repo root (so a fresh download shows only the Windows/Mac buttons).
ROOT = Path(__file__).parent            # <repo>/System Files
REPO = ROOT.parent                      # <repo> (repo root)


def test_windows_install_folder_is_self_explanatory():
    folder = REPO / "Windows Install"
    installer = (folder / "Install Ember.bat").read_text(encoding="utf-8")
    guide = (folder / "Installation Guide.html").read_text(encoding="utf-8")
    assert "Installation Guide.html" in installer
    assert ".venv\\Scripts\\python.exe" in installer
    assert "winget install" in installer
    assert "private environment" in guide


def test_macos_install_folder_is_self_explanatory():
    folder = REPO / "macOS Install"
    installer = (folder / "Install Ember.command").read_text(encoding="utf-8")
    guide = (folder / "Installation Guide.html").read_text(encoding="utf-8")
    assert 'open "$HERE/Installation Guide.html"' in installer
    assert 'exec "$ROOT/System Files/Ember.command"' in installer
    assert "private environment" in guide


def test_readme_leads_with_platform_install_folders():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    install = readme.index("## Install")
    features = readme.index("## ✨ What's inside")
    assert install < features
    assert "Windows Install/" in readme and "macOS Install/" in readme


def test_windows_launcher_reuses_private_environment():
    launcher = (ROOT / "Ember.bat").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\pythonw.exe" in launcher
    assert "Windows Install\\Install Ember.bat" in launcher


def test_macos_launcher_names_the_process_ember_not_python():
    # Running from source, the Dock/Activity Monitor label comes from the process name, which
    # defaults to the interpreter ("python3.12"). `exec -a Ember` names it "Ember" instead.
    launcher = (ROOT / "Ember.command").read_text(encoding="utf-8")
    assert "exec -a Ember" in launcher
    assert 'exec "$PYBIN" main.py' not in launcher   # no un-named exec left behind


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} install-entrypoint tests passed")
