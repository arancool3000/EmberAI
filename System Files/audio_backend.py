"""Microphone and speaker backends, with PyAudio kept firmly in its place.

PyAudio is the single most reliable source of voice failures in this app, for reasons that
have nothing to do with Ember:

* it has no macOS/Linux wheel, so it compiles against whatever PortAudio is on the box —
  or against one that has since been upgraded out from under it;
* a downloaded build can carry the wrong CPU architecture, which surfaces as a bizarre
  "Bad CPU type" at import;
* on Linux, constructing it dumps a wall of ALSA/JACK probe errors to stderr even when it
  works perfectly;
* and worst of all, it frequently **opens successfully and then delivers nothing** — a
  stream that never yields a frame is far harder to diagnose than one that fails outright.

``sounddevice`` ships prebuilt wheels that bundle PortAudio, so it works out of the box.
This module therefore makes sounddevice the real default (the code used to try PyAudio
first while its own comment claimed otherwise), verifies that a backend actually produces
audio before handing it over, and never leaks a PortAudio context when an open fails
half-way.

Selection and ordering are pure functions, tested headlessly; only the ``open_*`` helpers
touch a device.
"""
from __future__ import annotations

import contextlib
import os
import sys
from typing import Callable, List, Optional, Sequence

#: Backends in the order they should normally be tried. sounddevice first — it is the one
#: that ships a working PortAudio.
DEFAULT_ORDER = ("sounddevice", "pyaudio")

#: Set EMBER_AUDIO_BACKEND=pyaudio|sounddevice to force one (support escape hatch).
ENV_OVERRIDE = "EMBER_AUDIO_BACKEND"


def preferred_order(setting: Optional[str] = None,
                    env: Optional[dict] = None) -> List[str]:
    """Which backends to try, best first.

    An explicit choice — from Settings or the environment — is honoured by moving that
    backend to the front rather than by dropping the others, so a bad forced choice
    degrades to a working microphone instead of to no microphone at all.
    """
    env = os.environ if env is None else env
    choice = (setting or env.get(ENV_OVERRIDE) or "").strip().lower()
    order = list(DEFAULT_ORDER)
    if choice in order:
        order.remove(choice)
        order.insert(0, choice)
    return order


def importable(name: str, importer: Optional[Callable[[str], object]] = None) -> bool:
    """Whether a backend module can actually be imported.

    PyAudio can fail here with ImportError, OSError (missing libportaudio) *or* a bare
    Exception from its C extension, so this catches broadly on purpose.
    """
    importer = importer or __import__
    try:
        importer(name)
        return True
    except Exception:
        return False


def available_backends(importer: Optional[Callable[[str], object]] = None,
                       setting: Optional[str] = None) -> List[str]:
    return [b for b in preferred_order(setting) if importable(b, importer)]


@contextlib.contextmanager
def quiet_stderr(enabled: bool = True):
    """Swallow the ALSA/JACK probe spam PortAudio writes straight to fd 2 on Linux.

    The noise is emitted by the C library, not by Python, so redirecting ``sys.stderr`` is
    not enough — the file descriptor itself has to be pointed elsewhere.
    """
    if not enabled or not sys.platform.startswith("linux"):
        yield
        return
    saved = None
    devnull = None
    try:
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
    except Exception:
        # Couldn't redirect (no fd 2, sandbox). Run the body anyway, just noisily — and
        # make sure nothing is restored below that was never swapped.
        for fd in (saved, devnull):
            if fd is not None:
                with contextlib.suppress(Exception):
                    os.close(fd)
        saved = devnull = None
    # The yield must be in its own try/finally. Wrapping it in the same `except Exception`
    # as the setup meant an exception raised by the BODY was caught here and the generator
    # yielded a second time — "generator didn't stop after throw()" — which turned any
    # audio error into a confusing contextmanager error instead.
    try:
        yield
    finally:
        try:
            if saved is not None:
                os.dup2(saved, 2)
                os.close(saved)
            if devnull is not None:
                os.close(devnull)
        except Exception:
            pass


def open_with_fallback(factories: Sequence, *, verify: Optional[Callable] = None) -> object:
    """Try each ``(name, factory)`` in order; return the first that opens *and* verifies.

    ``verify(stream)`` is what catches PyAudio's worst failure mode — an open that
    succeeds and then never delivers a frame. A backend that fails verification is closed
    and the next one is tried, so a silently-dead device can't shadow a working one.
    """
    errors = []
    for name, factory in factories:
        stream = None
        try:
            stream = factory()
            if verify is not None:
                verify(stream)
            return stream
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            # A half-opened backend still holds a PortAudio context; dropping the
            # reference does NOT release it, and repeated retries exhaust device handles.
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
    raise RuntimeError("; ".join(errors) or "no audio backend is installed")


def install_hint() -> str:
    """What to tell the user when no backend works.

    Inside the packaged app there is nothing the user can pip install — ``sys.executable`` is
    the Ember binary, not a Python they can add packages to. Telling them to run
    ``"/Applications/Ember.app/Contents/MacOS/Ember" -m pip install sounddevice`` is advice
    that cannot possibly work, so the frozen build says what is actually true instead.
    """
    if getattr(sys, "frozen", False):
        return ("No microphone backend is available inside this build of Ember. That is a "
                "packaging fault, not something you can install — please report it. Running "
                "Ember from source (System Files/Ember.command) works in the meantime.")
    py = sys.executable or "python3"
    base = (f'No working microphone backend. Install the recommended one:\n'
            f'  "{py}" -m pip install sounddevice')
    if sys.platform == "darwin":
        return base + ('\n\nPyAudio is an alternative but has no macOS wheel and must be '
                       'built against Homebrew PortAudio:\n'
                       '  brew install portaudio && "%s" -m pip install pyaudio' % py)
    if sys.platform.startswith("linux"):
        return base + ('\n\nPyAudio is an alternative but needs PortAudio headers:\n'
                       '  sudo apt-get install -y portaudio19-dev && "%s" -m pip install pyaudio'
                       % py)
    return base


# ---------------------------------------------------------------------------
# Recording helper shared by the non-streaming callers
# ---------------------------------------------------------------------------

def record_wav(seconds: int, path: str, *, rate: int = 44100, channels: int = 1,
               chunk: int = 1024, setting: Optional[str] = None) -> str:
    """Record ``seconds`` of microphone audio to a WAV file.

    Tries sounddevice, then PyAudio. Both paths release their device even when the
    recording raises part-way through.
    """
    import wave

    for backend in preferred_order(setting):
        try:
            if backend == "sounddevice":
                frames = _record_sounddevice(seconds, rate, channels)
                width = 2
            else:
                frames, width = _record_pyaudio(seconds, rate, channels, chunk)
        except Exception:
            continue
        with wave.open(path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(width)
            wf.setframerate(rate)
            wf.writeframes(frames)
        return path
    raise RuntimeError(install_hint())


def _record_sounddevice(seconds: int, rate: int, channels: int) -> bytes:
    """Record via sounddevice WITHOUT NumPy.

    ``sd.rec()`` is the obvious call, and it returns a NumPy array — so it needs NumPy. The
    packaged Ember.app deliberately excludes NumPy (see Ember.spec), which meant recording
    failed inside the bundle while the microphone itself opened perfectly well through
    ``audio_level``'s RawInputStream. The user got "no working microphone backend" for a mic
    that plainly worked, and was told to pip install into an .app bundle.

    ``RawInputStream`` yields raw frames and has no NumPy dependency, so it works in the
    bundle and outside it alike.
    """
    frames = bytearray()
    want = int(seconds * rate) * channels * 2      # int16 => 2 bytes per sample
    with quiet_stderr():
        import sounddevice as sd
        stream = sd.RawInputStream(samplerate=rate, channels=channels, dtype="int16",
                                   blocksize=1024)
        stream.start()
        try:
            while len(frames) < want:
                data, _overflowed = stream.read(1024)
                if not data:
                    break
                frames.extend(bytes(data))
        finally:
            try:
                stream.stop()
            finally:
                stream.close()
    return bytes(frames[:want])


def _record_pyaudio(seconds: int, rate: int, channels: int, chunk: int):
    with quiet_stderr():
        import pyaudio
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=rate,
                             input=True, frames_per_buffer=chunk)
            frames = [stream.read(chunk, exception_on_overflow=False)
                      for _ in range(max(1, int(rate / chunk * seconds)))]
            return b"".join(frames), pa.get_sample_size(pyaudio.paInt16)
        finally:
            # terminate() must run even if open() itself raised, or every failed attempt
            # leaks a PortAudio context.
            try:
                if stream is not None:
                    stream.stop_stream()
                    stream.close()
            finally:
                with contextlib.suppress(Exception):
                    pa.terminate()
