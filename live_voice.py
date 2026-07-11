"""ChatGPT-style natural voice via the Gemini Live API (native-audio).

Unlike the old listen → transcribe → think → TTS pipeline, this opens ONE
bidirectional streaming session: your microphone audio streams up continuously and
Ember's spoken reply streams back as audio — so it hears *how* you speak (accent,
tone, pace), replies in a natural neural voice, and supports server-side barge-in
(start talking and it stops to listen). The native-audio Live models also lift the
per-minute request cap that made the old per-message pipeline hit 429s.

Why this design is testable despite needing a live socket:
  * the network/audio bits (genai client, pyaudio) are imported lazily and live ONLY
    in the real mic/player/connection wrappers;
  * the session STATE MACHINE — what to do with each server message, how the sender
    and receiver loops cooperate, stop/interrupt handling — is pure async logic that
    runs against injected fakes. parse_message() is a pure function.
The live websocket itself is verified on-device; everything around it is unit-tested.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

# Live API audio formats: input 16-bit PCM mono @16k, output PCM @24k.
AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 24000
AUDIO_IN_MIME = f"audio/pcm;rate={AUDIO_IN_RATE}"
CHUNK = 1024

# A current native-audio dialog model (overridable from settings). Google renames/retires these
# dated preview IDs every few months (a stale one fails the WebSocket handshake with "received
# 1008 (policy violation) ... not found ... or is not supported for bidiGenerateContent" - every
# retry then fails identically since retrying doesn't change the model), so on that specific
# failure _main() below advances through FALLBACK_MODELS instead of just retrying the same dead
# model up to max_failures times.
DEFAULT_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
FALLBACK_MODELS = ["gemini-live-2.5-flash-native-audio", "gemini-2.0-flash-live-001"]
DEFAULT_VOICE = "Zephyr"
DEFAULT_API_VERSION = "v1beta"


def _looks_like_bad_model(e) -> bool:
    """True if an exception looks like 'this model doesn't exist / isn't Live-capable' rather
    than a transient network hiccup - the two need different responses (switch model vs. just
    retry)."""
    s = str(e).lower()
    return ("1008" in s or "policy violation" in s or "not found" in s
            or "not supported for bidigeneratecontent" in s)


def _noop(*_a, **_k):
    return None


# ---------------------------------------------------------------------------
# Pure message parsing (the most-tested piece)
# ---------------------------------------------------------------------------

def parse_message(msg) -> dict:
    """Extract the bits we care about from one Live API server message, defensively
    (the SDK's shape varies by version). Returns a dict with audio/user_text/
    ember_text/interrupted/turn_complete."""
    out = {"audio": None, "user_text": None, "ember_text": None,
           "interrupted": False, "turn_complete": False, "tool_calls": []}
    if msg is None:
        return out
    # Tool calls: the native-audio model can DRIVE Ember's tools mid-conversation (open apps,
    # read the screen, manage files…) — the thing a talk-only assistant like GPT Live can't do.
    tc = getattr(msg, "tool_call", None)
    if tc is not None:
        for fc in (getattr(tc, "function_calls", None) or []):
            args = getattr(fc, "args", None)
            try:
                args = dict(args) if args else {}
            except Exception:
                args = {}
            out["tool_calls"].append({"id": getattr(fc, "id", None),
                                      "name": getattr(fc, "name", None), "args": args})
    data = getattr(msg, "data", None)
    if isinstance(data, (bytes, bytearray)) and len(data) > 0:
        out["audio"] = bytes(data)
    sc = getattr(msg, "server_content", None)
    if sc is not None:
        it = getattr(sc, "input_transcription", None)
        if it is not None:
            t = getattr(it, "text", None)
            if t:
                out["user_text"] = t
        ot = getattr(sc, "output_transcription", None)
        if ot is not None:
            t = getattr(ot, "text", None)
            if t:
                out["ember_text"] = t
        if getattr(sc, "interrupted", False):
            out["interrupted"] = True
        if getattr(sc, "turn_complete", False):
            out["turn_complete"] = True
    # Some builds expose the model's text directly on the message.
    if out["ember_text"] is None:
        t = getattr(msg, "text", None)
        if isinstance(t, str) and t:
            out["ember_text"] = t
    return out


# Tools worth giving the live-voice model — the ones that make sense to drive by speaking. A
# curated set (not all ~290) keeps the session lean and the model's tool-choice sharp. The UI
# filters Ember's full declarations to these before starting the session.
VOICE_TOOL_NAMES = {
    # see + control the screen
    "take_screenshot", "read_screen_text", "smart_click", "type_text", "press_key",
    "list_windows", "focus_window", "open_app", "open_url", "open_path",
    # web / browser
    "web_search", "browser_open", "browser_navigate", "browser_get_page", "browser_click_text",
    "wikipedia_summary", "weather_lookup", "translate_text", "define_word",
    # files
    "read_file", "write_file", "list_directory", "search_files", "find_large_files",
    "organize_folder", "get_folder_size",
    # system + media
    "get_system_info", "get_performance", "system_health", "set_volume", "toggle_mute",
    "media_keys", "get_battery", "power_action", "show_notification",
    # productivity
    "set_timer", "list_timers", "cancel_timer", "remember", "recall", "what_you_know",
    "send_email", "gmail_search", "gmail_read", "now",
    # security (read-mostly)
    "security_status", "scan_file", "run_shell",
}


def curate_voice_tools(declarations: list) -> list:
    """Return the [{function_declarations:[...]}] tools block for the Live API, filtered to the
    voice-appropriate set. Pure so it's unit-tested without the genai SDK."""
    decls = [d for d in (declarations or []) if d.get("name") in VOICE_TOOL_NAMES]
    return [{"function_declarations": decls}] if decls else []


def run_tool_calls(calls: list, executor) -> list:
    """Execute each live tool call via `executor(name, args) -> dict` and shape the results as Live
    API function responses. Pure control-flow (executor is injected), so it's fully unit-tested."""
    responses = []
    for c in (calls or []):
        name = c.get("name")
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        try:
            result = executor(name, args)
        except Exception as e:  # an executor bug must not kill the voice session
            result = {"ok": False, "error": str(e)}
        if not isinstance(result, dict):
            result = {"result": result}
        responses.append({"id": c.get("id"), "name": name, "response": result})
    return responses


def default_tool_executor(allow_high_risk: bool = False):
    """A safety-gated executor that runs a tool by name through Ember's normal guard (capability
    mode + high-risk blocking), reusing ember_bridge.execute_tool. Imports agent lazily."""
    def _exec(name, args):
        import ember_bridge
        import agent
        param_types = {}
        try:
            import tool_args
            param_types = tool_args.build_param_types(agent.TOOL_DECLARATIONS)
        except Exception:
            param_types = {}
        return ember_bridge.execute_tool(name, args or {}, agent.TOOL_DISPATCH,
                                         allow_high_risk=allow_high_risk, param_types=param_types)
    return _exec


def _audio_blob(frame: bytes):
    """Wrap a PCM frame as the Live API expects, or a plain dict when genai is absent."""
    try:
        from google.genai import types
        return types.Blob(data=frame, mime_type=AUDIO_IN_MIME)
    except Exception:
        return {"data": frame, "mime_type": AUDIO_IN_MIME}


# ---------------------------------------------------------------------------
# Async loops (run against real OR injected session/mic/player)
# ---------------------------------------------------------------------------

async def _sender(session, mic, stop_event: "asyncio.Event") -> None:
    """Stream mic frames up until stop, the mic dries up, or the socket errors."""
    while not stop_event.is_set():
        try:
            frame = await mic.read()
        except Exception:
            break
        if not frame:
            break
        try:
            await session.send_realtime_input(audio=_audio_blob(frame))
        except Exception:
            break


async def _receiver(session, player, handlers: dict, stop_event: "asyncio.Event") -> None:
    """Consume server messages: play audio, surface transcripts, honour barge-in."""
    async for msg in session.receive():
        if stop_event.is_set():
            break
        p = parse_message(msg)
        if p["interrupted"]:
            try:
                player.clear()           # drop buffered Ember audio so barge-in feels instant
            except Exception:
                pass
            handlers.get("on_interrupted", _noop)()
            handlers.get("on_state", _noop)("listening")
        if p["audio"]:
            try:
                await player.feed(p["audio"])
            except Exception:
                pass
            handlers.get("on_state", _noop)("speaking")
        if p["user_text"]:
            handlers.get("on_user_text", _noop)(p["user_text"])
        if p["ember_text"]:
            handlers.get("on_ember_text", _noop)(p["ember_text"])
        if p.get("tool_calls"):
            await _run_and_reply_tools(session, p["tool_calls"], handlers)
        if p["turn_complete"]:
            handlers.get("on_turn_complete", _noop)()


async def _run_and_reply_tools(session, calls: list, handlers: dict) -> None:
    """Execute the model's tool calls (off the event loop so a slow tool can't stall audio) and
    send the results back into the live session so it can speak the outcome."""
    executor = handlers.get("tool_executor")
    if not executor:
        return
    for c in calls:
        handlers.get("on_tool", _noop)(c.get("name"))
    responses = await asyncio.to_thread(run_tool_calls, calls, executor)
    try:
        await session.send_tool_response(function_responses=responses)
    except Exception as e:
        handlers.get("on_error", _noop)(f"tool response failed: {e}")


async def _drive(session, mic, player, handlers: dict, stop_event: "asyncio.Event") -> None:
    """Run the sender + receiver concurrently against an already-open session."""
    send_task = asyncio.ensure_future(_sender(session, mic, stop_event))
    try:
        await _receiver(session, player, handlers, stop_event)
    finally:
        stop_event.set()
        send_task.cancel()
        try:
            await send_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Real audio devices (lazy pyaudio) — only used on-device
# ---------------------------------------------------------------------------

class _PyAudioMic:
    def __init__(self):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1,
                                     rate=AUDIO_IN_RATE, input=True, frames_per_buffer=CHUNK)

    async def read(self):
        return await asyncio.to_thread(self._stream.read, CHUNK, False)

    def close(self):
        for fn in (lambda: self._stream.stop_stream(), self._stream.close, self._pa.terminate):
            try:
                fn()
            except Exception:
                pass


class _PyAudioPlayer:
    def __init__(self):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        self._out = self._pa.open(format=pyaudio.paInt16, channels=1,
                                  rate=AUDIO_OUT_RATE, output=True)

    async def feed(self, pcm: bytes):
        await asyncio.to_thread(self._out.write, pcm)

    def clear(self):
        # Best-effort flush of buffered output for snappy barge-in.
        try:
            self._out.stop_stream()
            self._out.start_stream()
        except Exception:
            pass

    def close(self):
        for fn in (self._out.stop_stream, self._out.close, self._pa.terminate):
            try:
                fn()
            except Exception:
                pass


def available() -> bool:
    """True only if the real Live-voice path can run (genai + pyaudio present)."""
    try:
        import google.genai  # noqa: F401
        import pyaudio        # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public controller
# ---------------------------------------------------------------------------

class LiveVoice:
    """Start/stop a full-duplex Live API voice session on a background asyncio thread.

    Handlers (all optional, called from the asyncio thread — marshal to your UI):
      on_user_text(str), on_ember_text(str), on_state(str), on_turn_complete(),
      on_interrupted(), on_error(str).
    """

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, voice: str = DEFAULT_VOICE,
                 api_version: str = DEFAULT_API_VERSION, system_instruction: str = "",
                 on_user_text: Optional[Callable] = None, on_ember_text: Optional[Callable] = None,
                 on_state: Optional[Callable] = None, on_turn_complete: Optional[Callable] = None,
                 on_interrupted: Optional[Callable] = None, on_error: Optional[Callable] = None,
                 max_failures: int = 4, tools: Optional[list] = None,
                 tool_executor: Optional[Callable] = None, on_tool: Optional[Callable] = None):
        self.key = "".join((api_key or "").split())
        self.model = model or DEFAULT_MODEL
        self.voice = voice or DEFAULT_VOICE
        self.api_version = api_version or DEFAULT_API_VERSION
        self.system_instruction = system_instruction or ""
        self.max_failures = max_failures
        # Tool-calling: `tools` is the Live API tools block ([{function_declarations:[...]}]);
        # `tool_executor(name, args)->dict` actually runs them (safety-gated). When both are set
        # the voice model can operate the computer mid-conversation.
        self.tools = tools or []
        self.tool_executor = tool_executor
        self._handlers = {
            "on_user_text": on_user_text or _noop, "on_ember_text": on_ember_text or _noop,
            "on_state": on_state or _noop, "on_turn_complete": on_turn_complete or _noop,
            "on_interrupted": on_interrupted or _noop, "on_error": on_error or _noop,
            "on_tool": on_tool or _noop,          # notified (name) when the voice model runs a tool
            "tool_executor": tool_executor,       # carried through to the receiver loop
        }
        self._thread: Optional[threading.Thread] = None
        self._loop_stop: Optional[asyncio.Event] = None
        self._aioloop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_requested = threading.Event()
        self._running = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> dict:
        if self._running and self._thread and self._thread.is_alive():
            return {"ok": True, "running": True, "message": "live voice already running"}
        if not self.key:
            return {"ok": False, "error": "Add a Gemini API key in Settings to use natural voice."}
        if not available():
            return {"ok": False, "error": "Natural voice needs google-genai + pyaudio installed."}
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._thread_main, name="ember-live-voice", daemon=True)
        self._running = True
        self._thread.start()
        return {"ok": True, "running": True, "message": "natural voice listening"}

    def stop(self) -> dict:
        self._stop_requested.set()
        # Wake the async loops from this (other) thread via the running event loop.
        loop, ev = self._aioloop, self._loop_stop
        if loop is not None and ev is not None:
            try:
                loop.call_soon_threadsafe(ev.set)
            except Exception:
                pass
        th = self._thread
        if th is not None:
            th.join(timeout=5.0)
        self._running = False
        return {"ok": True, "message": "natural voice stopped"}

    def is_running(self) -> bool:
        return bool(self._running and self._thread and self._thread.is_alive())

    # -- internals ---------------------------------------------------------
    def _thread_main(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            self._handlers["on_error"](f"natural voice ended: {e}")
        finally:
            self._running = False
            self._handlers["on_state"]("idle")

    def _config(self) -> dict:
        # A dict config is tolerated across genai versions (no version-specific type names).
        cfg = {
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": self.voice}}},
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        if self.system_instruction:
            cfg["system_instruction"] = self.system_instruction
        if self.tools:
            cfg["tools"] = self.tools     # lets the model call Ember's tools mid-conversation
        return cfg

    async def _main(self):
        from google import genai
        try:
            from google.genai import types
            http_options = types.HttpOptions(api_version=self.api_version)
        except Exception:
            http_options = {"api_version": self.api_version}
        client = genai.Client(api_key=self.key, http_options=http_options)
        config = self._config()
        self._aioloop = asyncio.get_running_loop()
        backoff = 1.0
        failures = 0
        # Try the configured model first, then fall back through candidates - Google retires
        # dated Live preview model IDs on a schedule, and retrying the SAME dead model just
        # fails identically every time instead of ever recovering.
        candidates = [self.model] + [m for m in FALLBACK_MODELS if m and m != self.model]
        model_idx = 0
        while not self._stop_requested.is_set() and failures < self.max_failures:
            current_model = candidates[min(model_idx, len(candidates) - 1)]
            self._loop_stop = asyncio.Event()
            mic = player = None
            try:
                # Construct SEQUENTIALLY, not as one tuple assignment: a tuple form opens the mic
                # first, so if the player constructor then raises the binding never happens, `mic`
                # stays None, and the already-open input stream is orphaned - the device stays
                # captured (macOS orange dot on, mic busy for wake-word/push-to-talk) until the
                # process exits. Building them in order lets the except close the mic it opened.
                mic = _PyAudioMic()
                player = _PyAudioPlayer()
            except Exception as e:
                if mic is not None:
                    try:
                        mic.close()
                    except Exception:
                        pass
                self._handlers["on_error"](f"microphone/speaker unavailable: {e}")
                return
            try:
                async with client.aio.live.connect(model=current_model, config=config) as session:
                    failures = 0
                    backoff = 1.0
                    self.model = current_model
                    self._handlers["on_state"]("listening")
                    await _drive(session, mic, player, self._handlers, self._loop_stop)
            except Exception as e:
                failures += 1
                if _looks_like_bad_model(e) and model_idx < len(candidates) - 1:
                    model_idx += 1
                    self._handlers["on_error"](
                        f"model '{current_model}' unavailable — trying '{candidates[model_idx]}' "
                        f"({failures}/{self.max_failures})")
                else:
                    self._handlers["on_error"](f"connection issue ({failures}/{self.max_failures}): {e}")
                if not self._stop_requested.is_set():
                    await asyncio.sleep(min(backoff, 8.0))
                    backoff *= 2
            finally:
                for d in (mic, player):
                    try:
                        d and d.close()
                    except Exception:
                        pass
            if self._stop_requested.is_set():
                break
