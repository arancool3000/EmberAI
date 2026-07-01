"""Hermetic tests for Ember Browser's in-page ad/tracker blocking (ember_browser.py).

ember_browser.py hard-imports PyQt6-WebEngine at module load, which CI doesn't install (only
rapidfuzz is installed for the test suite) — so this test extracts and executes the pure
_host_is_blocked function's source via ast instead of `import ember_browser`, exercising the
REAL implementation without needing Qt at all.
Run: python test_ember_browser_adblock.py"""
import ast
import os
import time

_SRC_PATH = os.path.join(os.path.dirname(__file__), "ember_browser.py")
_SRC = open(_SRC_PATH, encoding="utf-8").read()


def _load_host_is_blocked():
    tree = ast.parse(_SRC)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_host_is_blocked":
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, _SRC_PATH, "exec"), ns)
            return ns["_host_is_blocked"]
    raise AssertionError("_host_is_blocked not found in ember_browser.py")


_host_is_blocked = _load_host_is_blocked()


def test_exact_match():
    assert _host_is_blocked("doubleclick.net", frozenset({"doubleclick.net"}))


def test_subdomain_matches():
    assert _host_is_blocked("ads.doubleclick.net", frozenset({"doubleclick.net"}))
    assert _host_is_blocked("a.b.doubleclick.net", frozenset({"doubleclick.net"}))


def test_unrelated_host_not_blocked():
    assert not _host_is_blocked("example.com", frozenset({"doubleclick.net"}))


def test_suffix_must_be_label_aligned():
    # "notdoubleclick.net" must NOT match "doubleclick.net" - no accidental substring match
    assert not _host_is_blocked("notdoubleclick.net", frozenset({"doubleclick.net"}))


def test_empty_domain_set_blocks_nothing():
    assert not _host_is_blocked("ads.doubleclick.net", frozenset())


def test_scales_to_a_large_merged_list():
    # A big pulled-in list (e.g. StevenBlack's hosts, merged via network_adblock.blocklist())
    # can be 100k+ domains - this must stay an O(depth) walk, not an O(n) scan per request, or
    # the browser would stall on every single network request once a big list is enabled.
    big = frozenset(f"tracker{i}.example" for i in range(200000)) | {"doubleclick.net"}
    start = time.monotonic()
    for _ in range(2000):
        _host_is_blocked("ads.doubleclick.net", big)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"too slow: {elapsed:.2f}s for 2000 lookups against a 200k-domain set"


def test_guard_shares_the_system_wide_adblock_list():
    """The in-browser interceptor must pull in network_adblock's list (builtin + anything the
    user has added, including a big pulled-in list) instead of staying stuck on its own small
    hardcoded set — that disconnect is why enabling a bigger list didn't help the browser."""
    assert "import network_adblock" in _SRC
    assert "network_adblock.blocklist()" in _SRC


def test_guard_uses_the_scalable_lookup_not_a_linear_scan():
    assert "_host_is_blocked(host, self._domains)" in _SRC
    assert "for d in _TRACKERS" not in _SRC   # the old O(n) per-request scan is gone


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
