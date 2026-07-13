"""Hermetic tests for offline.py — Offline Mode's pure core (flag, network-tool
classification, uniform error). No network assertions. Run: python test_offline.py"""
import offline


def test_flag_roundtrip():
    offline.set_offline(True)
    assert offline.is_offline() is True
    offline.set_offline(False)
    assert offline.is_offline() is False


def test_network_tools_classified():
    for t in ("web_search", "http_get", "weather_lookup", "send_email", "ask_claude",
              "browser_open", "adblock_update_from_url", "stock_quote"):
        assert offline.requires_network(t), t


def test_local_tools_not_network():
    for t in ("read_file", "list_directory", "run_shell", "take_screenshot", "calculator",
              "get_system_info", "remember", "hash_text", "click", "type_text"):
        assert not offline.requires_network(t), t


def test_offline_error_shape():
    e = offline.offline_error("web_search")
    assert e["ok"] is False
    assert e["offline"] is True
    assert "web_search" in e["error"]
    assert "Offline Mode" in e["error"]


def test_network_ok_returns_bool_without_raising():
    assert isinstance(offline.network_ok(timeout=0.2), bool)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} offline tests passed")
