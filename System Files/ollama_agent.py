"""Local Ollama agent — an offline chat brain (no API key, no rate limits).

Exposes the SAME interface as agent.Agent / claude_agent.ClaudeAgent (subscribe /
send_user_message / stop / reset, emitting agent.AgentEvent) so the app can swap to a
local model from the model picker.

Scope: a fully-offline brain that can also DRIVE THE COMPUTER with a curated set of core
LOCAL tools (terminal, files, screen + OCR, mouse/keyboard, system info, memory — see
ollama_tools.py). It calls those tools via Ollama's function-calling API, with the same
safety/confirmation + events as the cloud agent, and falls back to plain chat if the chosen
local model doesn't support tools. Cloud-only abilities (web/browser) still need Gemini/Claude;
for reliable local tool-use prefer a tool-capable model like qwen2.5 or llama3.1.
The module imports with only stdlib + requests (agent.py / google-genai are imported lazily)
so it stays testable without the cloud SDKs installed.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from typing import Callable

# NOTE: `requests` is imported LAZILY inside the HTTP methods (not at module top) so this module
# — and the pure helpers like extract_text_tool_calls + the OllamaAgent tool dispatch — import
# with only the standard library. That keeps the hermetic tests (and any non-networked use)
# working in environments where `requests` isn't installed (e.g. CI).

OLLAMA_BASE = "http://localhost:11434"

try:
    from agent import AgentEvent  # the real event type used by the UI in production
except Exception:  # google-genai not installed (dev/test) — keep the module importable
    from dataclasses import dataclass

    @dataclass
    class AgentEvent:  # structurally identical to agent.AgentEvent
        kind: str
        payload: object = None

try:
    from agent import PendingConfirmation  # so the UI's confirm dialog handles it identically
except Exception:
    import queue as _queue
    from dataclasses import dataclass, field

    @dataclass
    class PendingConfirmation:   # structurally identical to agent.PendingConfirmation
        tool_name: str
        args: dict
        reason: str
        response: "_queue.Queue" = field(default_factory=_queue.Queue)


OFFLINE_TOOLS_SYSTEM_PROMPT = (
    "You are Ember, running locally on the user's computer via Ollama — fully offline, no API "
    "key, no rate limits. You CAN control this computer using the provided tools: run terminal "
    "commands, read/write files, see the screen (screenshot + OCR), move/click the mouse, type, "
    "open apps, and read system info. To DO something, call a tool — don't just describe it. "
    "When asked to launch an app, ALWAYS use open_app(name); never guess or click a desktop, "
    "Dock, or taskbar icon. For a visible labelled control, prefer smart_click(target). Use raw "
    "click coordinates only for an unlabeled target; coordinates chosen from a screenshot are "
    "automatically translated to the full display. "
    "After the tools have done the work, reply with a short, plain summary of the result. Be "
    "concise and only take the actions the user asked for. Destructive or risky actions will ask "
    "the user to confirm."
)


CHAT_SYSTEM_PROMPT = (
    "You are Ember, running locally on the user's computer via Ollama — fully offline, with "
    "no API key and no rate limits. In this local mode you are a helpful, concise assistant: "
    "answer questions, write and edit text, brainstorm, explain, and reason step by step. "
    "You cannot see the screen or control the computer in local mode; if the user needs Ember "
    "to operate apps, the browser, or files, tell them to switch the model to Gemini or Claude "
    "in Settings, which enables full computer control."
)


def _memory_extras() -> str:
    try:
        from agent import system_extras
        return system_extras() or ""
    except Exception:
        return ""


_TOOLCALL_TAG_RE = re.compile(
    r"<(?:tool_call|function_call|tool|function)>(.*?)</(?:tool_call|function_call|tool|function)>",
    re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|tool_code|tool|python)?\s*(.*?)```", re.DOTALL)
_NAME_RE = re.compile(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def _iter_json_objects(text: str):
    """Yield each top-level {...} object in `text` as a parsed dict, skipping braces that
    appear inside string literals (so a value like \"{}\" doesn't break brace matching)."""
    out = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        out.append(json.loads(text[start:i + 1]))
                    except Exception:
                        pass
                    start = -1
    return out


def _canon_name(name, tool_names, aliases) -> str | None:
    """Map a model-supplied tool name (possibly an alias / different case) to a known tool."""
    if not isinstance(name, str):
        return None
    if name in tool_names:
        return name
    low = name.strip().lower()
    if low in tool_names:
        return low
    canon = (aliases or {}).get(low)
    return canon if canon in tool_names else None


def _coerce_toolcall_obj(obj, tool_names, aliases=None) -> dict | None:
    """Normalize a parsed object into Ollama's tool_call shape {function:{name,arguments}} if it
    names a known tool (resolving common aliases), else None. Accepts name/tool/function keys
    and arguments/parameters/args."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters")
    if args is None:
        args = obj.get("args")
    if isinstance(name, dict):  # e.g. {"function": {"name": ..., "arguments": ...}}
        inner = name
        name = inner.get("name")
        if args is None:
            args = inner.get("arguments") or inner.get("parameters")
    canon = _canon_name(name, tool_names, aliases)
    if canon is None:
        return None
    if not isinstance(args, dict):
        args = {}
    return {"function": {"name": canon, "arguments": args}}


def extract_text_tool_calls(content: str, tool_names, aliases=None) -> list:
    """Some local models emit tool calls as TEXT in the message body instead of using Ollama's
    structured tool_calls field (often wrapped in <tool_call>…/```json…``` or even malformed,
    e.g. {"name": "take_screenshot", "{}"}, or with an aliased name like "screenshot"). Pull any
    well-formed-enough calls out so the tool still runs — and the raw JSON doesn't leak into the
    chat. Pass `aliases` (alias->canonical) to resolve invented names. Returns a list of
    {function:{name,arguments}} (possibly empty)."""
    if not content:
        return []
    calls = []
    seen = set()
    chunks = list(_TOOLCALL_TAG_RE.findall(content)) + list(_FENCE_RE.findall(content)) + [content]
    for chunk in chunks:
        for obj in _iter_json_objects(chunk):
            c = _coerce_toolcall_obj(obj, tool_names, aliases)
            if c:
                key = json.dumps(c, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    calls.append(c)
    if not calls:
        # Fallback for malformed JSON: just find a known (or aliased) tool name in a "name" slot.
        for name in _NAME_RE.findall(content):
            canon = _canon_name(name, tool_names, aliases)
            if canon:
                calls.append({"function": {"name": canon, "arguments": {}}})
                break
    return calls


# Local models that can actually look at images (so we may send a screenshot). Everything else
# is treated as text-only — sending images would 400 ("model does not support multimodal").
_VISION_MODEL_HINTS = (
    "llava", "bakllava", "vision", "moondream", "minicpm-v", "qwen2-vl", "qwen2.5-vl",
    "qwen2.5vl", "llama3.2-vision", "llama-3.2-vision", "llama4", "llama-4", "pixtral",
    "cogvlm", "internvl", "gemma3", "gemma-3", "granite3.2-vision", "mistral-small3.1",
)


def model_supports_vision(model_name: str) -> bool:
    """Heuristic: can this local model accept image inputs? True only for known vision families;
    a runtime "multimodal not supported" 400 is still caught as a safety net."""
    m = (model_name or "").lower()
    return any(h in m for h in _VISION_MODEL_HINTS)


def _is_multimodal_error(body: str) -> bool:
    """True if an Ollama/endpoint error body says the model can't accept images."""
    b = (body or "").lower()
    return ("multimodal" in b) or ("does not support" in b and ("image" in b or "vision" in b))


def _strip_images(messages) -> bool:
    """Remove any `images` from chat messages in place (for text-only models). Returns True if
    anything was stripped."""
    changed = False
    for m in messages or []:
        if isinstance(m, dict) and m.get("images"):
            m.pop("images", None)
            changed = True
    return changed


def quick_complete(prompt: str, model: str = "", base_url: str = OLLAMA_BASE,
                   timeout: float = 30.0) -> str:
    """One-shot, non-streaming local completion (used for cheap background jobs like naming a
    chat). Returns the text, or "" on any problem. Never raises."""
    import requests
    res = resolve_model(model, base_url)
    if not res.get("ok"):
        return ""
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={"model": res["model"], "stream": False,
                  "messages": [{"role": "user", "content": prompt}],
                  "options": {"temperature": 0.2}},
            timeout=timeout,
        )
        if r.status_code != 200:
            return ""
        return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception:
        return ""


def _parse_stream_line(line) -> dict:
    """Parse one NDJSON line from Ollama's streaming /api/chat. Returns {} on a bad line."""
    try:
        if isinstance(line, (bytes, bytearray)):
            line = line.decode("utf-8", "replace")
        return json.loads(line)
    except Exception:
        return {}


def resolve_model(preferred: str = "", base_url: str = OLLAMA_BASE) -> dict:
    """Decide which Ollama model to use: the preferred one if given, else the first installed.
    Returns {ok: True, model} or {ok: False, error} with a helpful message."""
    preferred = (preferred or "").strip()
    try:
        import local_ai
        st = local_ai.local_ai_status()
    except Exception as e:
        return {"ok": False, "error": f"cannot reach Ollama: {e}"}
    if not st.get("running"):
        return {"ok": False, "error": st.get("note")
                or "Ollama is not running. Install it from https://ollama.com, then: ollama pull llama3.2"}
    models = st.get("models") or []
    if preferred:
        return {"ok": True, "model": preferred}   # trust the user; the API errors clearly if wrong
    if models:
        return {"ok": True, "model": models[0]}
    return {"ok": False, "error": "Ollama is running but no models are pulled. Run: ollama pull llama3.2"}


class OllamaAgent:
    """Drop-in local chat backend. Same interface the UI uses for Agent / ClaudeAgent."""

    def __init__(self, model_name: str = "", auto_screenshot: bool = True,
                 base_url: str = OLLAMA_BASE, **_kwargs):
        self.requested_model = (model_name or "").strip()
        self.active_model = self.requested_model or "ollama"
        self.base_url = (base_url or OLLAMA_BASE).rstrip("/")
        self.auto_screenshot = auto_screenshot  # accepted for parity
        # Local tool-use: let the local model drive Ember's core LOCAL tools (shell, files,
        # screen, mouse/keyboard, system info). Falls back to plain chat if the model/endpoint
        # doesn't support function calling.
        self.tools_enabled = bool(_kwargs.get("tools_enabled", True))
        self._messages: list[dict] = []
        self._pending_images: list[str] = []   # base64 imgs (e.g. a screenshot) for the vision model
        # None = unknown (decided per model); True/False = can this model accept image input.
        self._vision_ok: bool | None = None
        self._event_subs: list[Callable[[AgentEvent], None]] = []
        self._stop_flag = threading.Event()

    # --- interface parity with Agent/ClaudeAgent ---
    def reset(self):
        self._messages = []

    def load_history(self, turns):
        """Seed the conversation from prior visible turns so a model/provider switch keeps
        context. `turns` is [{"role": "user"|"assistant", "text": str}] (already normalized).
        Text-only; the system prompt is prepended at call time, not stored here."""
        msgs = []
        for t in turns or []:
            role = t.get("role")
            text = (t.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                msgs.append({"role": role, "content": text})
        if msgs:
            self._messages = msgs

    def stop(self):
        self._stop_flag.set()

    def subscribe(self, fn: Callable[[AgentEvent], None]):
        self._event_subs.append(fn)

    def _emit(self, ev: AgentEvent):
        for fn in self._event_subs:
            try:
                fn(ev)
            except Exception:
                traceback.print_exc()

    def send_user_message(self, text: str, images: list | None = None):
        # `images` (base64 PNG/JPEG) lets the local VISION model analyse pasted/dropped pictures.
        threading.Thread(target=self._run_turn, args=(text, images), daemon=True).start()

    def _system_prompt(self) -> str:
        return CHAT_SYSTEM_PROMPT + _memory_extras()

    def _run_turn(self, user_text: str, images: list | None = None):
        self._stop_flag.clear()
        try:
            res = resolve_model(self.requested_model, self.base_url)
            if not res.get("ok"):
                self._emit(AgentEvent("error", res.get("error", "Ollama unavailable")))
                return
            self.active_model = res["model"]
            if self._vision_ok is None:
                self._vision_ok = model_supports_vision(self.active_model)
            user_msg = {"role": "user", "content": user_text}
            if images:   # a pasted/dropped image for the vision model to analyse
                if self._vision_ok:
                    user_msg["images"] = list(images)
                else:   # text-only local model — sending images would 400; explain instead
                    user_msg["content"] = (
                        user_text + f"\n\n[An image was attached, but the local model "
                        f"'{self.active_model}' is text-only and can't view images. Switch to "
                        f"Gemini/Claude, or a local vision model like llava, to analyse pictures.]")
            self._messages.append(user_msg)
            # Try the tool-using loop first. If the model/endpoint doesn't support tools, fall
            # back to a plain streaming chat so it still answers.
            if self.tools_enabled and self._run_tool_loop():
                return
            self._plain_chat()
        except Exception as e:
            self._emit(AgentEvent("error", f"{type(e).__name__}: {e}"))
        finally:
            self._emit(AgentEvent("done"))

    def _plain_chat(self, _retry: bool = True):
        """Stream a plain text answer (no tools) — the original local-chat behaviour."""
        import requests
        if not self._vision_ok:
            _strip_images(self._messages)
        payload = {
            "model": self.active_model,
            "messages": [{"role": "system", "content": self._system_prompt()}] + self._messages,
            "stream": True,
        }
        streamed = []
        try:
            with requests.post(f"{self.base_url}/api/chat", json=payload,
                               stream=True, timeout=300) as r:
                if r.status_code != 200:
                    body = (r.text or "")
                    if _is_multimodal_error(body) and _retry:
                        self._vision_ok = False
                        _strip_images(self._messages)
                        return self._plain_chat(_retry=False)   # retry once, text-only
                    self._emit(AgentEvent("error",
                               f"Ollama returned {r.status_code}: {body[:300]}"))
                    return
                for line in r.iter_lines():
                    if self._stop_flag.is_set():
                        break
                    if not line:
                        continue
                    chunk = _parse_stream_line(line)
                    if not chunk:
                        continue
                    delta = (chunk.get("message") or {}).get("content") or ""
                    if delta:
                        streamed.append(delta)
                        self._emit(AgentEvent("stream_chunk", delta))
                    if chunk.get("done"):
                        break
        except requests.exceptions.RequestException as e:
            self._emit(AgentEvent("error", f"Local AI unavailable: {e} (is Ollama running?)"))
            return
        if streamed:
            self._emit(AgentEvent("stream_end", None))
            self._messages.append({"role": "assistant", "content": "".join(streamed).strip()})
        else:
            self._emit(AgentEvent("message", "[no response from the local model]"))

    def _run_tool_loop(self) -> bool:
        """Let the local model call Ember's curated LOCAL tools. Returns True if it produced a
        final answer (or errored), False only if this model can't be driven at all (caller falls
        back to a plain streamed chat).

        Robust against the two ways local models 'mess up': (1) emitting tool calls as TEXT /
        under invented names (we parse + alias-resolve them so the action runs and raw JSON never
        leaks), and (2) rejecting the structured `tools` field or image input (we retry without
        them, and feed OCR text in place of a screenshot for text-only models)."""
        import ollama_tools
        import requests
        sys_prompt = OFFLINE_TOOLS_SYSTEM_PROMPT + _memory_extras()
        include_tools = True       # send the structured `tools` field until the model rejects it
        progressed = False         # have we executed any tool / consumed a real model turn?
        last_call_signature = ""
        repeated_call_rounds = 0
        while not self._stop_flag.is_set():
            if self._stop_flag.is_set():
                self._emit(AgentEvent("message", "[stopped]"))
                return True
            if not self._vision_ok:
                _strip_images(self._messages)
            payload = {
                "model": self.active_model,
                "messages": [{"role": "system", "content": sys_prompt}] + self._messages,
                "stream": False,
            }
            if include_tools:
                payload["tools"] = ollama_tools.TOOLS
            try:
                r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=300)
            except requests.exceptions.RequestException as e:
                self._emit(AgentEvent("error", f"Local AI unavailable: {e} (is Ollama running?)"))
                return True
            if r.status_code != 200:
                body = (r.text or "")
                if _is_multimodal_error(body):
                    # Model can't see images — drop them (and any pending screenshot) and retry.
                    self._vision_ok = False
                    self._pending_images = []
                    _strip_images(self._messages)
                    continue
                if include_tools and ("tool" in body.lower() or "function" in body.lower()
                                      or r.status_code == 400):
                    # Endpoint/model doesn't accept the structured tools field. Keep going WITHOUT
                    # it and parse any tool calls the model writes as text — so we never drop to a
                    # chat that dumps raw tool-call JSON at the user.
                    include_tools = False
                    continue
                self._emit(AgentEvent("error", f"Ollama returned {r.status_code}: {body[:300]}"))
                return True
            try:
                msg = (r.json().get("message")) or {}
            except Exception:
                if progressed:
                    self._emit(AgentEvent("error", "the local model returned an unreadable response"))
                    return True
                return False
            self._messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                content = (msg.get("content") or "").strip()
                # Some local models write the tool call as TEXT (e.g. {"name": "screenshot", ...})
                # instead of the structured field. Parse + alias-resolve and run those so the
                # action happens instead of the raw JSON leaking into the chat.
                text_calls = extract_text_tool_calls(content, ollama_tools.TOOL_NAMES,
                                                      ollama_tools.TOOL_ALIASES)
                if text_calls:
                    tool_calls = text_calls
                else:
                    self._emit(AgentEvent("message", content or "[no response from the local model]"))
                    return True
            progressed = True
            call_signature = json.dumps(tool_calls, sort_keys=True, default=str)
            repeated_call_rounds = (repeated_call_rounds + 1
                                    if call_signature == last_call_signature else 1)
            last_call_signature = call_signature
            if repeated_call_rounds >= 4:
                self._emit(AgentEvent(
                    "error", "Stopped a repeated-action loop after the local model requested "
                    "the same operation four times."))
                return True
            for tc in tool_calls:
                if self._stop_flag.is_set():
                    break
                fnobj = tc.get("function") or {}
                name = ollama_tools.resolve_name(fnobj.get("name") or "")
                result = self._exec_tool(name, fnobj.get("arguments"))
                self._messages.append({
                    "role": "tool", "name": name,
                    "content": json.dumps(result)[:6000],
                })
            # A screenshot was captured — hand the image to a vision model, or OCR text otherwise.
            if self._pending_images:
                if self._vision_ok:
                    self._messages.append({
                        "role": "user",
                        "content": "[Screenshot captured — look at the image and continue.]",
                        "images": self._pending_images,
                    })
                else:
                    self._messages.append({
                        "role": "user",
                        "content": ("[Screenshot captured. This local model is text-only and "
                                    "can't view images, so here is the on-screen text (OCR):]\n\n"
                                    + self._ocr_fallback_text()),
                    })
                self._pending_images = []
        self._emit(AgentEvent("message", "[stopped]"))
        return True

    def _ocr_fallback_text(self) -> str:
        """On-screen text via OCR, for text-only local models that can't view a screenshot."""
        try:
            import ollama_tools
            res = ollama_tools.call("read_screen_text", {})
            if isinstance(res, dict):
                if res.get("ok") is False:
                    return f"(OCR unavailable: {res.get('error', 'no text extracted')})"
                txt = res.get("text") or res.get("ocr") or res.get("result") or ""
                return (str(txt)[:4000] or "(no readable text found on screen)")
            return str(res)[:4000]
        except Exception as e:
            return f"(OCR unavailable: {e})"

    def _exec_tool(self, name: str, raw_args) -> dict:
        """Run one curated tool with safety + confirmation, emitting the same events the cloud
        agent does so the UI shows the activity."""
        import ollama_tools
        import safety
        name = ollama_tools.resolve_name(name)
        args = ollama_tools.coerce_args(name, raw_args)
        self._emit(AgentEvent("tool_call", {"name": name, "args": args}))
        if name not in ollama_tools.TOOL_NAMES:
            result = {"ok": False, "error": f"unknown tool {name}"}
            self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
            return result
        # Confirmation for risky (non-readonly) actions, mirroring the cloud agent.
        try:
            risk, reason = safety.classify(name, args)
            if name not in ollama_tools.READONLY and safety.needs_confirmation(risk):
                pending = PendingConfirmation(name, args, reason)
                self._emit(AgentEvent("confirm", pending))
                if not pending.response.get():
                    result = {"ok": False, "error": "user denied this action"}
                    self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                    return result
        except Exception:
            pass
        result = ollama_tools.call(name, args)
        # If the tool produced an image (e.g. take_screenshot), hand it to the VISION model on
        # the next turn instead of dumping a huge base64 blob into the text tool-result.
        if isinstance(result, dict) and result.get("image_b64"):
            self._pending_images.append(result.pop("image_b64"))
        self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
        try:
            import memory
            memory.log_action(name, args, str(result)[:200])
        except Exception:
            pass
        return result
