"""Hermetic tests for voice.speak() engine routing. The actual TTS engines are stubbed,
so we only verify which engine speak() picks for a given config and that it always falls
back to the system voice. Run: python test_voice_tts.py"""
import voice


def _route(cfg, edge=True, gem=True, snd=True):
    """Configure speak(), stub engines (returning given success flags), return which ran."""
    calls = []
    voice.set_tts_config(cfg)
    voice._edge_tts = lambda t: (calls.append("edge"), edge)[1]
    voice._gemini_tts = lambda t: (calls.append("gemini"), gem)[1]
    voice._soundtools_tts = lambda t: (calls.append("soundtools"), snd)[1]
    voice._system_tts = lambda t: calls.append("system")
    voice.speak("hello there")
    return calls


def test_system_is_default():
    assert _route({"tts_engine": "system"}) == ["system"]


def test_edge_needs_no_key():
    # The whole point: Edge works with NO api key.
    assert _route({"tts_engine": "edge"}) == ["edge"]


def test_edge_falls_back_to_system_when_unavailable():
    # e.g. edge-tts not installed -> _edge_tts returns False -> system voice.
    assert _route({"tts_engine": "edge"}, edge=False) == ["edge", "system"]


def test_gemini_requires_key():
    assert _route({"tts_engine": "gemini"}) == ["system"]                  # no key -> system
    assert _route({"tts_engine": "gemini", "gemini_api_key": "k"}) == ["gemini"]


def test_soundtools_requires_url_not_key():
    # soundtools.io has no key; the path is gated on a custom URL now, not a key.
    assert _route({"tts_engine": "soundtools"}) == ["system"]              # no url -> system
    assert _route({"tts_engine": "soundtools", "soundtools_api_key": "k"}) == ["system"]
    assert _route({"tts_engine": "soundtools", "soundtools_url": "https://x/tts"}) == ["soundtools"]


def test_empty_text_speaks_nothing():
    assert _route({"tts_engine": "edge"}) and voice.speak("") is None
    # A whitespace-only string should also do nothing.
    calls = []
    voice._system_tts = lambda t: calls.append("system")
    voice.speak("   ")
    assert calls == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} voice TTS routing tests passed")
