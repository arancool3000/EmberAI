"""Tests for memory.py — the "learns about you" layer: auto fact-extraction, relevance
retrieval, and the profile view. State is redirected to a temp file. Run: python test_memory.py
"""
import os
import sys
import tempfile
from pathlib import Path

# Redirect the memory file to a throwaway location BEFORE importing the module.
_TMP = tempfile.mkdtemp(prefix="ember_mem_test_")
os.environ["EMBER_SUPPORT_DIR"] = _TMP   # (harmless even if memory uses its own dir)

import memory
# Force the store into our temp dir regardless of how _data_dir resolves.
memory.MEMORY_PATH = Path(_TMP) / "memory.json"


def _reset():
    try:
        memory.MEMORY_PATH.unlink()
    except FileNotFoundError:
        pass


# ---- extraction (pure) ----

def test_extract_identity():
    facts = dict((k, v) for k, v, _c in memory.extract_facts("My name is Sam and my timezone is GMT."))
    assert facts.get("name") == "Sam", facts
    assert "GMT" in facts.get("timezone", ""), facts


def test_extract_call_me():
    facts = {k: v for k, v, _c in memory.extract_facts("call me Alex please")}
    assert facts.get("name") == "Alex", facts


def test_extract_preferences():
    out = memory.extract_facts("I prefer concise answers. I hate long emails.")
    vals = [v for _k, v, _c in out]
    assert any("prefers concise answers" in v for v in vals), out
    assert any("dislikes long emails" in v for v in vals), out


def test_extract_note_command():
    out = memory.extract_facts("Remember that my project lives in ~/code/ember")
    assert out and out[0][2] == "note"
    assert "~/code/ember" in out[0][1], out


def test_questions_are_not_learned():
    assert memory.extract_facts("do you like pizza?") == []
    assert memory.extract_facts("what is my name?") == []


def test_secrets_are_never_learned():
    assert memory.extract_facts("my password is hunter2") == []
    assert memory.extract_facts("remember that my api key is sk-abc123") == []


def test_directive_rule_is_learned_only_at_clause_start():
    out = memory.extract_facts("Always back up before deleting")
    assert out and out[0][2] == "preference", out
    assert "back up before deleting" in out[0][1].lower()
    # "always" mid-sentence (a description, not a directive) must NOT be learned.
    assert memory.extract_facts("the app always crashes on launch") == []


def test_no_false_role_from_im_a():
    # The noisy "I'm a <x>" pattern was removed — casual phrasing shouldn't pollute memory.
    assert memory.extract_facts("i'm a bit tired today") == []


# ---- learn + persist ----

def test_learn_from_message_persists_and_dedupes():
    _reset()
    r1 = memory.learn_from_message("My name is Jordan. I prefer dark mode.")
    assert set(r1["learned"]) >= {"name"}, r1
    # Re-stating the same facts shouldn't re-learn (idempotent).
    r2 = memory.learn_from_message("My name is Jordan.")
    assert r2["learned"] == [], r2
    # Changing a fact updates it.
    memory.learn_from_message("My name is Jordan Lee.")
    assert memory.recall("name")["facts"]["name"]["value"] == "Jordan Lee"


# ---- relevance retrieval ----

def test_get_relevant_facts_prioritises_query_match():
    _reset()
    memory.remember("editor", "VS Code", category="setup")
    memory.remember("favourite_food", "sushi", category="general")
    memory.remember("car", "blue Tesla", category="general")
    out = memory.get_relevant_facts("what editor should I use for coding?", max_facts=3)
    # The editor fact must be present and appear before unrelated ones.
    assert "VS Code" in out
    assert out.index("editor") < out.index("car") if "car" in out else True


def test_get_relevant_facts_empty_query_is_newest_first():
    _reset()
    memory.remember("a", "first")
    memory.remember("b", "second")
    out = memory.get_relevant_facts("", max_facts=5)
    assert out.splitlines()[0].startswith("- b:"), out  # newest first


def test_profile_groups_by_category():
    _reset()
    memory.remember("name", "Sam", category="identity")
    memory.remember("pref:dark", "prefers dark mode", category="preference")
    p = memory.profile()
    assert p["count"] == 2
    cats = p["by_category"]
    assert "identity" in cats and "preference" in cats
    assert any(f["value"] == "Sam" for f in cats["identity"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} memory tests passed")
