"""Tests for phone_intake and the /api/upload endpoint that feeds it.

The endpoint is driven through a real HTTP request against a running Ember Link server, because
the parts most likely to break — header auth, the size ceiling, a lying Content-Length — only
exist at the HTTP layer.
"""
import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# remote_server imports pyautogui/tools at module level for the screen-mirroring half, which is
# irrelevant here and unavailable headless. Same stubbing as test_pairing.py.
if "pyautogui" not in sys.modules:
    _pg = types.ModuleType("pyautogui")
    _pg.FAILSAFE = False
    _pg.PAUSE = 0
    _pg.size = lambda: (1920, 1080)
    sys.modules["pyautogui"] = _pg
if "tools" not in sys.modules:
    _t = types.ModuleType("tools")
    _t.run_powershell = lambda cmd, timeout=60: {"ok": True, "ran": cmd}
    _t.press_key = lambda *a, **k: None
    _t.type_text = lambda *a, **k: None
    sys.modules["tools"] = _t

import key_vault as KV
import data_protect as DP
import phone_intake as PI


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(KV, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(KV, "KEY_FILE", tmp_path / "vault.key")
    monkeypatch.setattr(DP, "RECIPIENTS_FILE", tmp_path / "adp_recipients.json")
    monkeypatch.setattr(PI, "CONFIG_FILE", tmp_path / "phone_intake.json")
    PI.set_dest(str(tmp_path / "iCloudDrive" / "Ember Photos"))
    return tmp_path


@pytest.fixture
def ready():
    assert DP.adp_setup("a good long passphrase")["ok"] is True


# --- filename safety ------------------------------------------------------------
@pytest.mark.parametrize("hostile", [
    "../../../.ssh/authorized_keys",
    "..\\..\\Windows\\System32\\evil.dll",
    "/etc/passwd",
    "....//....//x.jpg",
])
def test_filenames_cannot_escape_the_folder(hostile):
    """Anything a phone or Shortcut sends is untrusted input."""
    safe = PI.safe_name(hostile)
    assert "/" not in safe and "\\" not in safe
    assert not safe.startswith(".")
    assert Path(safe).name == safe


def test_blank_and_dot_names_get_a_generated_one():
    for junk in ("", "   ", ".", "..", "///"):
        assert PI.safe_name(junk).startswith("upload-")


def test_ordinary_names_survive_recognisably():
    assert PI.safe_name("IMG_0042.HEIC") == "IMG_0042.HEIC"


def test_absurdly_long_names_are_truncated():
    assert len(PI.safe_name("a" * 5000 + ".jpg")) <= 120


# --- receiving ------------------------------------------------------------------
def test_receive_encrypts_into_the_destination(ready, isolate):
    r = PI.receive("IMG_0001.jpg", b"\xff\xd8\xff-phone-photo-bytes")
    assert r["ok"] is True
    out = Path(r["protected"])
    assert out.exists() and out.parent == PI.dest_dir()
    assert b"phone-photo-bytes" not in out.read_bytes()


def test_received_photo_decrypts_back(ready, isolate):
    r = PI.receive("IMG_0001.jpg", b"the original photo")
    back = isolate / "back"
    DP.adp_unprotect_folder(str(PI.dest_dir()), dest_dir=str(back))
    assert (back / "IMG_0001.jpg").read_bytes() == b"the original photo"


def test_no_plaintext_is_ever_written_to_disk(ready, isolate):
    """The whole point of encrypt_bytes: nothing to shred, race, or leave after a crash."""
    PI.receive("IMG_0001.jpg", b"unique-plaintext-marker")
    for f in isolate.rglob("*"):
        if f.is_file():
            assert b"unique-plaintext-marker" not in f.read_bytes(), f


def test_resending_the_same_name_does_not_overwrite(ready):
    a = PI.receive("IMG_0001.jpg", b"first")
    b = PI.receive("IMG_0001.jpg", b"second")
    assert a["protected"] != b["protected"]
    assert Path(a["protected"]).exists() and Path(b["protected"]).exists()


def test_refuses_without_protection_set_up():
    r = PI.receive("IMG_0001.jpg", b"data")
    assert r["ok"] is False and "not set up" in r["error"]


def test_refuses_empty_and_oversized(ready, monkeypatch):
    assert PI.receive("x.jpg", b"")["ok"] is False
    monkeypatch.setattr(PI, "MAX_UPLOAD_BYTES", 10)
    r = PI.receive("x.jpg", b"much longer than ten bytes")
    assert r["ok"] is False and "exceeds" in r["error"]


def test_uploads_are_readable_by_enrolled_devices(ready):
    """A phone upload must be a normal protected file, not a passphrase-only special case."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    pub = DP.encode_pubkey(DP._pub_raw(X25519PrivateKey.generate()))
    DP.adp_add_recipient(pub, "my laptop")
    r = PI.receive("IMG_0001.jpg", b"shared photo")
    slots = DP.adp_inspect(r["protected"])["readable_by"]
    assert any(s["label"] == "my laptop" for s in slots)


# --- the HTTP endpoint ----------------------------------------------------------
@pytest.fixture
def server(ready):
    """A real Ember Link on an ephemeral port.

    Requests arrive from 127.0.0.1, which `_is_lan_ip` deliberately treats as NOT-LAN — that is
    what a tunnel-relayed public request looks like. So the PIN does not work here and every
    test authenticates with a pairing token, exactly as a phone or Shortcut off the LAN would.
    """
    import remote_server
    r = remote_server.start(port=0, pin="123456", idle_timeout=600.0)
    assert r.get("ok"), r
    port = remote_server._STATE["port"]
    token = remote_server.issue_pair_token()
    yield f"http://127.0.0.1:{port}", remote_server, token
    remote_server.stop()


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def test_upload_over_http_protects_the_photo(server):
    base, _rs, tok = server
    code, body = _post(base + "/api/upload", b"\xff\xd8\xff-over-the-wire",
                       {"X-Ember-Token": tok, "X-Ember-Filename": "IMG_9999.jpg"})
    assert code == 200 and body["ok"] is True
    out = Path(body["protected"])
    assert out.exists() and b"over-the-wire" not in out.read_bytes()


def test_upload_rejects_a_bad_credential(server):
    base, _rs, _tok = server
    code, body = _post(base + "/api/upload", b"data",
                       {"X-Ember-Token": "not-a-real-token", "X-Ember-Filename": "x.jpg"})
    assert code == 403 and body.get("ok") is False


def test_upload_rejects_no_credential(server):
    base, _rs, _tok = server
    code, _body = _post(base + "/api/upload", b"data", {"X-Ember-Filename": "x.jpg"})
    assert code == 403


def test_the_pin_alone_does_not_work_off_the_lan(server):
    """The tunnel-hardening property, asserted for uploads too: a request arriving over
    loopback (which is what a tunnel relay looks like) must not be authorisable by the
    6-digit PIN, or anyone finding the public URL could brute-force it."""
    base, _rs, _tok = server
    code, _b = _post(base + "/api/upload", b"data",
                     {"X-Ember-Pin": "123456", "X-Ember-Filename": "x.jpg"})
    assert code == 403


def test_upload_accepts_a_pairing_token(server):
    """This is the path a Shortcuts automation uses."""
    base, _rs, tok = server
    code, body = _post(base + "/api/upload", b"from-shortcuts",
                       {"X-Ember-Token": tok, "X-Ember-Filename": "IMG_1234.HEIC"})
    assert code == 200 and body["ok"] is True
    assert body["name"] == "IMG_1234.HEIC"


def test_upload_refuses_oversized_before_reading_it(server, monkeypatch):
    import remote_server
    monkeypatch.setattr(remote_server, "_MAX_UPLOAD_BYTES", 16)
    base, _rs, tok = server
    code, body = _post(base + "/api/upload", b"x" * 64,
                       {"X-Ember-Token": tok, "X-Ember-Filename": "big.jpg"})
    assert code == 413 and "too large" in body["error"]


def test_upload_rejects_empty_body(server):
    base, _rs, tok = server
    code, _b = _post(base + "/api/upload", b"", {"X-Ember-Token": tok})
    assert code == 400


def test_upload_url_encoded_filename_is_decoded(server):
    base, _rs, tok = server
    code, body = _post(base + "/api/upload", b"data",
                       {"X-Ember-Token": tok, "X-Ember-Filename": "holiday%20snap.jpg"})
    assert code == 200 and body["name"] == "holiday_snap.jpg"


def test_upload_with_a_traversal_filename_stays_in_the_folder(server):
    base, _rs, tok = server
    code, body = _post(base + "/api/upload", b"data",
                       {"X-Ember-Token": tok,
                        "X-Ember-Filename": "..%2F..%2F..%2Fetc%2Fpasswd"})
    assert code == 200
    assert Path(body["protected"]).parent == PI.dest_dir()


def test_the_2mb_event_cap_is_untouched(server):
    """Raising the upload ceiling must not widen what /api/event will read."""
    import remote_server
    assert remote_server._MAX_POST_BYTES == 2_000_000
    assert remote_server._MAX_UPLOAD_BYTES > remote_server._MAX_POST_BYTES


# --- the Shortcuts recipe -------------------------------------------------------
def test_shortcut_recipe_names_the_real_endpoint_and_headers():
    steps = " ".join(PI.shortcut_recipe("https://x.trycloudflare.com", "TOKEN123"))
    assert "https://x.trycloudflare.com/api/upload" in steps
    assert "X-Ember-Token" in steps and "TOKEN123" in steps
    assert "X-Ember-Filename" in steps
    assert "POST" in steps


def test_advice_leads_with_the_icloud_photos_precondition():
    """If iCloud Photos stays on, none of this helps — that has to be the first thing said."""
    first = PI.advice()[0]
    assert "iCloud Photos" in first and "OFF" in first


def test_setup_refuses_before_protection_is_configured():
    r = PI.adp_phone_setup()
    assert r["ok"] is False and "data protection" in r["error"]


def test_setup_refuses_when_link_is_not_running(ready):
    r = PI.adp_phone_setup()
    assert r["ok"] is False and "Ember Link" in r["error"]


def test_setup_returns_a_usable_recipe(server):
    base, _rs, _tok = server
    r = PI.adp_phone_setup()
    assert r["ok"] is True
    assert r["token"] and r["url"]
    assert any("/api/upload" in s for s in r["steps"])
    # The token is full Ember Link access, not upload-only — say so.
    assert "revoke_pairings" in r["note"]


def test_setup_token_actually_works(server):
    base, _rs, _tok = server
    r = PI.adp_phone_setup()
    code, body = _post(base + "/api/upload", b"via issued token",
                       {"X-Ember-Token": r["token"], "X-Ember-Filename": "IMG_1.jpg"})
    assert code == 200 and body["ok"] is True


# --- wiring ---------------------------------------------------------------------
def test_dest_defaults_to_a_sync_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(PI, "CONFIG_FILE", tmp_path / "none.json")
    icloud = tmp_path / "iCloudDrive"
    icloud.mkdir()
    monkeypatch.setattr(DP, "icloud_targets", lambda: [
        {"path": str(icloud), "label": "iCloud Drive", "exists": True, "syncable": True, "note": ""},
    ])
    assert PI.dest_dir() == icloud / "Ember Photos"


def test_set_folder_rejects_a_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert PI.adp_phone_set_folder(str(f))["ok"] is False


def test_tool_tables_agree():
    names = {d["name"] for d in PI.TOOL_DECLARATIONS}
    assert names == set(PI.TOOL_DISPATCH)
    assert PI.READONLY_TOOLS | PI.INTERACTION_TOOLS == names


def test_registered_with_the_agent():
    src = open("agent.py", encoding="utf-8").read()
    assert "\nimport phone_intake\n" in src
    assert "phone_intake" in src.split("for _feat in (", 1)[1].split("):", 1)[0]


def test_issuing_a_phone_token_is_high_risk():
    import safety
    assert safety.classify("adp_phone_setup", {})[0] == "high"


def test_phone_pane_exists_in_the_link_app():
    import remote_server
    assert 'data-mode=photos' in remote_server.PAGE
    assert "/api/upload" in remote_server.PAGE
    assert "X-Ember-Token" in remote_server.PAGE
    # The precondition has to be visible on the phone, not only in the docs.
    assert "iCloud Photos" in remote_server.PAGE


# --- the one-tap link -----------------------------------------------------------
def test_lan_magic_link_signs_in_without_a_pin(server):
    """The LAN case previously made the phone type a 6-digit PIN for no benefit — the token in
    the link is a stronger credential than the PIN it replaces."""
    _base, rs, _tok = server
    link = rs.magic_link(go="photos")
    assert link.startswith("http://") and "#tok=" in link and "go=photos" in link
    tok = link.split("#tok=", 1)[1].split("&", 1)[0]
    assert rs._token_valid(tok) is True


def test_magic_link_is_empty_when_link_is_down():
    import remote_server
    remote_server.stop()
    assert remote_server.magic_link(go="photos") == ""


def test_each_magic_link_is_a_fresh_token(server):
    _base, rs, _tok = server
    a = rs.magic_link().split("#tok=", 1)[1]
    b = rs.magic_link().split("#tok=", 1)[1]
    assert a != b and rs._token_valid(a) and rs._token_valid(b)


def test_the_page_honours_the_go_fragment():
    import remote_server
    # The QR lands on the Photos tab rather than the screen mirror.
    assert "go=([a-z]+)" in remote_server.PAGE
    assert "if(GOTO)" in remote_server.PAGE


def test_recovery_code_is_strong_and_transcribable():
    seen = {PI.generate_recovery_code() for _ in range(200)}
    assert len(seen) == 200                       # no repeats
    code = PI.generate_recovery_code()
    assert code.count("-") == PI._CODE_GROUPS - 1
    # Characters that get misread off paper must not appear.
    assert not (set(code.replace("-", "")) & set("01OIl"))


def test_quick_setup_turns_everything_on_in_one_call(server):
    _base, rs, _tok = server
    r = PI.quick_setup()
    assert r["ok"] is True
    assert "go=photos" in r["link"]
    assert r["dest"] and r["reachable"] == "home Wi-Fi only"


def test_quick_setup_reports_a_tunnel_failure_instead_of_hanging(server, monkeypatch):
    """Clicking 'yes, away from home' without cloudflared must fail fast and say why."""
    import remote_server
    monkeypatch.setattr(remote_server, "remote_url", lambda: "")
    monkeypatch.setattr(remote_server, "enable_remote",
                        lambda *a, **k: {"ok": False, "error": "cloudflared is not installed"})
    r = PI.quick_setup(public=True)
    assert r["ok"] is False and "cloudflared" in r["error"]
