"""Hermetic tests for live_voice — the Gemini Live API natural-voice session.

No network, no genai, no audio: a fake async session yields scripted server messages,
a fake mic feeds frames, a fake player records audio. We test the pure message parser
and the sender/receiver/drive state machine. The real websocket is device-verified.

Run: python test_live_voice.py
"""
import asyncio
from types import SimpleNamespace as NS

import live_voice as lv


# ---- fakes ----------------------------------------------------------------

class FakeSession:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send_realtime_input(self, audio=None, **_kw):
        self.sent.append(audio)

    async def receive(self):
        for m in self._messages:
            await asyncio.sleep(0)
            yield m


class FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)

    async def read(self):
        await asyncio.sleep(0)
        return self._frames.pop(0) if self._frames else None


class FakePlayer:
    def __init__(self):
        self.fed = []
        self.cleared = 0
        self.closed = False

    async def feed(self, pcm):
        self.fed.append(pcm)

    def clear(self):
        self.cleared += 1

    def close(self):
        self.closed = True


def _msg(audio=None, user=None, ember=None, interrupted=False, turn_complete=False, text=None):
    sc = NS(
        input_transcription=(NS(text=user) if user is not None else None),
        output_transcription=(NS(text=ember) if ember is not None else None),
        interrupted=interrupted,
        turn_complete=turn_complete,
    )
    return NS(data=audio, server_content=sc, text=text)


def _handlers():
    log = {"user": [], "ember": [], "state": [], "turns": 0, "interrupts": 0, "errors": []}
    return log, {
        "on_user_text": lambda t: log["user"].append(t),
        "on_ember_text": lambda t: log["ember"].append(t),
        "on_state": lambda s: log["state"].append(s),
        "on_turn_complete": lambda: log.__setitem__("turns", log["turns"] + 1),
        "on_interrupted": lambda: log.__setitem__("interrupts", log["interrupts"] + 1),
        "on_error": lambda e: log["errors"].append(e),
    }


# ---- parse_message --------------------------------------------------------

def test_parse_audio_only():
    p = lv.parse_message(_msg(audio=b"\x01\x02"))
    assert p["audio"] == b"\x01\x02"
    assert p["user_text"] is None and p["ember_text"] is None


def test_parse_transcripts():
    p = lv.parse_message(_msg(user="hello there", ember="hi back"))
    assert p["user_text"] == "hello there"
    assert p["ember_text"] == "hi back"


def test_parse_interrupt_and_turn_complete():
    p = lv.parse_message(_msg(interrupted=True, turn_complete=True))
    assert p["interrupted"] is True and p["turn_complete"] is True


def test_parse_text_fallback():
    p = lv.parse_message(_msg(text="model said this"))
    assert p["ember_text"] == "model said this"


def test_parse_none_and_empty_audio():
    assert lv.parse_message(None)["audio"] is None
    assert lv.parse_message(_msg(audio=b""))["audio"] is None


# ---- sender ---------------------------------------------------------------

def test_sender_streams_frames_then_stops_on_drain():
    async def run():
        sess = FakeSession([])
        mic = FakeMic([b"aaa", b"bbb"])  # None after -> sender stops
        stop = asyncio.Event()
        await lv._sender(sess, mic, stop)
        return sess
    sess = asyncio.run(run())
    assert len(sess.sent) == 2


def test_sender_respects_stop_event():
    async def run():
        sess = FakeSession([])
        mic = FakeMic([b"x"] * 100)
        stop = asyncio.Event()
        stop.set()
        await lv._sender(sess, mic, stop)
        return sess
    sess = asyncio.run(run())
    assert sess.sent == []


# ---- receiver / drive -----------------------------------------------------

def test_receiver_surfaces_audio_and_transcripts():
    async def run():
        log, h = _handlers()
        sess = FakeSession([
            _msg(user="what's the weather"),
            _msg(audio=b"\x10\x20", ember="It's sunny."),
            _msg(turn_complete=True),
        ])
        player = FakePlayer()
        await lv._receiver(sess, player, h, asyncio.Event())
        return log, player
    log, player = asyncio.run(run())
    assert log["user"] == ["what's the weather"]
    assert log["ember"] == ["It's sunny."]
    assert player.fed == [b"\x10\x20"]
    assert "speaking" in log["state"]
    assert log["turns"] == 1


def test_receiver_handles_barge_in():
    async def run():
        log, h = _handlers()
        sess = FakeSession([
            _msg(audio=b"\x01"),         # Ember talking
            _msg(interrupted=True),       # user barged in
        ])
        player = FakePlayer()
        await lv._receiver(sess, player, h, asyncio.Event())
        return log, player
    log, player = asyncio.run(run())
    assert player.cleared == 1
    assert log["interrupts"] == 1
    assert log["state"][-1] == "listening"


def test_drive_runs_sender_and_receiver_together():
    async def run():
        log, h = _handlers()
        sess = FakeSession([_msg(user="hi"), _msg(ember="hello"), _msg(turn_complete=True)])
        mic = FakeMic([b"frame1", b"frame2"])
        player = FakePlayer()
        await lv._drive(sess, mic, player, h, asyncio.Event())
        return log, sess
    log, sess = asyncio.run(run())
    assert log["user"] == ["hi"] and log["ember"] == ["hello"]
    assert len(sess.sent) >= 1  # sender pushed at least one frame before drain


# ---- config / availability ------------------------------------------------

def test_config_includes_voice_and_transcription():
    v = lv.LiveVoice("KEY", voice="Puck", system_instruction="be kind")
    cfg = v._config()
    assert cfg["response_modalities"] == ["AUDIO"]
    assert cfg["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Puck"
    assert "input_audio_transcription" in cfg and "output_audio_transcription" in cfg
    assert cfg["system_instruction"] == "be kind"


def test_start_without_key_errors():
    v = lv.LiveVoice("")
    r = v.start()
    assert r["ok"] is False and "key" in r["error"].lower()


def test_start_without_deps_errors_cleanly():
    # In this sandbox genai/pyaudio are absent -> available() False -> friendly error.
    v = lv.LiveVoice("KEY")
    r = v.start()
    assert r["ok"] is False
    assert "genai" in r["error"].lower() or "pyaudio" in r["error"].lower()


def test_audio_blob_fallback_without_genai():
    blob = lv._audio_blob(b"abc")
    # genai absent here -> dict fallback with the right mime.
    assert isinstance(blob, dict)
    assert blob["mime_type"] == lv.AUDIO_IN_MIME and blob["data"] == b"abc"


def test_mic_is_not_orphaned_when_the_player_fails_to_open():
    # Regression: `mic, player = _PyAudioMic(), _PyAudioPlayer()` opened the mic first, so if the
    # player raised, the tuple never bound, `mic` stayed None, and the open input stream leaked
    # (device stays captured until the process exits). The fix constructs them sequentially and
    # closes the mic on failure.
    import inspect
    src = inspect.getsource(lv.LiveVoice._main)
    assert "mic, player = _PyAudioMic(), _PyAudioPlayer()" not in src   # buggy tuple form gone
    assert "mic = _PyAudioMic()" in src and "player = _PyAudioPlayer()" in src
    assert "mic.close()" in src                                          # closed on player failure


def test_looks_like_bad_model_detects_model_not_found_style_errors():
    # These are the actual close-reason texts the Live API sends when a dated preview model ID
    # has been retired (retrying the SAME model then just fails identically forever).
    assert lv._looks_like_bad_model(
        Exception("received 1008 (policy violation) models/gemini-2.5-flash-preview-native-audio-dialog "
                  "is not found for API version v1beta, or is not supported for bidiGenerateContent"))
    assert lv._looks_like_bad_model(Exception("Policy Violation"))
    assert lv._looks_like_bad_model(Exception("model not found"))


def test_looks_like_bad_model_ignores_transient_errors():
    assert not lv._looks_like_bad_model(Exception("Connection reset by peer"))
    assert not lv._looks_like_bad_model(Exception("timed out"))
    assert not lv._looks_like_bad_model(TimeoutError())


def test_default_model_is_not_the_retired_dialog_naming():
    # Regression guard: the old "-preview-native-audio-dialog" suffix naming was retired by
    # Google and every connection attempt failed with 1008 (policy violation) - the default
    # must never regress back to that exact stale ID.
    assert lv.DEFAULT_MODEL != "gemini-2.5-flash-preview-native-audio-dialog"
    assert "native-audio" in lv.DEFAULT_MODEL


def test_fallback_models_are_distinct_from_the_default():
    assert lv.DEFAULT_MODEL not in lv.FALLBACK_MODELS
    assert len(lv.FALLBACK_MODELS) == len(set(lv.FALLBACK_MODELS))


# ---- tool calling (voice that ACTS — the "better than GPT Live" bit) -------

def _tool_msg(calls):
    fcs = [NS(id=c.get("id"), name=c["name"], args=c.get("args")) for c in calls]
    return NS(data=None, server_content=None, text=None, tool_call=NS(function_calls=fcs))


def test_parse_extracts_tool_calls():
    m = _tool_msg([{"id": "a", "name": "take_screenshot", "args": {"grid": True}}])
    p = lv.parse_message(m)
    assert p["tool_calls"] == [{"id": "a", "name": "take_screenshot", "args": {"grid": True}}]


def test_parse_no_tool_calls_defaults_empty():
    assert lv.parse_message(_msg(user="hi"))["tool_calls"] == []


def test_curate_voice_tools_filters_to_voice_set():
    decls = [{"name": "take_screenshot"}, {"name": "some_obscure_tool"}, {"name": "web_search"}]
    block = lv.curate_voice_tools(decls)
    assert len(block) == 1
    names = [d["name"] for d in block[0]["function_declarations"]]
    assert "take_screenshot" in names and "web_search" in names and "some_obscure_tool" not in names


def test_curate_empty_when_nothing_matches():
    assert lv.curate_voice_tools([{"name": "nope"}]) == []


def test_run_tool_calls_shapes_responses():
    ran = []
    def ex(name, args):
        ran.append((name, args))
        return {"ok": True, "did": name}
    calls = [{"id": "1", "name": "take_screenshot", "args": {"grid": True}},
             {"id": "2", "name": "web_search", "args": {"q": "cats"}}]
    resp = lv.run_tool_calls(calls, ex)
    assert ran == [("take_screenshot", {"grid": True}), ("web_search", {"q": "cats"})]
    assert resp[0] == {"id": "1", "name": "take_screenshot",
                       "response": {"ok": True, "did": "take_screenshot"}}


def test_run_tool_calls_survives_executor_error():
    def ex(name, args):
        raise RuntimeError("boom")
    resp = lv.run_tool_calls([{"id": "1", "name": "x", "args": {}}], ex)
    assert resp[0]["response"]["ok"] is False and "boom" in resp[0]["response"]["error"]


def test_run_tool_calls_wraps_non_dict_result():
    resp = lv.run_tool_calls([{"id": "1", "name": "x", "args": {}}], lambda n, a: "plain")
    assert resp[0]["response"] == {"result": "plain"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} live_voice tests passed")
