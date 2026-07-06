"""OpenAI-powered agent loop — same interface as agent.Agent so the UI can swap backends.

This is a drop-in sibling of claude_agent.ClaudeAgent and ollama_agent.OllamaAgent: it
exposes subscribe / send_user_message / stop / reset and emits agent.AgentEvent, reuses the
shared TOOL_DECLARATIONS / TOOL_DISPATCH and the safety/confirmation layer, and drives the
same ~290 tools — only the wire format (OpenAI Chat Completions) differs.

"Other API-key providers" come for free: the OpenAI SDK talks to any OpenAI-compatible
endpoint, so passing a `base_url` (+ that provider's key + a model id) points Ember at
xAI Grok, DeepSeek, Groq, Mistral, OpenRouter, Together, Perplexity, or a local server
(LM Studio / vLLM / llama.cpp) — all through this one agent. See models.OPENAI_COMPAT_BASES.

The `openai` SDK is imported LAZILY (inside __init__), so this module — and its pure helpers
(_build_openai_tools, _lower_types) — import with only the standard library. That keeps the
hermetic tests and any non-networked use working where `openai` isn't installed (e.g. CI).
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Callable

import safety

# The real event/pending types the UI depends on live in agent.py, which imports google-genai.
# Import them if available; otherwise fall back to structurally-identical stand-ins so this
# module — and its pure helpers (_lower_types, _build_openai_tools) — import with only the
# standard library (keeps the hermetic tests + any non-cloud use working, as in ollama_agent).
try:
    from agent import AgentEvent  # the real event type used by the UI in production
except Exception:  # google-genai not installed (dev/test)
    from dataclasses import dataclass

    @dataclass
    class AgentEvent:  # structurally identical to agent.AgentEvent
        kind: str
        payload: object = None

try:
    from agent import PendingConfirmation, PendingHumanPause
except Exception:
    import queue as _queue
    from dataclasses import dataclass, field

    @dataclass
    class PendingConfirmation:
        tool_name: str
        args: dict
        reason: str
        response: "_queue.Queue" = field(default_factory=_queue.Queue)

    @dataclass
    class PendingHumanPause:
        reason: str
        what_you_need: str
        response: "_queue.Queue" = field(default_factory=_queue.Queue)


def _agent_registries():
    """Lazily fetch (TOOL_DECLARATIONS, TOOL_DISPATCH) from the live app (heavy import)."""
    import agent
    return agent.TOOL_DECLARATIONS, agent.TOOL_DISPATCH

# Tool-produced screenshots that should be fed back to the model as an image. OpenAI's
# `role:"tool"` messages are text-only, so these are attached as a follow-up user image
# message instead of being nested inside the tool result (as the Claude backend does).
_SCREENSHOT_TOOLS = ("take_screenshot", "capture_window", "browser_screenshot")


def _lower_types(node):
    """Recursively lowercase JSON-Schema `type` values so OpenAI accepts them.

    Ember's TOOL_DECLARATIONS use Gemini's uppercase types ("STRING", "OBJECT", …); the
    OpenAI function schema wants standard lowercase JSON-Schema ("string", "object", …).
    """
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            else:
                out[k] = _lower_types(v)
        return out
    if isinstance(node, list):
        return [_lower_types(x) for x in node]
    return node


def _build_openai_tools(declarations):
    """Convert Ember tool declarations into OpenAI's function-tool format."""
    out = []
    for decl in declarations:
        params = _lower_types(decl.get("parameters", {"type": "object"}))
        if not isinstance(params, dict) or "type" not in params:
            params = {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": decl["name"],
                "description": decl.get("description", ""),
                "parameters": params,
            },
        })
    return out


def _reassemble_stream(chunks, on_text=None, stop=None):
    """Fold an OpenAI streaming response into (assistant_message_dict, finish_reason).

    Pure + provider-agnostic: `chunks` is any iterable of objects shaped like OpenAI stream
    chunks (`.choices[0].delta` with `.content` / `.tool_calls`, and `.choices[0].finish_reason`).
    Text deltas are forwarded to `on_text(str)` as they arrive. `stop()` (if given) is polled
    each chunk to abort early. tool_calls arrive fragmented across deltas — id + name early,
    the JSON `arguments` string in pieces — and are reassembled by their `index`.
    """
    text_parts: list[str] = []
    tool_acc: dict[int, dict] = {}
    finish_reason = None
    for chunk in chunks:
        if stop is not None and stop():
            break
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        text = getattr(delta, "content", None)
        if text:
            text_parts.append(text)
            if on_text is not None:
                on_text(text)
        for tcd in (getattr(delta, "tool_calls", None) or []):
            idx = getattr(tcd, "index", 0) or 0
            slot = tool_acc.setdefault(
                idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
            if getattr(tcd, "id", None):
                slot["id"] = tcd.id
            fn = getattr(tcd, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["function"]["name"] = fn.name
                if getattr(fn, "arguments", None):
                    slot["function"]["arguments"] += fn.arguments

    message = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_acc:
        ordered = [tool_acc[i] for i in sorted(tool_acc)]
        ordered = [t for t in ordered if t.get("id") and t["function"].get("name")]
        if ordered:
            message["tool_calls"] = ordered
            if finish_reason is None:
                finish_reason = "tool_calls"
    if finish_reason is None:
        finish_reason = "stop"
    return message, finish_reason


class OpenAIAgent:
    """Drop-in replacement for Agent that uses OpenAI's Chat Completions API.

    Works with OpenAI (ChatGPT models) and any OpenAI-compatible provider via `base_url`.
    """

    def __init__(self, api_key: str, model_name: str = "gpt-5",
                 auto_screenshot: bool = True, base_url: str | None = None,
                 provider_label: str = "OpenAI", **_kwargs):
        if not api_key:
            raise ValueError(f"{provider_label} API key required")
        # Strip whitespace/newlines so a bad paste can't become an illegal HTTP header.
        api_key = "".join((api_key or "").split())
        self.api_key = api_key
        # `openai:<model>` is an escape hatch for custom/compatible endpoints — strip the
        # prefix before it reaches the API, which only knows the bare model id.
        if isinstance(model_name, str) and model_name.startswith("openai:"):
            model_name = model_name.split(":", 1)[1]
        self.model_name = model_name
        self.active_model = model_name
        self.auto_screenshot = auto_screenshot
        self.base_url = (base_url or "").strip() or None
        self.provider_label = provider_label or "OpenAI"
        self.fallback_models = []
        try:
            import openai
        except ImportError as e:  # pragma: no cover - only hit when the SDK is absent
            raise RuntimeError(
                "The 'openai' package is required for the OpenAI/ChatGPT provider. "
                "Install it with: pip install openai"
            ) from e
        self._openai = openai
        client_kwargs = {"api_key": api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = openai.OpenAI(**client_kwargs)
        self._declarations, self._dispatch = _agent_registries()
        self._messages: list[dict] = []
        self._tools = _build_openai_tools(self._declarations)
        self._event_subs: list[Callable[[AgentEvent], None]] = []
        self._stop_flag = threading.Event()

    # --- interface parity with agent.Agent / ClaudeAgent -------------------------------

    def reset(self):
        self._messages = []

    def load_history(self, turns):
        """Seed the conversation from prior visible turns so a model/provider switch keeps
        context. `turns` is [{"role": "user"|"assistant", "text": str}] (already normalized:
        alternating, starts with user). Text-only — tool-call details don't carry across a
        switch, but what was said does."""
        msgs = [{"role": "system", "content": self._system_prompt()}]
        for t in turns or []:
            role = t.get("role")
            text = (t.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                msgs.append({"role": role, "content": text})
        if len(msgs) > 1:
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

    def send_user_message(self, text: str):
        t = threading.Thread(target=self._run_turn, args=(text,), daemon=True)
        t.start()

    # --- helpers -----------------------------------------------------------------------

    def _system_prompt(self) -> str:
        import agent
        base = agent.BASE_SYSTEM_PROMPT
        extras = ""
        try:
            extras = agent.system_extras() or ""
        except Exception:
            extras = ""
        return base + ("\n\n" + extras if extras else "")

    def _user_text(self, text: str) -> str:
        # The model DECIDES when to look at the screen — it has take_screenshot and the system
        # prompt tells it when — so we never keyword-attach a capture up front.
        if not getattr(self, "auto_screenshot", True):
            text += ("\n# Screen viewing is OFF (user setting): do NOT take_screenshot / "
                     "capture_window / read_screen_text; use the browser DOM, files, and shell.")
        return text

    def _compact_result(self, result: dict, max_str: int = 3000) -> dict:
        # Kept in sync with the Gemini/Claude backends' compaction limits so every model is
        # handed the same amount of tool context.
        out = {}
        for k, v in result.items():
            if k == "image_b64":
                continue
            if isinstance(v, str) and len(v) > max_str:
                out[k] = v[:max_str] + f"...[truncated {len(v) - max_str} chars]"
            elif isinstance(v, list) and len(v) > 60:
                out[k] = list(v[:60]) + [f"...[{len(v) - 60} more]"]
            else:
                out[k] = v
        return out

    # --- turn loop ---------------------------------------------------------------------

    def _heal_dangling_tool_calls(self):
        """Drop a trailing assistant message whose tool_calls were never answered.

        If the user hit Stop mid-execution last turn, history can end on an assistant message
        with tool_calls but no matching tool results — which OpenAI rejects with a 400. Trim it
        so the next turn starts from a clean, valid state.
        """
        while self._messages:
            last = self._messages[-1]
            if last.get("role") == "assistant" and last.get("tool_calls"):
                self._messages.pop()
            else:
                break

    def _run_turn(self, user_text: str):
        self._stop_flag.clear()
        try:
            if not self._messages:
                self._messages.append({"role": "system", "content": self._system_prompt()})
            self._heal_dangling_tool_calls()
            self._messages.append({"role": "user", "content": self._user_text(user_text)})
            for _ in range(12):
                if self._stop_flag.is_set():
                    return
                message, finish_reason = self._call_openai()
                if message is None:
                    return
                tool_calls = message.get("tool_calls") or []
                # Visible text was already streamed to the UI via stream_chunk/stream_end
                # inside _call_openai — don't re-emit it as a "message".
                self._messages.append(message)
                if not tool_calls or finish_reason not in ("tool_calls", "function_call"):
                    if finish_reason == "length":
                        self._emit(AgentEvent("message",
                            "[response hit the length limit — say 'continue' to finish it]"))
                    elif finish_reason == "content_filter":
                        self._emit(AgentEvent("message",
                            "[the response was stopped by the provider's content filter]"))
                    return

                pending_images: list[str] = []
                for tc in tool_calls:
                    if self._stop_flag.is_set():
                        return
                    name = tc.get("function", {}).get("name", "")
                    raw_args = tc.get("function", {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except (ValueError, TypeError):
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    self._emit(AgentEvent("tool_call", {"name": name, "args": args}))

                    risk, reason = safety.classify(name, args)
                    allowed_by_mode, mode_reason = safety.mode_allows(name, risk)
                    if not allowed_by_mode:
                        result = {"ok": False, "error": mode_reason,
                                  "blocked_by_mode": safety.current_mode()}
                        self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                        try:
                            import audit
                            audit.record(name, args, risk, mode_reason)
                        except Exception:
                            pass
                        self._append_tool_result(tc.get("id"), result)
                        continue
                    if safety.needs_confirmation(risk):
                        pending = PendingConfirmation(name, args, reason)
                        self._emit(AgentEvent("confirm", pending))
                        if not pending.response.get():
                            result = {"ok": False, "error": "user denied this action"}
                            self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                            self._append_tool_result(tc.get("id"), result)
                            continue

                    if name == "ask_claude":
                        result = {"ok": True, "note": "Ember is already running on a cloud model - no escalation needed"}
                    elif name == "pause_for_human":
                        result = self._handle_human_pause(args)
                    else:
                        fn = self._dispatch.get(name)
                        if not fn:
                            result = {"ok": False, "error": f"unknown tool {name}"}
                        else:
                            try:
                                result = fn(**args)
                            except TypeError as e:
                                result = {"ok": False, "error": f"bad args: {e}"}
                            except Exception as e:
                                result = {"ok": False, "error": str(e)}
                    if not isinstance(result, dict):
                        result = {"ok": True, "result": result}
                    self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                    _brief = str(result.get("error") or result.get("action") or "")[:200]
                    try:
                        import memory
                        memory.log_action(name, args, _brief)
                    except Exception:
                        pass
                    try:
                        import audit
                        audit.record(name, args, risk, _brief)
                    except Exception:
                        pass
                    self._append_tool_result(tc.get("id"), result)
                    if (name in _SCREENSHOT_TOOLS and result.get("ok")
                            and result.get("image_b64")):
                        pending_images.append(result["image_b64"])

                # OpenAI tool messages are text-only, so any tool-produced screenshots are
                # handed back as a single follow-up user image message.
                if pending_images:
                    content = [{"type": "text",
                                "text": "Screenshot(s) produced by the tool call(s) above:"}]
                    for b64 in pending_images:
                        content.append({"type": "image_url", "image_url":
                            {"url": f"data:image/png;base64,{b64}"}})
                    self._messages.append({"role": "user", "content": content})
            self._emit(AgentEvent("message", "[step limit reached - say 'continue' to keep going]"))
        except Exception as e:
            self._emit(AgentEvent("error", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"))
        finally:
            self._emit(AgentEvent("done"))

    def _append_tool_result(self, tool_call_id, result: dict):
        sanitized = self._compact_result(result)
        try:
            text = json.dumps(sanitized, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(sanitized)
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id or "",
            "content": text[:20000],
        })

    def _call_openai(self):
        """Run one streamed model call. Returns (assistant_message_dict, finish_reason).

        Streams text token-by-token (emitting stream_chunk/stream_end) and reassembles the
        assistant message — including any tool_calls, which arrive as fragmented deltas.
        """
        delays = [1, 3, 8]
        for attempt in range(len(delays) + 1):
            streamed = {"any": False}
            try:
                stream = self._client.chat.completions.create(
                    model=self.active_model,
                    messages=self._messages,
                    tools=self._tools,
                    tool_choice="auto",
                    stream=True,
                )

                def _on_text(t):
                    streamed["any"] = True
                    self._emit(AgentEvent("stream_chunk", t))

                message, finish_reason = _reassemble_stream(
                    stream, on_text=_on_text, stop=self._stop_flag.is_set)
                if streamed["any"]:
                    self._emit(AgentEvent("stream_end", None))
                return message, finish_reason
            except Exception as e:
                if streamed["any"]:
                    # Close out the partial bubble so a retry starts a fresh one.
                    self._emit(AgentEvent("stream_end", None))
                status = self._status_code(e)
                retryable = status in (408, 409, 429, 500, 502, 503, 504)
                if retryable and attempt < len(delays):
                    wait_s = delays[attempt]
                    self._emit(AgentEvent("message",
                        f"[{self.provider_label} {status} - retrying in {wait_s}s "
                        f"({attempt + 1}/{len(delays) + 1})]"))
                    time.sleep(wait_s)
                    continue
                raise
        return None, "error"

    def _status_code(self, exc) -> int | None:
        for attr in ("status_code", "status", "http_status", "code"):
            val = getattr(exc, attr, None)
            if isinstance(val, int):
                return val
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        return code if isinstance(code, int) else None

    def _handle_human_pause(self, args: dict) -> dict:
        pending = PendingHumanPause(
            reason=args.get("reason", "manual step required"),
            what_you_need=args.get("what_you_need", "complete the step then resume"),
        )
        self._emit(AgentEvent("human_pause", pending))
        note = pending.response.get()
        return {"ok": True, "resumed": True, "user_note": note or "(no note)"}
