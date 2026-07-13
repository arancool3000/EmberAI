"""Hermetic tests for netsecurity.py — Wi-Fi encryption grading + remote-access exposure audit.
No OS, no network, no root: the grading is pure, the per-OS parsers run over sample command
output, and the probes are driven through the injectable _RUNNER. Run: python test_netsecurity.py"""
import netsecurity as ns


# ---- Wi-Fi grading -----------------------------------------------------------

def test_normalize_security_maps_every_tier():
    cases = {
        "Open": "open", "none": "open", "unsecured": "open",
        "WEP": "wep",
        "WPA-PSK": "wpa", "tkip": "wpa", "WPA Personal": "wpa",
        "WPA2-Personal": "wpa2", "RSNA": "wpa2", "WPA2 Enterprise": "wpa2",
        "WPA3-Personal": "wpa3", "SAE": "wpa3", "WPA2/WPA3": "wpa3",
        "": "unknown", "something weird": "unknown",
    }
    for raw, expect in cases.items():
        assert ns.normalize_security(raw) == expect, (raw, ns.normalize_security(raw))


def test_open_network_is_at_risk_and_unsafe():
    r = ns.assess_wifi("CoffeeShop", "Open")
    assert r["security"] == "OPEN" and r["score"] < 40 and r["rating"] == "At risk"
    assert r["safe"] is False
    assert r["findings"][0]["level"] == "critical" and r["findings"][0]["fix"]


def test_wep_is_treated_as_broken():
    r = ns.assess_wifi("Old", "WEP")
    assert r["score"] < 40 and r["findings"][0]["level"] == "critical"
    assert "wep" in r["findings"][0]["fix"].lower() or "wpa" in r["findings"][0]["fix"].lower()


def test_wpa_is_a_warning_not_a_failure():
    r = ns.assess_wifi("Legacy", "WPA-PSK TKIP")
    assert r["security"] == "WPA" and r["findings"][0]["level"] == "warning"
    assert r["rating"] in ("Needs attention", "Mostly secure")


def test_wpa2_is_ok_and_suggests_wpa3():
    r = ns.assess_wifi("Home", "WPA2-Personal CCMP")
    assert r["security"] == "WPA2" and r["safe"] is True
    assert r["findings"][0]["level"] == "ok" and "WPA3" in r["findings"][0]["fix"]


def test_wpa3_is_secure():
    r = ns.assess_wifi("Home", "WPA3-Personal SAE")
    assert r["score"] == 100 and r["rating"] == "Secure" and r["safe"] is True


def test_enterprise_flag_and_small_bonus():
    base = ns.assess_wifi("Corp", "WPA2")["score"]
    ent = ns.assess_wifi("Corp", "WPA2 Enterprise 802.1X")
    assert ent["enterprise"] is True and ent["score"] >= base


def test_unknown_security_is_honest():
    r = ns.assess_wifi("Mystery", "")
    assert r["security"] == "Unknown" and r["findings"][0]["level"] == "warning"


# ---- Wi-Fi OS parsers --------------------------------------------------------

def test_parse_macos_airport_current_network():
    text = (
        "    Interfaces:\n      en0:\n        Current Network Information:\n"
        "          MyHomeWiFi:\n            PHY Mode: 802.11ax\n"
        "            Security: WPA3 Personal\n            Channel: 149\n"
        "        Other Local Wi-Fi Networks:\n          Neighbor:\n            Security: WPA2 Personal\n")
    ssid, sec = ns.parse_macos_airport(text)
    assert ssid == "MyHomeWiFi" and "WPA3" in sec


def test_parse_windows_netsh():
    text = ("    SSID                   : OfficeNet\n"
            "    Authentication         : WPA2-Personal\n"
            "    Cipher                 : CCMP\n")
    ssid, sec = ns.parse_windows_netsh(text)
    assert ssid == "OfficeNet" and "WPA2" in sec and "CCMP" in sec


def test_parse_linux_nmcli_active_row_and_escaped_colon():
    ssid, sec = ns.parse_linux_nmcli("no:Neighbor:WPA1\nyes:HomeLinux:WPA2\nno:Cafe:")
    assert ssid == "HomeLinux" and sec == "WPA2"
    ssid2, sec2 = ns.parse_linux_nmcli(r"yes:My\:Net:WPA3")
    assert ssid2 == "My:Net" and sec2 == "WPA3"


def test_wifi_security_uses_injected_runner():
    import sys
    real = sys.platform
    ns._RUNNER = lambda cmd: ("    SSID : TestNet\n    Authentication : WPA3-Personal\n"
                              "    Cipher : CCMP\n")
    try:
        sys.platform = "win32"
        r = ns.wifi_security()
        assert r["ok"] and r["ssid"] == "TestNet" and r["security"] == "WPA3"
    finally:
        sys.platform = real
        ns._RUNNER = None


# ---- address scope -----------------------------------------------------------

def test_addr_scope_classification():
    for ip, scope in [("0.0.0.0", "all"), ("::", "all"), ("*", "all"), ("", "all"),
                      ("127.0.0.1", "loopback"), ("::1", "loopback"),
                      ("192.168.1.10", "lan"), ("10.1.2.3", "lan"), ("172.16.0.1", "lan"),
                      ("169.254.1.1", "lan"), ("8.8.8.8", "all"), ("203.0.113.7", "all")]:
        assert ns._addr_scope(ip) == scope, (ip, ns._addr_scope(ip))


def test_split_addr_ipv4_and_ipv6():
    assert ns._split_addr("0.0.0.0:3389") == ("0.0.0.0", 3389)
    assert ns._split_addr("[::]:22") == ("::", 22)
    assert ns._split_addr("192.168.1.5:5900") == ("192.168.1.5", 5900)


# ---- remote-access grading ---------------------------------------------------

def test_loopback_services_are_not_exposures():
    r = ns.assess_remote_exposure(
        [{"addr": "127.0.0.1:22", "proto": "tcp"}, {"addr": "::1:5900", "proto": "tcp"}],
        [], {"enabled": True})
    assert r["exposed_services"] == [] and r["score"] == 100 and r["rating"] == "Secure"


def test_rdp_on_all_interfaces_is_critical():
    r = ns.assess_remote_exposure([{"addr": "0.0.0.0:3389", "proto": "tcp", "process": "svchost"}],
                                  [], {"enabled": True})
    svc = r["exposed_services"][0]
    assert svc["service"] == "RDP" and svc["scope"] == "all"
    assert any(f["level"] == "critical" for f in r["findings"]) and r["score"] < 70


def test_ssh_on_lan_is_a_warning_not_critical():
    r = ns.assess_remote_exposure([{"addr": "192.168.1.5:22", "proto": "tcp", "process": "sshd"}],
                                  [], {"enabled": True})
    assert r["exposed_services"][0]["scope"] == "lan"
    levels = {f["level"] for f in r["findings"]}
    assert "warning" in levels and "critical" not in levels


def test_telnet_is_always_critical_even_on_lan():
    r = ns.assess_remote_exposure([{"addr": "192.168.1.5:23", "proto": "tcp"}], [], {"enabled": True})
    assert any(f["level"] == "critical" for f in r["findings"])
    assert "clear text" in " ".join(f["fix"] for f in r["findings"]).lower()


def test_active_inbound_session_is_flagged():
    r = ns.assess_remote_exposure(
        [], [{"local_port": 3389, "remote": "203.0.113.9:5512"}], {"enabled": True})
    assert r["inbound_sessions"] and r["inbound_sessions"][0]["service"] == "RDP"
    assert any("Active RDP session" in f["title"] for f in r["findings"])


def test_firewall_off_lowers_score():
    on = ns.assess_remote_exposure([], [], {"enabled": True})["score"]
    off = ns.assess_remote_exposure([], [], {"enabled": False})
    assert off["score"] < on
    assert any("Firewall is off" in f["title"] for f in off["findings"])


def test_clean_machine_reports_secure_with_no_findings_noise():
    r = ns.assess_remote_exposure([{"addr": "127.0.0.1:5432", "proto": "tcp"}], [], {"enabled": True})
    assert r["score"] == 100 and r["rating"] == "Secure"
    assert len(r["findings"]) == 1 and r["findings"][0]["level"] == "ok"


def test_non_remote_ports_are_ignored():
    r = ns.assess_remote_exposure([{"addr": "0.0.0.0:8080", "proto": "tcp", "process": "node"},
                                   {"addr": "0.0.0.0:443", "proto": "tcp"}], [], {"enabled": True})
    assert r["exposed_services"] == [] and r["score"] == 100


def test_multiple_criticals_can_reach_at_risk():
    listening = [{"addr": "0.0.0.0:3389", "proto": "tcp"}, {"addr": "0.0.0.0:5900", "proto": "tcp"},
                 {"addr": "0.0.0.0:23", "proto": "tcp"}]
    r = ns.assess_remote_exposure(listening, [], {"enabled": False})
    assert r["rating"] == "At risk" and r["score"] < 40
    assert {e["service"] for e in r["exposed_services"]} == {"RDP", "VNC", "Telnet"}


# ---- firewall probe (injected) -----------------------------------------------

def test_firewall_probe_reads_windows_state():
    import sys
    real = sys.platform
    ns._RUNNER = lambda cmd: "State ON\nState OFF\nState ON\n"
    try:
        sys.platform = "win32"
        fw = ns._gather_firewall()
        assert fw["enabled"] is False   # any OFF profile -> off
    finally:
        sys.platform = real
        ns._RUNNER = None


def test_firewall_probe_reads_macos_state():
    import sys
    real = sys.platform
    ns._RUNNER = lambda cmd: "Firewall is enabled. (State = 1)"
    try:
        sys.platform = "darwin"
        fw = ns._gather_firewall()
        assert fw["enabled"] is True
    finally:
        sys.platform = real
        ns._RUNNER = None


# ---- combined report + tool layer -------------------------------------------

def test_network_security_report_takes_the_weakest_area():
    orig = (ns.wifi_security, ns.remote_access_audit)
    ns.wifi_security = lambda: {"ok": True, "score": 100, "rating": "Secure", "summary": "wifi ok"}
    ns.remote_access_audit = lambda: {"ok": True, "score": 30, "rating": "At risk", "summary": "rdp open"}
    try:
        r = ns.network_security_report()
        assert r["ok"] and r["overall_score"] == 30 and r["overall_rating"] == "At risk"
        assert "wifi ok" in r["summary"] and "rdp open" in r["summary"]
    finally:
        ns.wifi_security, ns.remote_access_audit = orig


def test_tool_exports_consistent():
    assert set(ns.TOOL_DISPATCH) == {d["name"] for d in ns.TOOL_DECLARATIONS}
    assert ns.READONLY_TOOLS <= set(ns.TOOL_DISPATCH)
    assert ns.READONLY_TOOLS == {"wifi_security", "remote_access_audit", "network_security_report"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} netsecurity tests passed")
