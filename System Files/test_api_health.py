"""Hermetic tests for api_health.py — per-API issue tracking + smart ranking.

Uses an injectable clock (no real waiting) and a temp health file (no real state).
Run: python test_api_health.py
"""
import os
import tempfile
from pathlib import Path

import api_health as H


class _Clock:
    def __init__(self, t=100000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += float(dt)


def _fresh():
    """Point api_health at a fresh temp file + a controllable clock."""
    fd, path = tempfile.mkstemp(suffix="_apihealth.json")
    os.close(fd)
    H.HEALTH_FILE = Path(path)
    clk = _Clock()
    H._set_clock(clk)
    H.reset()
    return clk, path


def test_key_fingerprint_is_stable_and_hides_the_secret():
    a = H.key_fingerprint("AIzaSyABCDEF-super-secret-key")
    b = H.key_fingerprint("  AIzaSyABCDEF-super-secret-key  ")   # whitespace ignored
    assert a == b and len(a) == 8
    assert "secret" not in a and "AIza" not in a                 # non-reversible
    assert H.key_fingerprint("") == "nokey"
    assert H.api_id("Gemini", "k") == f"gemini:{H.key_fingerprint('k')}"
    assert H.api_id("claude") == "claude"


def test_classify_maps_errors_to_kinds():
    assert H.classify("429 RESOURCE_EXHAUSTED: quota") == H.RATE_LIMIT
    assert H.classify("You exceeded your current quota") == H.RATE_LIMIT
    assert H.classify("API key not valid (401)") == H.AUTH_ERROR
    assert H.classify("PERMISSION_DENIED") == H.AUTH_ERROR
    assert H.classify("model gemini-x NOT_FOUND (404)") == H.BAD_MODEL
    assert H.classify("503 UNAVAILABLE / overloaded") == H.SERVER_ERROR
    assert H.classify("DEADLINE_EXCEEDED: timed out") == H.TIMEOUT
    assert H.classify("some weird thing") == H.OTHER_ERROR


def test_rate_limit_starts_a_cooldown_that_expires():
    clk, _ = _fresh()
    aid = H.api_id("gemini", "key-A")
    H.record(aid, H.RATE_LIMIT)
    assert H.in_cooldown(aid) is True
    assert H.cooldown_remaining(aid) > 0
    clk.advance(59)
    assert H.in_cooldown(aid) is True
    clk.advance(2)                       # past the 60s rate-limit cooldown
    assert H.in_cooldown(aid) is False


def test_success_clears_cooldown_and_lifts_score():
    clk, _ = _fresh()
    aid = H.api_id("gemini", "key-A")
    H.record(aid, H.RATE_LIMIT)
    low = H.score(aid)
    assert H.in_cooldown(aid)
    H.record_success(aid)
    assert H.in_cooldown(aid) is False
    assert H.score(aid) > low


def test_rank_prefers_a_working_api_over_a_rate_limited_one():
    clk, _ = _fresh()
    good = H.api_id("gemini", "key-good")
    bad = H.api_id("gemini", "key-bad")
    H.record_success(good)
    H.record(bad, H.RATE_LIMIT)
    assert H.best([bad, good]) == good           # order-independent: the healthy one wins
    assert H.rank([bad, good])[0] == good
    assert H.rank([bad, good])[-1] == bad


def test_auth_error_is_avoided_far_longer_than_a_rate_limit():
    clk, _ = _fresh()
    rl = H.api_id("gemini", "rl")
    auth = H.api_id("gemini", "auth")
    H.record(rl, H.RATE_LIMIT)
    H.record(auth, H.AUTH_ERROR)
    clk.advance(120)                     # rate limit has cleared, auth error has not
    assert H.in_cooldown(rl) is False
    assert H.in_cooldown(auth) is True
    assert H.rank([auth, rl])[0] == rl   # prefer the rate-limited-but-recovered key over auth-failing


def test_score_decays_so_old_issues_matter_less():
    clk, _ = _fresh()
    old = H.api_id("gemini", "old")
    recent = H.api_id("gemini", "recent")
    H.record(old, H.SERVER_ERROR)
    clk.advance(600)                     # two half-lives later
    H.record(recent, H.SERVER_ERROR)
    # The just-happened error should hurt more than the 10-minute-old one.
    assert H.score(recent) < H.score(old)


def test_state_persists_across_reload():
    clk, path = _fresh()
    aid = H.api_id("openai", "sk-test")
    H.record(aid, H.RATE_LIMIT, detail="429 too many requests")
    # Simulate a restart: drop in-memory nothing (module has none) but re-read the file fresh.
    data = H._load()
    assert aid in data["apis"]
    assert data["apis"][aid]["counts"].get(H.RATE_LIMIT) == 1
    os.unlink(path)


def test_summary_reports_healthiest_first():
    clk, _ = _fresh()
    g = H.api_id("gemini", "g")
    c = "claude"
    H.record(g, H.RATE_LIMIT)
    H.record_success(c)
    s = H.summary([g, c])
    assert s["ok"] and s["healthiest"] == c
    assert s["apis"][0]["api"] == c
    by = {r["api"]: r for r in s["apis"]}
    assert by[g]["in_cooldown"] is True and by[c]["in_cooldown"] is False
    assert by[g]["counts"].get(H.RATE_LIMIT) == 1


def test_reset_forgets_history():
    clk, _ = _fresh()
    aid = H.api_id("gemini", "z")
    H.record(aid, H.AUTH_ERROR)
    assert H.in_cooldown(aid)
    H.reset(aid)
    assert H.in_cooldown(aid) is False
    assert H.note_for(aid) == "untested"


def test_record_error_classifies_and_records():
    clk, _ = _fresh()
    aid = H.api_id("gemini", "e")
    kind = H.record_error(aid, Exception("429 rate limit exceeded"))
    assert kind == H.RATE_LIMIT
    assert H.in_cooldown(aid)


def test_agent_wires_api_health():
    # agent.py imports google-genai (absent in CI), so verify the wiring from source.
    src = open(os.path.join(os.path.dirname(__file__), "agent.py"), encoding="utf-8").read()
    assert "import api_health" in src
    assert 'self._record_api("success")' in src           # success is remembered
    assert "self._record_api_error(e)" in src              # failures are remembered
    assert "api_health.score(self._api_hid(i)" in src      # backup keys tried in health order
    assert '"api_health_status": api_health.tool_status' in src   # tool is dispatched
    assert '"name": "api_health_status"' in src            # tool is declared


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print("  ok ", t.__name__)
        passed += 1
    print(f"\n{passed}/{len(tests)} api_health tests passed")
