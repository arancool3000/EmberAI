"""Tests for the sound_level (mic loudness) tool. No real microphone needed — we
inject a scripted stream via audio_level._STREAM_FACTORY."""
import struct

import audio_level
import sound_level


class _FakeStream:
    """Returns constant-amplitude 16-bit mono frames, so RMS ~= |amplitude|."""
    def __init__(self, amplitude):
        self.amp = int(amplitude)
    def read(self, n):
        return struct.pack("<%dh" % n, *([self.amp] * n))
    def close(self):
        pass


def _with_factory(amplitude):
    audio_level._STREAM_FACTORY = lambda: _FakeStream(amplitude)


def teardown_function(_):
    audio_level._STREAM_FACTORY = None


def test_dbfs_reference_full_scale():
    assert sound_level._dbfs(32768) == 0.0
    assert sound_level._dbfs(0) == -120.0
    # Half amplitude ~ -6 dBFS
    assert -6.5 < sound_level._dbfs(16384) < -5.5


def test_descriptor_bands():
    assert sound_level._descriptor(-60) == "near silence"
    assert sound_level._descriptor(-30) == "quiet"
    assert sound_level._descriptor(-22) == "moderate"
    assert sound_level._descriptor(-15) == "loud"
    assert sound_level._descriptor(-5) == "very loud"


def test_measures_a_loud_signal():
    _with_factory(8000)                     # ~ -12 dBFS
    r = sound_level.measure_sound_level(seconds=0.3)
    assert r["ok"] is True
    assert r["samples"] >= 1
    assert -14 < r["dbfs_peak"] < -10
    assert r["descriptor"] in ("moderate", "loud")
    assert 0.0 < r["level"] <= 1.0


def test_silence_is_flagged():
    _with_factory(0)
    r = sound_level.measure_sound_level(seconds=0.3)
    assert r["ok"] is True
    assert r["descriptor"] == "silent"
    assert r["level"] == 0.0


def test_spl_offset_estimates_spl():
    _with_factory(8000)
    r = sound_level.measure_sound_level(seconds=0.3, spl_offset_db=94.0)
    assert "est_db_spl_peak" in r
    assert r["est_db_spl_peak"] == round(r["dbfs_peak"] + 94.0, 1)


def test_seconds_is_clamped():
    _with_factory(4000)
    r = sound_level.measure_sound_level(seconds=999)
    assert r["seconds"] == 10.0            # clamped to max


def test_missing_pyaudio_is_graceful():
    def _boom():
        raise RuntimeError("No module named 'pyaudio'")
    audio_level._STREAM_FACTORY = _boom
    r = sound_level.measure_sound_level(seconds=0.3)
    assert r["ok"] is False
    assert "pyaudio" in r["error"].lower()


if __name__ == "__main__":  # allow running without pytest
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); teardown_function(None); print("PASS", fn.__name__)
        except Exception:
            failed += 1; print("FAIL", fn.__name__); traceback.print_exc()
    raise SystemExit(1 if failed else 0)
