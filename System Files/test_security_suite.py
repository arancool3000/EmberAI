"""Tests for Ember's web protection, secret redaction, audit log, and capability
modes (web_policy.py, redaction.py, audit.py, safety.py modes).

Hermetic: all state goes to a throwaway dir and online reputation is disabled.

    pytest test_security_suite.py
    python test_security_suite.py
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ember_sec_test_")
os.environ["EMBER_SUPPORT_DIR"] = _TMP

import redaction
import web_policy
import audit
import safety

web_policy.set_config(online_reputation=False)


# ----------------------------- redaction -----------------------------------

def test_redacts_api_keys_and_tokens():
    samples = [
        "sk-ABCDEFGHIJKLMNOPQRSTUVWX1234",          # openai-style
        "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",       # anthropic
        "AKIAIOSFODNN7EXAMPLE",                       # aws
        "AIza" + "B" * 35,                            # google
    ]
    for s in samples:
        clean, n = redaction.scrub_text(f"key is {s} ok")
        assert n >= 1 and s not in clean, (s, clean)


def test_redacts_password_assignment_and_url_creds():
    c1, n1 = redaction.scrub_text("password=hunter2supersecret")
    assert n1 >= 1 and "hunter2supersecret" not in c1
    c2, n2 = redaction.scrub_text("https://admin:s3cr3tpw@example.com/x")
    assert n2 >= 1 and "s3cr3tpw" not in c2


def test_redacts_credit_card_with_luhn():
    clean, n = redaction.scrub_text("card 4111 1111 1111 1111 please")
    assert n >= 1 and "4111" not in clean


def test_contains_secret_and_scrub_obj():
    assert redaction.contains_secret("token=abcdef1234567890") is True
    assert redaction.contains_secret("just a normal sentence") is False
    obj = {"a": "sk-ABCDEFGHIJKLMNOPQRSTUV1234", "b": ["x", "password=longenough123"]}
    out = redaction.scrub_obj(obj)
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV1234" not in str(out)
    assert "longenough123" not in str(out)


# ----------------------------- web policy ----------------------------------

def test_clean_site_allowed():
    r = web_policy.check_url("https://example.com/page")
    assert r["allowed"] and r["verdict"] == "clean", r


def test_blocklist_blocks_host_and_subdomains():
    web_policy.add_block("bad.test")
    assert web_policy.check_url("http://bad.test/x")["verdict"] == "blocked"
    assert web_policy.check_url("http://sub.bad.test/x")["verdict"] == "blocked"


def test_allow_overrides_block():
    web_policy.add_block("good.test")
    web_policy.add_allow("good.test")
    assert web_policy.check_url("http://good.test/x")["allowed"] is True


def test_builtin_malware_domain_blocked():
    r = web_policy.check_url("http://malware.testing.google.test/path")
    assert r["verdict"] == "blocked", r


def test_typosquat_flagged_suspicious():
    r = web_policy.check_url("http://paypa1.com")
    assert r["verdict"] == "suspicious" and r.get("impersonates") == "paypal.com", r


def test_gate_navigation_blocks():
    web_policy.add_block("phisher.test")
    g = web_policy.gate_navigation("http://phisher.test/login")
    assert g["allowed"] is False, g


# ----------------------------- capability modes ----------------------------

def test_modes_gate_tools():
    safety.set_mode("read_only")
    assert safety.mode_allows("read_file", "low")[0] is True
    assert safety.mode_allows("scan_file", "low")[0] is True
    assert safety.mode_allows("run_shell", "medium")[0] is False
    safety.set_mode("restricted")
    assert safety.mode_allows("write_file", "medium")[0] is True
    assert safety.mode_allows("run_shell", "high")[0] is False
    safety.set_mode("full")
    assert safety.mode_allows("run_shell", "high")[0] is True
    assert safety.set_mode("bogus")["ok"] is False
    safety.set_mode("full")


# ----------------------------- audit log -----------------------------------

def _fresh_support():
    d = tempfile.mkdtemp(prefix="ember_audit_")
    old = os.environ["EMBER_SUPPORT_DIR"]
    os.environ["EMBER_SUPPORT_DIR"] = d
    return old


def test_audit_chain_valid_and_detects_tampering():
    import json
    old = _fresh_support()
    try:
        audit.record("scan_file", {"path": "/tmp/x"}, "low", "clean")
        audit.record("run_shell", {"command": "ls"}, "medium", "ok")
        v = audit.verify()
        assert v["valid"] and v["entries"] == 2, v
        p = audit._log_path()
        lines = p.read_text().splitlines()
        e = json.loads(lines[0]); e["name"] = "tampered"; lines[0] = json.dumps(e)
        p.write_text("\n".join(lines) + "\n")
        assert audit.verify()["valid"] is False
    finally:
        os.environ["EMBER_SUPPORT_DIR"] = old


def test_audit_redacts_secrets():
    old = _fresh_support()
    try:
        audit.record("write_file",
                     {"path": "/tmp/c", "text": "api_key=sk-ABCDEFGHIJKLMNOPQRSTUVWX"},
                     "medium", "ok")
        blob = str(audit.tail(1)["entries"])
        assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in blob and "REDACTED" in blob, blob
    finally:
        os.environ["EMBER_SUPPORT_DIR"] = old


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
