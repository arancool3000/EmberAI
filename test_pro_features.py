"""Tests for Ember's permanently-free access policy, VPN manager, and directory scanning
(plan.py, vpn.py, antivirus.scan_directory).

Hermetic: all state in a throwaway dir; no network; no WireGuard required.

    pytest test_pro_features.py
    python test_pro_features.py
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ember_pro_test_")
os.environ["EMBER_SUPPORT_DIR"] = _TMP
os.environ.pop("EMBER_PLAN", None)
os.environ.pop("VIRUSTOTAL_API_KEY", None)

import plan
import vpn
import antivirus

antivirus.set_config(vt_api_key="", vt_hash_lookup=False, vt_upload_unknown=False)


# ------------------------------- plans -------------------------------------

def test_every_feature_is_free_by_default():
    assert plan.current_plan() == "free"
    g = plan.get_plan()
    assert g["all_features_free"] and not g["is_pro"]
    assert plan.has("vpn") and plan.has("sandbox") and plan.has("advanced_antivirus")
    assert plan.require("vpn") is None


def test_plan_compatibility_call_cannot_create_a_paywall():
    assert plan.set_plan("free")["ok"]
    assert plan.current_plan() == "free"
    assert plan.has("antivirus") is True
    assert plan.has("vpn") is True
    assert plan.set_plan("pro")["plan"] == "free"
    assert plan.require("vpn") is None


def test_set_plan_is_a_safe_noop_for_old_plugins():
    assert plan.set_plan("enterprise")["ok"] is True
    assert plan.current_plan() == "free"


def test_list_pro_features():
    r = plan.list_pro_features()
    assert r["ok"] and r["all_features_free"]
    assert r["pro_features"] == [] and r["benefits"] == []


# -------------------------------- vpn --------------------------------------

def test_vpn_add_list_remove():
    conf = Path(_TMP) / "sample.conf"
    conf.write_text("[Interface]\nPrivateKey = x\n[Peer]\nEndpoint = 1.2.3.4:51820\n")
    assert vpn.add_location("uk-london", str(conf))["ok"]
    listing = vpn.list_locations()
    assert "uk-london" in listing["locations"]
    assert "suggested" in listing and "wireguard_installed" in listing
    assert vpn.remove_location("uk-london")["ok"]
    assert "uk-london" not in vpn.list_locations()["locations"]


def test_vpn_add_missing_config_errors():
    assert vpn.add_location("nowhere", "/no/such/file.conf")["ok"] is False


def test_vpn_status_is_honest_without_wireguard():
    s = vpn.status()
    assert s["ok"] is True
    # No WireGuard here -> must report not connected, never fake a tunnel.
    if not s["wireguard_installed"]:
        assert s["connected"] is False and s["active_interfaces"] == []


def test_vpn_connect_without_wireguard_refuses():
    if not vpn.wireguard_available():
        r = vpn.connect("uk-london")
        assert r["ok"] is False and "wireguard" in r["error"].lower()


# ------------------------- advanced antivirus ------------------------------

def test_scan_directory_flags_and_quarantines():
    d = Path(_TMP) / "scanme"
    d.mkdir()
    (d / "notes.txt").write_text("totally fine text\n")
    (d / "evil.bin").write_bytes(antivirus.EICAR_SIG)
    r = antivirus.scan_directory(str(d), deep=False)
    assert r["ok"] and r["scanned"] >= 2, r
    verdicts = {f["path"].split("/")[-1]: f["verdict"] for f in r["flagged"]}
    assert verdicts.get("evil.bin") == "malicious", r
    assert not (d / "evil.bin").exists()  # quarantined (moved out)


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
