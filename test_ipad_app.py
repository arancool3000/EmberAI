"""Guard the standalone Ember AI iPad PWA (docs/app/*): the install assets exist, the manifest is
valid + standalone, the app shell wires up the service worker + Gemini streaming + key storage,
and the service worker caches the shell. No browser needed.
Run: python test_ipad_app.py"""
import json
import os

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "app")


def _read(name):
    with open(os.path.join(APP, name), encoding="utf-8") as f:
        return f.read()


def test_install_assets_exist():
    for f in ("index.html", "manifest.webmanifest", "sw.js", "icon.png"):
        assert os.path.exists(os.path.join(APP, f)), f"missing {f}"


def test_manifest_valid_and_standalone():
    m = json.loads(_read("manifest.webmanifest"))
    assert m["display"] == "standalone"
    assert m["start_url"] == "./"
    assert any(i["src"] == "./icon.png" for i in m["icons"])


def test_app_shell_is_installable_and_calls_gemini():
    h = _read("index.html")
    assert 'rel="manifest"' in h and "apple-mobile-web-app-capable" in h
    assert 'apple-touch-icon' in h
    assert 'register("./sw.js")' in h                     # PWA service worker
    assert "streamGenerateContent" in h                  # talks to the Gemini API directly
    assert "generativelanguage.googleapis.com" in h
    assert "ember_ai_key" in h and "localStorage" in h   # key stored on-device


def test_service_worker_caches_shell():
    s = _read("sw.js")
    assert "caches.open" in s and "index.html" in s


def test_icon_is_real():
    assert os.path.getsize(os.path.join(APP, "icon.png")) > 1000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} ipad-app tests passed")
