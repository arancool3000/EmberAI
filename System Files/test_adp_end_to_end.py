"""End-to-end: the exact journey a user takes, with a real JPEG, over real HTTP.

Every other test file checks one layer. This one walks the whole thing in order — click the
button, scan the QR, send a photo, get it back — because that is what actually has to work, and
each layer passing in isolation does not prove the seams line up.

`qrcode` is a declared dependency; `cv2` is not, so the QR *decode* check skips when it is
absent rather than failing. Everything else runs unconditionally.
"""
import json
import sys
import urllib.error
import tempfile
import types
import urllib.request
from pathlib import Path

import pytest

for _n, _attrs in (("pyautogui", {"FAILSAFE": False, "PAUSE": 0, "size": lambda: (1920, 1080)}),
                   ("tools", {"run_powershell": lambda *a, **k: {},
                              "press_key": lambda *a, **k: None,
                              "type_text": lambda *a, **k: None})):
    if _n not in sys.modules:
        _m = types.ModuleType(_n)
        for _k, _v in _attrs.items():
            setattr(_m, _k, _v)
        sys.modules[_n] = _m

import key_vault as KV
import data_protect as DP
import phone_intake as PI
import remote_server as RS

#: A genuine minimal JPEG (SOI … EOI), so "it decrypts" means a real photo came back, not just
#: that some bytes round-tripped.
JPEG = (bytes.fromhex("ffd8ffe000104a46494600010100000100010000")
        + bytes(range(256)) * 8
        + bytes.fromhex("ffd9"))


@pytest.fixture
def journey(monkeypatch, tmp_path):
    """A fresh machine: empty vault, no recipients, Ember Link up on an ephemeral port."""
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "recipients.json")
    monkeypatch.setattr(PI, "CONFIG_FILE", tmp_path / "phone_intake.json")
    PI.set_dest(str(tmp_path / "iCloudDrive" / "Ember Photos"))
    assert RS.start(port=0, pin="123456", idle_timeout=300).get("ok")
    yield tmp_path, RS._STATE["port"]
    RS.stop()


def _upload(port, token, name, data):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/upload", data=data,
                                 method="POST",
                                 headers={"X-Ember-Token": token, "X-Ember-Filename": name})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def test_the_whole_journey(journey):
    tmp_path, port = journey

    # 1. The user clicks "Protect my photos".
    r = PI.quick_setup()
    assert r["ok"] is True
    assert DP.is_set_up() is True
    code = r["recovery_code"]
    assert code and len(code.replace("-", "")) == PI._CODE_GROUPS * PI._CODE_GROUP_LEN
    link = r["link"]
    assert "#tok=" in link and "go=photos" in link

    # 2. The phone opens the link. The #fragment never reaches the server — the page JS reads
    #    it — so check the page carries that code, then use the token exactly as the JS would.
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode()
    assert "tok=([^&]+)" in page and "go=([a-z]+)" in page
    assert "data-mode=photos" in page
    token = link.split("#tok=", 1)[1].split("&", 1)[0]
    assert RS._token_valid(token) is True

    # 3. The phone sends a photo.
    up = _upload(port, token, "IMG_0042.JPG", JPEG)
    assert up["ok"] is True and up["bytes"] == len(JPEG)
    enc = Path(up["protected"])
    assert enc.exists() and enc.suffix == DP.EXT

    # 4. What Apple would receive is unreadable, and no plaintext was left behind.
    blob = enc.read_bytes()
    assert blob.startswith(DP.MAGIC)
    assert not blob.startswith(b"\xff\xd8")
    assert JPEG[:32] not in blob
    leaked = [str(f) for f in tmp_path.rglob("*")
              if f.is_file() and f != enc and JPEG[:32] in f.read_bytes()]
    assert not leaked, leaked

    # 5. The user gets the photo back, byte for byte.
    back = tmp_path / "restored"
    u = DP.adp_unprotect_folder(str(PI.dest_dir()), dest_dir=str(back))
    assert u["restored_count"] == 1
    got = (back / "IMG_0042.JPG").read_bytes()
    assert got == JPEG
    assert got.startswith(b"\xff\xd8") and got.endswith(b"\xff\xd9")


def test_a_stranger_cannot_read_the_result(journey, monkeypatch, tmp_path):
    """Someone who obtains the encrypted file — from a breach, a warrant, a stolen backup —
    with their own Ember install and their own code."""
    _tmp, port = journey
    r = PI.quick_setup()
    token = r["link"].split("#tok=", 1)[1].split("&", 1)[0]
    enc = Path(_upload(port, token, "IMG_0001.JPG", JPEG)["protected"])

    other = tmp_path / "stranger"
    other.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(KV, "VAULT_FILE", other / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", other / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", other / "recipients.json")
    assert DP.adp_setup("a completely unrelated passphrase")["ok"] is True

    out = DP.adp_unprotect_file(str(enc), dest_dir=str(other / "out"))
    assert out["ok"] is False
    assert not list((other / "out").glob("*")) if (other / "out").exists() else True


def test_the_qr_code_actually_scans(journey, tmp_path):
    """The one step the user performs physically. A QR that encodes the wrong thing fails in
    their hands, not in any layer test."""
    cv2 = pytest.importorskip("cv2", reason="QR decoding needs opencv; qrcode itself is a "
                                            "declared dependency and is checked below")
    import quick_tools
    link = PI.quick_setup()["link"]
    png = tmp_path / "qr.png"
    assert quick_tools.qr_make(link, str(png)).get("ok") is True
    assert png.exists() and png.stat().st_size > 0
    decoded, _pts, _qr = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(png)))
    assert decoded == link, f"QR encodes {decoded!r}, not the link"


def test_qr_generation_is_available_at_all(journey, tmp_path):
    """qrcode is in requirements.txt and bundled in Ember.spec, so when it IS installed the
    wiring must work. Skipped rather than failed on a bare checkout, so this reports a real
    breakage and not just "you haven't run pip install"."""
    pytest.importorskip("qrcode")
    import quick_tools
    r = quick_tools.qr_make("https://example.invalid/#tok=x", str(tmp_path / "q.png"))
    assert r.get("ok") is True, r.get("error")


def test_a_second_photo_does_not_clobber_the_first(journey):
    _tmp, port = journey
    r = PI.quick_setup()
    token = r["link"].split("#tok=", 1)[1].split("&", 1)[0]
    a = _upload(port, token, "IMG_0001.JPG", JPEG)
    b = _upload(port, token, "IMG_0001.JPG", JPEG + b"different")
    assert a["protected"] != b["protected"]
    assert Path(a["protected"]).exists() and Path(b["protected"]).exists()


def test_uploads_stop_when_protection_is_reset(journey):
    """Turning protection off must fail the upload loudly, not accept photos and drop them."""
    _tmp, port = journey
    r = PI.quick_setup()
    token = r["link"].split("#tok=", 1)[1].split("&", 1)[0]
    DP.adp_reset()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/upload", data=JPEG, method="POST",
                                 headers={"X-Ember-Token": token, "X-Ember-Filename": "x.jpg"})
    try:
        urllib.request.urlopen(req, timeout=20)
        assert False, "upload should not have succeeded"
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        assert e.code == 500 and "not set up" in body["error"]
