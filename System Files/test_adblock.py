"""Tests for the system-wide ad blocker (network_adblock.py).

Hermetic: $EMBER_HOSTS_FILE points at a temp hosts file (so writes are direct, no admin /
no DNS flush / no touching the real /etc/hosts), and $EMBER_SUPPORT_DIR isolates state.
"""
import os
import tempfile

_D = tempfile.mkdtemp(prefix="ember_adblock_test_")
os.environ["EMBER_HOSTS_FILE"] = os.path.join(_D, "hosts")
os.environ["EMBER_SUPPORT_DIR"] = os.path.join(_D, "support")

import network_adblock as ab

_BASE = "127.0.0.1 localhost\n255.255.255.255 broadcasthost\n10.0.0.5 myserver\n"


def _reset():
    open(os.environ["EMBER_HOSTS_FILE"], "w").write(_BASE)
    try:
        os.remove(ab._state_path())
    except OSError:
        pass


def _hosts():
    return open(os.environ["EMBER_HOSTS_FILE"]).read()


def test_enable_sinkholes_and_preserves_existing():
    _reset()
    r = ab.adblock_enable()
    assert r["ok"] and r["blocked_domains"] > 50, r
    h = _hosts()
    assert "0.0.0.0 doubleclick.net" in h
    assert "127.0.0.1 localhost" in h and "10.0.0.5 myserver" in h  # untouched
    assert ab.adblock_status()["enabled"] is True


def test_disable_removes_only_our_block():
    _reset()
    ab.adblock_enable()
    r = ab.adblock_disable()
    assert r["ok"]
    h = _hosts()
    assert ab._BEGIN not in h and "doubleclick.net" not in h
    assert "127.0.0.1 localhost" in h and "10.0.0.5 myserver" in h
    assert ab.adblock_status()["enabled"] is False


def test_enable_is_idempotent_single_block():
    _reset()
    ab.adblock_enable()
    ab.adblock_enable()
    assert _hosts().count(ab._BEGIN) == 1  # no duplicate blocks


def test_add_domain_blocks_and_reapplies():
    _reset()
    ab.adblock_enable()
    ab.adblock_add_domain("https://ads.evil.com/path")  # normalized
    assert "0.0.0.0 ads.evil.com" in _hosts()


def test_allow_domain_unblocks():
    _reset()
    ab.adblock_enable()
    assert "0.0.0.0 doubleclick.net" in _hosts()
    ab.adblock_allow_domain("doubleclick.net")
    assert "0.0.0.0 doubleclick.net" not in _hosts()


def test_add_domain_validation():
    _reset()
    assert ab.adblock_add_domain("not a domain")["ok"] is False


def test_status_shape():
    _reset()
    s = ab.adblock_status()
    assert {"ok", "enabled", "blocked_domains", "hosts_file"} <= set(s)


def test_lists_reports_custom_and_allow():
    _reset()
    ab.adblock_add_domain("ads.foo.com")
    ab.adblock_allow_domain("doubleclick.net")
    lists = ab.adblock_lists()
    assert "ads.foo.com" in lists["extra"]
    assert "doubleclick.net" in lists["allow"]
    assert lists["blocked_domains"] >= 1


def test_remove_forgets_from_both_lists():
    _reset()
    ab.adblock_enable()
    ab.adblock_add_domain("ads.foo.com")
    assert "0.0.0.0 ads.foo.com" in _hosts()
    r = ab.adblock_remove("ads.foo.com")
    assert r["ok"] is True
    assert "ads.foo.com" not in ab.adblock_lists()["extra"]
    assert "0.0.0.0 ads.foo.com" not in _hosts()      # re-applied without it
    # allow-listed entries are removable too
    ab.adblock_allow_domain("doubleclick.net")
    assert ab.adblock_remove("doubleclick.net")["ok"] is True
    assert "doubleclick.net" not in ab.adblock_lists()["allow"]
    # removing something not in our lists is a no-op error
    assert ab.adblock_remove("nothere.example")["ok"] is False


def test_wiring_contract():
    names = {d["name"] for d in ab.TOOL_DECLARATIONS}
    assert {"adblock_enable", "adblock_disable", "adblock_status"} <= names
    assert "adblock_status" in ab.READONLY_TOOLS
    assert "adblock_enable" in ab.INTERACTION_TOOLS


def _run():
    import types
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
