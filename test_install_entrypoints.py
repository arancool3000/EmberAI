"""Static guards for the two user-facing installation entry points."""
from pathlib import Path


ROOT = Path(__file__).parent


def test_windows_install_folder_is_self_explanatory():
    folder = ROOT / "Windows Install"
    installer = (folder / "Install Ember.bat").read_text(encoding="utf-8")
    guide = (folder / "Installation Guide.html").read_text(encoding="utf-8")
    assert "Installation Guide.html" in installer
    assert ".venv\\Scripts\\python.exe" in installer
    assert "winget install" in installer
    assert "private environment" in guide


def test_macos_install_folder_is_self_explanatory():
    folder = ROOT / "macOS Install"
    installer = (folder / "Install Ember.command").read_text(encoding="utf-8")
    guide = (folder / "Installation Guide.html").read_text(encoding="utf-8")
    assert 'open "$HERE/Installation Guide.html"' in installer
    assert 'exec "$ROOT/Ember.command"' in installer
    assert "private environment" in guide


def test_readme_leads_with_platform_install_folders():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = readme.index("## Install")
    features = readme.index("## ✨ What's inside")
    assert install < features
    assert "Windows Install/" in readme and "macOS Install/" in readme


def test_windows_launcher_reuses_private_environment():
    launcher = (ROOT / "Ember.bat").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\pythonw.exe" in launcher
    assert "Windows Install\\Install Ember.bat" in launcher


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print(f"{len(tests)}/{len(tests)} install-entrypoint tests passed")
