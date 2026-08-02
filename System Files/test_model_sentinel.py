"""Tests that the "auto" model sentinel never reaches a provider API.

The reported error:

    AI error: 404 NOT_FOUND. models/auto is not found for API version v1beta,
    or is not supported for generateContent.

"auto" is a UI sentinel meaning "pick the best free model" — the first entry in the model
dropdown. `models.resolve()` exists to turn it into a real id. Two call sites skipped it:

    mdl = model if model and "claude" not in model.lower() else "gemini-3.1-flash-lite"

"auto" is truthy and contains no "claude", so it fell through the conditional untouched and was
handed to the API as a model name.
"""
import re
from pathlib import Path

import pytest

import models

HERE = Path(__file__).resolve().parent


# --- the resolver ----------------------------------------------------------------
def test_the_sentinel_resolves_to_a_real_model():
    resolved = models.resolve("auto")
    assert resolved and resolved != "auto"
    assert resolved == models.RECOMMENDED_FREE


def test_empty_and_none_resolve_too():
    assert models.resolve("") == models.RECOMMENDED_FREE
    assert models.resolve(None) == models.RECOMMENDED_FREE


def test_a_real_id_passes_through():
    assert models.resolve("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"


def test_retired_ids_are_remapped():
    """The same call site now also picks up dead-model remapping for free."""
    for dead, live in models._DEAD_MODELS.items():
        assert models.resolve(dead) == live


def test_the_sentinel_is_what_the_dropdown_offers():
    """Guards the premise: if the UI stopped offering "auto", these tests would be theatre."""
    ids = [mid for _prov, mid, _label, _hint in models.all_choices()]
    assert "auto" in ids
    assert ids[0] == "auto", "auto should be the first, default choice"


# --- the call sites that got it wrong ---------------------------------------------
@pytest.mark.parametrize("module", ["ai_detect.py", "ember_browser.py"])
def test_gemini_calls_resolve_the_model_first(module):
    """Both files had the identical broken line. Neither may hand a raw model id to the API."""
    src = (HERE / module).read_text(encoding="utf-8")
    assert 'mdl = model if model and "claude" not in model.lower()' not in src, \
        "the unresolved conditional is back"
    assert "_models.resolve(" in src


def test_ai_detect_does_not_send_the_sentinel(monkeypatch):
    """Drive the real function with a stubbed client and assert what it would have sent."""
    import sys
    import types as _types
    sent = {}

    fake_genai = _types.ModuleType("google.genai")

    class _Client:
        def __init__(self, api_key=None):
            self.models = self

        def generate_content(self, model=None, contents=None):
            sent["model"] = model
            return _types.SimpleNamespace(text="ok")

    fake_genai.Client = _Client
    fake_google = _types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    import ai_detect
    # Exactly what the UI stores when the user leaves the model picker on "Auto".
    ai_detect._ask_model("hello", {"gemini_api_key": "k", "model_id": "auto",
                                   "provider": "gemini"})
    assert sent["model"] != "auto"
    assert sent["model"] == models.RECOMMENDED_FREE


# --- nothing else leaks it ---------------------------------------------------------
def test_no_module_passes_an_unresolved_setting_straight_to_generate_content():
    """A sweep rather than a spot check: any `model=` argument to generate_content must be a
    literal string or something that went through resolve()."""
    offenders = []
    for path in HERE.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"generate_content\(\s*\n?\s*model=([^,\)]+)", src):
            arg = m.group(1).strip()
            if arg.startswith(('"', "'")):
                continue                      # a hardcoded model id is fine
            if "resolve(" in arg:
                continue                      # explicitly resolved at the call
            if arg == "mdl":
                # Resolved on the line above; the two files that do this are asserted
                # individually in test_gemini_calls_resolve_the_model_first.
                if "_models.resolve(" in src or "models.resolve(" in src:
                    continue
            offenders.append(f"{path.name}: model={arg}")
    assert not offenders, offenders
