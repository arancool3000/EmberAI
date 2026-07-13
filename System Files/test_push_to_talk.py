"""Hermetic tests for push-to-talk: the PushToTalk state machine (push_to_talk.py) and the STT
engine-selection logic (stt.py). No mic, no model, no network — every side effect is injected.
Run: python test_push_to_talk.py"""
import push_to_talk as ptt
import stt


# --- PushToTalk state machine (run synchronously for deterministic assertions) ----------
def _make(**over):
    calls = {"start": 0, "stop": 0, "text": [], "states": [], "errors": []}
    audio = over.pop("audio", "clip.wav")
    transcript = over.pop("transcript", "hello ember")

    def stop_record():
        calls["stop"] += 1
        return audio

    min_hold_ms = over.pop("min_hold_ms", 0)
    clock = over.pop("clock", lambda: 0.0)
    p = ptt.PushToTalk(
        start_record=lambda: calls.__setitem__("start", calls["start"] + 1),
        stop_record=stop_record,
        transcribe=lambda a: transcript,
        on_text=lambda t: calls["text"].append(t),
        on_state=lambda s: calls["states"].append(s),
        on_error=lambda e: calls["errors"].append(e),
        run_async=False, min_hold_ms=min_hold_ms, clock=clock,
        **over)
    return p, calls


def test_full_cycle_press_release_transcribe_submit():
    p, c = _make()
    p.press()
    assert p.state == ptt.RECORDING and c["start"] == 1
    p.release()
    assert p.state == ptt.IDLE
    assert c["stop"] == 1
    assert c["text"] == ["hello ember"]
    assert c["states"] == [ptt.RECORDING, ptt.TRANSCRIBING, ptt.IDLE]


def test_release_without_press_is_ignored():
    p, c = _make()
    p.release()
    assert p.state == ptt.IDLE and c["stop"] == 0 and c["text"] == []


def test_double_press_does_not_restart():
    p, c = _make()
    p.press()
    p.press()
    assert c["start"] == 1


def test_blank_transcript_is_not_submitted():
    p, c = _make(transcript="   ")
    p.press(); p.release()
    assert c["text"] == [] and p.state == ptt.IDLE


def test_too_short_hold_is_discarded():
    # clock advances 0 -> 50ms; min_hold_ms 120 -> treated as an accidental tap
    ticks = iter([0.0, 50.0])
    p, c = _make(clock=lambda: next(ticks), min_hold_ms=120)
    p.press(); p.release()
    assert c["text"] == []           # nothing submitted
    assert c["stop"] == 1            # but the mic was still released


def test_transcribe_exception_reports_error_and_resets():
    p, c = _make()
    p._transcribe = lambda a: (_ for _ in ()).throw(RuntimeError("boom"))
    p.press(); p.release()
    assert p.state == ptt.IDLE
    assert c["text"] == [] and any("boom" in e for e in c["errors"])


def test_start_record_failure_resets_to_idle():
    p, c = _make()
    p._start_record = lambda: (_ for _ in ()).throw(OSError("no mic"))
    p.press()
    assert p.state == ptt.IDLE
    assert any("no mic" in e for e in c["errors"])
    assert c["states"][-1] == ptt.IDLE


def test_cancel_aborts_without_transcribing():
    p, c = _make()
    p.press()
    p.cancel()
    assert p.state == ptt.IDLE
    assert c["stop"] == 1 and c["text"] == []
    # cancel must not have run transcription (no text emitted)


def test_strips_whitespace_on_submit():
    p, c = _make(transcript="  do the thing  ")
    p.press(); p.release()
    assert c["text"] == ["do the thing"]


# --- STT engine selection (pure) --------------------------------------------------------
def test_auto_prefers_local_whisper():
    eng = stt.pick_stt_engine("auto", whisper_ok=True, has_gemini_key=True,
                              offline=False, sr_ok=True)
    assert eng[0] == "whisper"
    assert eng == ["whisper", "gemini", "google"]


def test_auto_without_whisper_falls_back_to_cloud():
    eng = stt.pick_stt_engine("auto", whisper_ok=False, has_gemini_key=True,
                              offline=False, sr_ok=True)
    assert eng == ["gemini", "google"]


def test_offline_drops_networked_engines():
    eng = stt.pick_stt_engine("auto", whisper_ok=True, has_gemini_key=True,
                              offline=True, sr_ok=True)
    assert "gemini" not in eng and "google" not in eng
    assert eng == ["whisper", "sphinx"]


def test_explicit_preference_goes_first_if_available():
    eng = stt.pick_stt_engine("google", whisper_ok=True, has_gemini_key=True,
                              offline=False, sr_ok=True)
    assert eng[0] == "google"


def test_explicit_preference_skipped_when_unavailable():
    # prefer whisper but it's not installed -> it must not appear
    eng = stt.pick_stt_engine("whisper", whisper_ok=False, has_gemini_key=True,
                              offline=False, sr_ok=True)
    assert "whisper" not in eng and eng[0] == "gemini"


def test_no_engine_available_returns_empty():
    eng = stt.pick_stt_engine("auto", whisper_ok=False, has_gemini_key=False,
                              offline=False, sr_ok=False)
    assert eng == []


def test_transcribe_audio_missing_file_is_safe():
    r = stt.transcribe_audio("/no/such/file.wav")
    assert r["ok"] is False and r["text"] == ""


def test_pynput_and_mac_key_helpers():
    # mac_keycode is a pure lookup (no deps)
    assert ptt.mac_keycode("f9") == 101
    assert ptt.mac_keycode("space") == 49
    assert ptt.mac_keycode("not_a_key") is None
    # pynput may be absent; the helper must degrade to None, never raise
    try:
        import pynput  # noqa: F401
    except Exception:
        assert ptt.pynput_key("f9") is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} push-to-talk tests passed")
