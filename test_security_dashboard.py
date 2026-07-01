"""Hermetic tests for security_suite.py — the pure Norton-style score (compute_dashboard), the
Software Updater output parsers, and the tool exports. No OS access / network.
(Named test_security_dashboard to avoid the pre-existing, unrelated test_security_suite.py.)
Run: python test_security_dashboard.py"""
import security_suite as ss


def test_compute_dashboard_all_on_is_100():
    d = ss.compute_dashboard({k: True for k in ss._WEIGHTS})
    assert d["score"] == 100 and d["grade"] == "A" and d["rating"] == "excellent"
    assert d["recommendations"] == []
    assert all(c["ok"] for c in d["components"])


def test_compute_dashboard_all_off_is_0():
    d = ss.compute_dashboard({k: False for k in ss._WEIGHTS})
    assert d["score"] == 0 and d["grade"] == "F" and d["rating"] == "at risk"
    assert len(d["recommendations"]) == len(ss._WEIGHTS)


def test_compute_dashboard_partial_and_priority():
    signals = {k: False for k in ss._WEIGHTS}
    signals["realtime_protection"] = True   # 18
    signals["malware_engine"] = True        # 12
    d = ss.compute_dashboard(signals)
    assert d["score"] == 30
    # highest-weight missing item (updates_current, 14) -> first recommendation
    assert "update" in d["recommendations"][0].lower()


def test_recommendation_items_keep_the_component_key_for_resolve_buttons():
    signals = {k: False for k in ss._WEIGHTS}
    signals["realtime_protection"] = True
    d = ss.compute_dashboard(signals)
    items = d["recommendation_items"]
    assert len(items) == len(d["recommendations"])
    assert items[0]["key"] == "updates_current"       # same priority order as recommendations
    assert items[0]["fix"] == d["recommendations"][0]
    assert all(set(i.keys()) == {"key", "fix"} for i in items)
    assert all(i["key"] in ss._WEIGHTS for i in items)


def test_weights_sum_to_100():
    assert sum(ss._WEIGHTS.values()) == 100


def test_macos_softwareupdate_parser():
    out = (
        "Software Update Tool\n\nFinding available software\n"
        "* Label: macOS Sequoia 15.5-24F74\n"
        "\tTitle: macOS Sequoia 15.5, Version: 15.5, Size: 4012345KiB, Recommended: YES,\n"
        "* Label: Safari18.5SequoiaAuto-18.5\n"
        "\tTitle: Safari, Version: 18.5, Size: 123456KiB,\n"
    )
    items = ss._parse_macos_softwareupdate(out)
    assert "macOS Sequoia 15.5-24F74" in items
    assert "Safari18.5SequoiaAuto-18.5" in items
    assert len(items) == 2


def test_brew_outdated_parser():
    assert ss._brew_outdated("node\npython@3.12\ngit\n") == ["node", "python@3.12", "git"]
    assert ss._brew_outdated("") == []


def test_exports_consistent():
    assert set(ss.TOOL_DISPATCH) == {d["name"] for d in ss.TOOL_DECLARATIONS}
    assert ss.READONLY_TOOLS == {"security_dashboard", "software_update_check"}
    assert ss.INTERACTION_TOOLS == set()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} security_dashboard tests passed")
