"""Hermetic tests for setup_tour.py — the first-run tour's pure logic (friendly labels,
install plan, per-level config, when to show). No Qt. Run: python test_setup_tour.py"""
import setup_tour as st


def test_friendly_labels_for_beginner():
    assert "Free offline AI" in st.friendly_model_label("ollama", "Local (Ollama)", "beginner")
    assert "Free online AI" in st.friendly_model_label("gemini-3.1-flash-lite", "Gemini", "beginner")
    assert "Advanced AI" in st.friendly_model_label("claude-opus-4-8", "Claude", "some")
    assert "Recommended" in st.friendly_model_label("auto", "Auto", "beginner")


def test_expert_sees_technical_names():
    assert st.friendly_model_label("ollama", "Local (Ollama)", "expert") == "Local (Ollama)"
    assert st.friendly_model_label("gemini-3.1-flash-lite", "Gemini X", "expert") == "Gemini X"


def test_install_plan_per_os():
    mac = st.ollama_install_plan("darwin")
    assert mac["method"] in ("brew", "download") and mac["label"]
    win = st.ollama_install_plan("win32")
    assert win["method"] == "download" and win["url"].startswith("https://")
    lin = st.ollama_install_plan("linux")
    assert lin["method"] == "script" and isinstance(lin["command"], list)


def test_recommended_model_and_settings():
    assert st.recommended_model_pull("beginner") == "llama3.2"
    assert st.recommended_model_pull("expert") == "qwen2.5"
    b = st.recommended_settings("beginner")
    assert b["experience_level"] == "beginner" and b["setup_complete"] is True
    assert b["lean_tools"] is True and b["wake_visual"] == "glow"
    e = st.recommended_settings("expert")
    assert e["setup_complete"] is True and "lean_tools" not in e   # experts keep their own defaults


def test_should_show_logic():
    assert st.should_show({}) is True                                  # fresh, nothing set
    assert st.should_show({"setup_complete": True}) is False           # already toured
    assert st.should_show({"gemini_api_key": "k"}) is False            # a brain is configured
    assert st.should_show({"model_id": "ollama"}) is False             # local brain chosen


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} setup_tour tests passed")
