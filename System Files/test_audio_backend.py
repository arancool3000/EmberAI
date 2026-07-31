"""Tests for the audio backend selection layer (audio_backend.py).

PyAudio is the app's most common voice failure, so these pin down the behaviours that keep
it from taking the app down with it. No device is opened. Runnable:
    pytest test_audio_backend.py
    python test_audio_backend.py
"""
import audio_backend as ab


# --- preference order ---------------------------------------------------------

def test_sounddevice_is_the_default_first_choice():
    # PyAudio has no macOS/Linux wheel and builds against whatever PortAudio is present.
    # sounddevice ships one. It must get first refusal on the device.
    assert ab.preferred_order(env={})[0] == "sounddevice"


def test_an_explicit_choice_is_honoured():
    assert ab.preferred_order("pyaudio", env={})[0] == "pyaudio"
    assert ab.preferred_order("sounddevice", env={})[0] == "sounddevice"


def test_the_environment_can_force_a_backend():
    assert ab.preferred_order(env={ab.ENV_OVERRIDE: "pyaudio"})[0] == "pyaudio"
    assert ab.preferred_order(env={ab.ENV_OVERRIDE: "PyAudio"})[0] == "pyaudio"


def test_a_forced_choice_still_keeps_the_others_as_fallback():
    # A bad forced choice must degrade to a working microphone, not to no microphone.
    order = ab.preferred_order("pyaudio", env={})
    assert order == ["pyaudio", "sounddevice"]


def test_a_nonsense_choice_is_ignored():
    assert ab.preferred_order("banana", env={}) == list(ab.DEFAULT_ORDER)
    assert ab.preferred_order("", env={}) == list(ab.DEFAULT_ORDER)
    assert ab.preferred_order(None, env={}) == list(ab.DEFAULT_ORDER)


# --- importability ------------------------------------------------------------

def test_importable_catches_more_than_importerror():
    # PyAudio can fail with OSError (missing libportaudio) or a bare C-extension error,
    # so a plain `except ImportError` was never enough.
    def boom_os(name):
        raise OSError("libportaudio.dylib not found")

    def boom_bare(name):
        raise Exception("Bad CPU type in executable")

    assert ab.importable("pyaudio", boom_os) is False
    assert ab.importable("pyaudio", boom_bare) is False
    assert ab.importable("pyaudio", lambda name: object()) is True


def test_available_backends_filters_to_what_imports():
    assert ab.available_backends(lambda name: object()) == list(ab.DEFAULT_ORDER)

    def only_sd(name):
        if name != "sounddevice":
            raise ImportError(name)
        return object()

    assert ab.available_backends(only_sd) == ["sounddevice"]


def test_available_backends_can_be_empty():
    def none(name):
        raise ImportError(name)
    assert ab.available_backends(none) == []


# --- fallback + cleanup -------------------------------------------------------

class _Stream:
    def __init__(self, frame=b"\x00\x00"):
        self.frame = frame
        self.closed = False

    def close(self):
        self.closed = True


def test_first_working_backend_wins():
    good = _Stream()
    got = ab.open_with_fallback([("a", lambda: good)])
    assert got is good


def test_a_broken_backend_is_skipped():
    good = _Stream()
    def broken():
        raise OSError("no device")
    got = ab.open_with_fallback([("a", broken), ("b", lambda: good)])
    assert got is good


def test_a_stream_that_fails_verification_is_closed_not_leaked():
    # The half-opened backend still holds a PortAudio context; dropping the reference does
    # not release it, and repeated retries exhaust the device handles.
    dead, good = _Stream(), _Stream()

    def verify(s):
        if s is dead:
            raise RuntimeError("opened but produced no audio")

    got = ab.open_with_fallback([("dead", lambda: dead), ("good", lambda: good)],
                                verify=verify)
    assert got is good
    assert dead.closed is True


def test_a_close_that_itself_fails_does_not_break_the_fallback():
    class _Nasty(_Stream):
        def close(self):
            raise RuntimeError("close blew up")

    good = _Stream()

    def verify(s):
        if isinstance(s, _Nasty):
            raise RuntimeError("no audio")

    got = ab.open_with_fallback([("bad", lambda: _Nasty()), ("good", lambda: good)],
                                verify=verify)
    assert got is good


def test_the_combined_failure_names_every_backend():
    def a():
        raise OSError("portaudio missing")

    def b():
        raise RuntimeError("no input device")

    try:
        ab.open_with_fallback([("PyAudio", a), ("sounddevice", b)])
    except RuntimeError as e:
        assert "PyAudio" in str(e) and "sounddevice" in str(e)
        assert "portaudio missing" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


def test_no_backends_at_all_still_raises_cleanly():
    try:
        ab.open_with_fallback([])
    except RuntimeError as e:
        assert "no audio backend" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


# --- stderr silencing ---------------------------------------------------------

def test_quiet_stderr_restores_the_descriptor():
    import os
    before = os.dup(2)
    try:
        with ab.quiet_stderr():
            pass
        after = os.dup(2)
        os.close(after)
    finally:
        os.close(before)
    # Nothing to assert beyond "we got here and fd 2 still works".
    import sys
    print("", file=sys.stderr, end="")


def test_quiet_stderr_propagates_the_bodys_exception():
    """Regression: the body's exception was caught by the setup handler, which then yielded
    a second time — "generator didn't stop after throw()" — turning any audio error into a
    confusing contextmanager error."""
    try:
        with ab.quiet_stderr():
            raise ValueError("real error")
    except ValueError as e:
        assert str(e) == "real error"
    except RuntimeError as e:
        raise AssertionError(f"masked the real error: {e}")
    else:
        raise AssertionError("exception was swallowed")


def test_quiet_stderr_can_be_disabled():
    with ab.quiet_stderr(enabled=False):
        pass


def test_quiet_stderr_is_reentrant():
    with ab.quiet_stderr():
        with ab.quiet_stderr():
            pass


# --- guidance -----------------------------------------------------------------

def test_install_hint_recommends_sounddevice_first():
    hint = ab.install_hint()
    assert "sounddevice" in hint
    assert hint.index("sounddevice") < (hint.index("pyaudio") if "pyaudio" in hint else 10 ** 6)


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
