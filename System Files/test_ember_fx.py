"""Tests for the visual effects engine (ember_fx.py) and the sound engine (ember_sound.py).

Pure maths — no display, no audio device. Runnable:
    pytest test_ember_fx.py
    python test_ember_fx.py
"""
import math

import ember_fx as fx
import ember_sound as snd


# --- colour ramps -------------------------------------------------------------

def test_hex_parsing_handles_short_and_long_form():
    assert fx.hex_to_rgb("#ff0080") == (255, 0, 128)
    assert fx.hex_to_rgb("f08") == (255, 0, 136)
    assert fx.hex_to_rgb("00ff00") == (0, 255, 0)


def test_ramp_wraps_seamlessly():
    # A ramp sampled at 0 and 1 must give the same colour, or an animated gradient shows
    # a hard seam every time the phase wraps.
    assert fx.ramp_at(fx.IRIDESCENT, 0.0) == fx.ramp_at(fx.IRIDESCENT, 1.0)
    assert fx.ramp_at(fx.IRIDESCENT, 0.25) == fx.ramp_at(fx.IRIDESCENT, 1.25)


def test_ramp_interpolates_between_stops():
    pal = ("#000000", "#ffffff")
    mid = fx.ramp_at(pal, 0.25)          # halfway from black to white
    assert all(100 < c < 160 for c in mid), mid


def test_flowing_stops_are_ordered_and_bounded():
    stops = fx.flowing_stops(fx.IRIDESCENT, 0.3, stops=10)
    assert len(stops) == 10
    positions = [p for p, _ in stops]
    assert positions == sorted(positions)
    assert positions[0] == 0.0 and positions[-1] == 1.0
    for _, rgb in stops:
        assert all(0 <= c <= 255 for c in rgb)


def test_flowing_stops_actually_move_with_phase():
    a = fx.flowing_stops(fx.IRIDESCENT, 0.0, stops=8)
    b = fx.flowing_stops(fx.IRIDESCENT, 0.5, stops=8)
    assert [c for _, c in a] != [c for _, c in b]


def test_smoothstep_is_clamped_and_eased():
    assert fx.smoothstep(-5) == 0.0 and fx.smoothstep(5) == 1.0
    assert fx.smoothstep(0.5) == 0.5
    assert fx.smoothstep(0.25) < 0.25      # eased in, not linear


# --- flow field ---------------------------------------------------------------

def test_flow_field_is_bounded():
    f = fx.FlowField(size=8, seed=3)
    for t in (0.0, 1.7, 9.3):
        for x in range(0, 20):
            v = f.at(x * 0.7, x * 0.3, t)
            assert 0.0 <= v <= 1.0, v


def test_flow_field_is_continuous():
    # Neighbouring samples must not jump, or the pointer's gradient flickers.
    f = fx.FlowField(size=8, seed=3)
    prev = f.at(0.0, 0.0, 0.0)
    for i in range(1, 60):
        cur = f.at(i * 0.02, 0.0, 0.0)
        assert abs(cur - prev) < 0.25, (i, prev, cur)
        prev = cur


def test_flow_field_evolves_over_time():
    f = fx.FlowField(size=8, seed=3)
    assert f.at(1.0, 1.0, 0.0) != f.at(1.0, 1.0, 2.5)


def test_flow_field_is_deterministic_for_a_seed():
    a, b = fx.FlowField(size=8, seed=5), fx.FlowField(size=8, seed=5)
    assert a.at(1.3, 2.1, 0.5) == b.at(1.3, 2.1, 0.5)


def test_flow_angle_is_a_full_turn():
    f = fx.FlowField(size=8, seed=3)
    angles = [f.angle(i * 0.9, i * 0.4, 0.0) for i in range(40)]
    assert all(0.0 <= a <= math.tau for a in angles)


# --- fire ---------------------------------------------------------------------

def test_fire_source_row_is_hot():
    f = fx.FireBuffer(32, 16)
    assert all(f.heat_at(x, 15) == fx.HEAT_MAX for x in range(32))


def test_fire_rises_and_cools():
    f = fx.FireBuffer(32, 24, seed=4)
    for _ in range(60):
        f.step()
    bottom = sum(f.heat_at(x, 22) for x in range(32))
    top = sum(f.heat_at(x, 2) for x in range(32))
    assert bottom > top > 0, (bottom, top)      # heat reaches upward but weakens


def test_fire_stays_in_the_palette_range():
    f = fx.FireBuffer(24, 16, seed=6)
    for _ in range(40):
        f.step()
    for y in range(16):
        for x in range(24):
            assert 0 <= f.heat_at(x, y) <= fx.HEAT_MAX


def test_fire_never_leaves_the_frame():
    # Drift must reflect at the edges; wrapping would tunnel flames across the image.
    f = fx.FireBuffer(16, 12, seed=9)
    for _ in range(50):
        f.step()
    assert len(f.cells) == 16 * 12


def test_dousing_lets_the_fire_die_out():
    f = fx.FireBuffer(24, 16, seed=2)
    for _ in range(30):
        f.step()
    f.douse()
    for _ in range(120):
        f.step()
    assert sum(f.cells) == 0


def test_intensity_changes_how_far_flames_reach():
    def reach(intensity):
        f = fx.FireBuffer(40, 40, seed=13)
        f.set_intensity(intensity)
        for _ in range(120):
            f.step()
        return sum(1 for y in range(40) for x in range(40) if f.heat_at(x, y) > 0)
    assert reach(2.0) > reach(0.3)


def test_intensity_is_clamped():
    f = fx.FireBuffer(8, 8)
    f.set_intensity(-99)
    assert f.intensity > 0
    f.set_intensity(999)
    assert f.intensity <= 3.0


def test_heat_outside_the_grid_is_zero():
    f = fx.FireBuffer(8, 8)
    assert f.heat_at(-1, 0) == 0 and f.heat_at(99, 99) == 0


# --- streaming text -----------------------------------------------------------

def test_text_is_never_lost_regardless_of_fade():
    s = fx.StreamingText(fade=0.3, now=0.0)
    s.append("Hello ", now=0.0)
    s.append("world", now=0.1)
    assert s.text == "Hello world"


def test_new_text_starts_transparent_and_reaches_full():
    s = fx.StreamingText(fade=0.2, now=0.0)
    s.append("abc", now=0.0)
    assert s.opacities(now=0.0)[0][1] == 0.0
    assert s.opacities(now=0.1)[0][1] > 0.0
    s.compact(now=0.5)
    assert s.opacities(now=0.5) == []          # settled, no longer a span


def test_older_chunks_are_brighter_than_newer_ones():
    # This is the whole effect: the tail is dimmer than the text before it.
    s = fx.StreamingText(fade=0.4, now=0.0)
    s.append("first", now=0.0)
    s.append("second", now=0.2)
    ops = dict((c, o) for c, o in s.opacities(now=0.25))
    assert ops["first"] > ops["second"]


def test_active_goes_false_once_everything_has_faded_in():
    s = fx.StreamingText(fade=0.2, now=0.0)
    s.append("x", now=0.0)
    assert s.active(now=0.05) is True
    assert s.active(now=1.0) is False


def test_compaction_keeps_the_span_count_bounded():
    # Mirrors the real render loop, which compacts on every repaint: only the chunks
    # inside the fade window stay as spans, so a 500-chunk reply never becomes 500 spans.
    s = fx.StreamingText(fade=0.15, now=0.0)
    worst = 0
    for i in range(500):
        now = i * 0.01
        s.append("tok", now=now)
        s.compact(now=now)
        worst = max(worst, len(s.opacities(now=now)))
    assert worst <= 20, worst
    s.compact(now=10.0)
    assert s.opacities(now=10.0) == []
    assert s.text == "tok" * 500


def test_finish_settles_everything_immediately():
    s = fx.StreamingText(fade=10.0, now=0.0)
    s.append("pending", now=0.0)
    s.finish()
    assert s.active(now=0.0) is False
    assert s.text == "pending"


def test_reset_clears_everything():
    s = fx.StreamingText(fade=0.2, now=0.0)
    s.append("gone", now=0.0)
    s.reset()
    assert s.text == "" and s.active(now=0.0) is False


def test_empty_chunks_are_ignored():
    s = fx.StreamingText(now=0.0)
    s.append("", now=0.0)
    s.append(None, now=0.0)
    assert s.text == "" and s.opacities(now=0.0) == []


def test_render_html_dims_only_the_tail():
    s = fx.StreamingText(fade=0.3, now=0.0)
    s.append("settled", now=0.0)
    s.append("fresh", now=1.0)
    html = s.render_html(now=1.0)
    assert html.startswith("settled")          # settled text is plain, not a span
    assert "rgba(" in html and "fresh" in html


def test_render_html_escapes_when_asked():
    import html as h
    s = fx.StreamingText(fade=0.3, now=0.0)
    s.append("<b>", now=0.0)
    out = s.render_html(now=0.0, escape=h.escape)
    assert "&lt;b&gt;" in out


# --- sound: envelopes ---------------------------------------------------------

def test_envelope_starts_and_ends_at_silence():
    e = snd.ADSR(attack=0.01, decay=0.05, sustain=0.5, release=0.1)
    assert e.at(0.0, 1.0) == 0.0
    assert e.at(1.0, 1.0) == 0.0          # a non-zero end is exactly what clicks


def test_envelope_peaks_after_the_attack():
    e = snd.ADSR(attack=0.05, decay=0.1, sustain=0.4, release=0.2)
    assert abs(e.at(0.05, 1.0) - 1.0) < 1e-6
    assert e.at(0.025, 1.0) < e.at(0.05, 1.0)


def test_envelope_sustains_then_releases():
    e = snd.ADSR(attack=0.01, decay=0.05, sustain=0.5, release=0.2)
    assert abs(e.at(0.4, 1.0) - 0.5) < 1e-6         # sustain plateau
    assert e.at(0.9, 1.0) < e.at(0.85, 1.0)         # falling through the release


def test_envelope_is_bounded_and_safe_outside_the_note():
    e = snd.ADSR()
    for t in (-1.0, 0.0, 0.5, 1.0, 2.0):
        assert 0.0 <= e.at(t, 1.0) <= 1.0


def test_envelope_survives_a_release_longer_than_the_note():
    e = snd.ADSR(attack=0.01, decay=0.05, sustain=0.5, release=10.0)
    for t in (0.0, 0.05, 0.1):
        assert 0.0 <= e.at(t, 0.1) <= 1.0


# --- sound: synthesis ---------------------------------------------------------

def test_notes_are_equal_tempered():
    assert abs(snd.note(0) - 440.0) < 1e-9
    assert abs(snd.note(12) - 880.0) < 1e-9
    assert abs(snd.note(-12) - 220.0) < 1e-9


def test_voice_renders_the_right_length():
    v = snd.Voice(440.0, 0.1)
    assert abs(len(v.render()) - int(0.1 * snd.SAMPLE_RATE)) <= 1


def test_mix_respects_start_offsets():
    a = snd.Voice(440.0, 0.05, start=0.0)
    b = snd.Voice(660.0, 0.05, start=0.10)
    buf = snd.mix([a, b])
    assert len(buf) >= int(0.15 * snd.SAMPLE_RATE)
    # The gap between the two notes is silent.
    gap = buf[int(0.07 * snd.SAMPLE_RATE)]
    assert abs(gap) < 1e-6


def test_mix_of_nothing_is_empty():
    assert snd.mix([]) == []
    assert snd.limit([]) == []


def test_limiter_bounds_the_signal_and_lands_on_silence():
    buf = snd.limit([5.0] * 2000)          # deliberately way over full scale
    assert all(abs(s) <= 1.0 for s in buf)
    assert buf[-1] == 0.0                   # never click on the final sample


def test_limiter_fades_the_tail():
    buf = snd.limit([1.0] * 5000)
    tail = buf[-50:]
    assert tail[0] > tail[-1]               # monotone-ish ramp down to zero


def test_every_cue_renders_audible_bounded_audio():
    for name in snd.CUES:
        buf = snd.cue(name)
        assert buf, name
        assert max(abs(s) for s in buf) > 0.05, name      # not silence
        assert all(abs(s) <= 1.0 for s in buf), name      # not clipping
        assert buf[-1] == 0.0, name                       # not clicking


def test_cues_are_distinct_from_one_another():
    # The on/off pair especially must not be the same sound played twice.
    on, off = snd.cue("voice_on"), snd.cue("voice_off")
    assert on[:2000] != off[:2000]


def test_cues_have_a_real_attack_rather_than_a_gate():
    # A gated beep is full-amplitude at sample 0; a shaped cue starts near silence.
    for name in ("voice_on", "voice_off", "done"):
        buf = snd.cue(name)
        peak = max(abs(s) for s in buf)
        assert abs(buf[0]) < peak * 0.1, name


def test_unknown_cue_falls_back_instead_of_raising():
    assert snd.cue("no-such-cue")


def test_wav_container_is_well_formed():
    import io
    import wave
    data = snd.to_wav_bytes(snd.cue("tick"))
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == snd.SAMPLE_RATE


def test_sound_can_be_switched_off():
    snd.set_enabled(False)
    try:
        assert snd.play("tick") is False
    finally:
        snd.set_enabled(True)
    assert snd.is_enabled() is True


def _run():
    failures = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in sorted(names):
        try:
            globals()[name]()
            print(f"PASS  {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(names) - failures}/{len(names)} passed")
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
