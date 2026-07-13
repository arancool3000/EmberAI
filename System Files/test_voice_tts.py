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


def test_audio_player_routes_mp3_away_from_wav_only_players():
    # The neural engines emit MP3. Routing an MP3 to a WAV-only player (Linux aplay) is silent
    # failure -> Ember went mute. _audio_player_cmd must refuse aplay for MP3 and pick an
    # MP3-capable player when one exists.
    import shutil
    orig_plat, orig_which = voice.sys.platform, shutil.which
    try:
        voice.sys.platform = "linux"
        shutil.which = lambda name, *a, **k: "/usr/bin/aplay" if name == "aplay" else None
        assert voice._audio_player_cmd("/tmp/x.mp3") is None          # only wav player -> refuse
        shutil.which = lambda name, *a, **k: ("/usr/bin/" + name) if name in ("ffplay", "aplay") else None
        cmd = voice._audio_player_cmd("/tmp/x.mp3")
        assert cmd and cmd[0] == "ffplay"                             # mp3 -> mp3-capable player
        cmd = voice._audio_player_cmd("/tmp/x.wav")
        assert cmd and cmd[0] == "aplay"                             # wav -> aplay is fine
    finally:
        voice.sys.platform, shutil.which = orig_plat, orig_which


def test_play_audio_file_reports_false_when_no_player_can_handle_it():
    import shutil
    orig_plat, orig_which = voice.sys.platform, shutil.which
    try:
        voice.sys.platform = "linux"
        shutil.which = lambda name, *a, **k: None                    # nothing installed
        assert voice._play_audio_file("/tmp/x.mp3") is False         # -> caller falls back to system voice
    finally:
        voice.sys.platform, shutil.which = orig_plat, orig_which


def test_windows_player_uses_mediaplayer_not_wav_only_soundplayer():
    orig_plat = voice.sys.platform
    try:
        voice.sys.platform = "win32"
        cmd = voice._audio_player_cmd(r"C:\Temp\ember_tts.mp3")
        joined = " ".join(cmd)
        assert "MediaPlayer" in joined and "SoundPlayer" not in joined
    finally:
        voice.sys.platform = orig_plat


def test_fix_assistant_name():
    # The headline bug: "ember" heard as "amber".
    assert voice.fix_assistant_name("hey amber what's the time") == "hey Ember what's the time"
    assert voice.fix_assistant_name("Amber, open chrome") == "Ember, open chrome"
    assert voice.fix_assistant_name("ambre play music") == "Ember play music"
    # Already correct / unrelated words are left alone.
    assert voice.fix_assistant_name("Ember is great") == "Ember is great"
    assert voice.fix_assistant_name("") == ""


def test_is_stop_phrase():
    for p in ("stop", "Stop.", "goodbye", "that's all", "never mind", "bye bye", "thanks ember"):
        assert voice.is_stop_phrase(p), p
    for p in ("what's the weather", "stop the music please", "ember tell me a joke", ""):
        assert not voice.is_stop_phrase(p), p


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
