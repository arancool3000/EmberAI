"""Claude-powered agent loop. Same interface as agent.Agent so the UI can swap backends."""
from __future__ import annotations

import base64
import json
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

import tools
import memory
import models
import safety
import agent as gemini_agent  # for shared declarations + system prompt builder
from agent import (
    AgentEvent, PendingConfirmation, PendingHumanPause, PendingClaudeResponse,
    TOOL_DECLARATIONS, TOOL_DISPATCH, BASE_SYSTEM_PROMPT, system_extras,
)


def _gemini_type_to_anthropic(node):
    """Recursively lowercase JSON Schema types so Anthropic accepts them."""
    if isinstance(node, dict):
        new = {}
        for k, v in node.items():
            if k == "type" and isinstance(v, str):
                new[k] = v.lower()
            else:
                new[k] = _gemini_type_to_anthropic(v)
        return new
    if isinstance(node, list):
        return [_gemini_type_to_anthropic(x) for x in node]
    return node


def _build_anthropic_tools():
    out = []
    for decl in TOOL_DECLARATIONS:
        out.append({
            "name": decl["name"],
            "description": decl.get("description", ""),
            "input_schema": _gemini_type_to_anthropic(decl.get("parameters", {"type": "object"})),
        })
    return out


class ClaudeAgent:
    """Drop-in replacement for Agent that uses Anthropic's Messages API."""

    def __init__(self, api_key: str, model_name: str = "claude-opus-4-8",
                 auto_screenshot: bool = True, **_kwargs):
        if not api_key:
            raise ValueError("Anthropic API key required for Claude as primary")
        # Strip whitespace/newlines so a bad paste can't become an illegal HTTP header.
        api_key = "".join((api_key or "").split())
        self.api_key = api_key
        self.model_name = model_name
        self.active_model = model_name
        self.auto_screenshot = auto_screenshot
        self.anthropic_key = api_key
        self.anthropic_model = model_name
        self.fallback_models = []
        self._client = anthropic.Anthropic(api_key=api_key)
        self._messages: list[dict] = []
        self._tools = _build_anthropic_tools()
        self._event_subs: list[Callable[[AgentEvent], None]] = []
        self._stop_flag = threading.Event()

    def reset(self):
        self._messages = []

    def load_history(self, turns):
        """Seed the conversation from prior visible turns so a model/provider switch keeps
        context. `turns` is [{"role": "user"|"assistant", "text": str}] (already normalized:
        alternating, starts with user). Text-only; the separate system prompt is unaffected."""
        msgs = []
        for t in turns or []:
            role = t.get("role")
            text = (t.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                msgs.append({"role": role, "content": [{"type": "text", "text": text}]})
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

    def send_user_message(self, text: str):
        t = threading.Thread(target=self._run_turn, args=(text,), daemon=True)
        t.start()

    def _user_block(self, text: str) -> list:
        # The model DECIDES when to look at the screen — it has take_screenshot and the system
        # prompt tells it when to use it — so we never keyword-attach a capture up front.
        if not getattr(self, "auto_screenshot", True):
            text += ("\n# Screen viewing is OFF (user setting): do NOT take_screenshot / "
                     "capture_window / read_screen_text; use the browser DOM, files, and shell.")
        return [{"type": "text", "text": text}]

    def _compact_result(self, result: dict, max_str: int = 3000) -> dict:
        # Kept in sync with the Gemini backend's compaction limits (agent.py) so both
        # models are handed the same amount of tool context.
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

    def _run_turn(self, user_text: str):
        self._stop_flag.clear()
        try:
            self._messages.append({"role": "user", "content": self._user_block(user_text)})
            last_call_signature = ""
            repeated_call_rounds = 0
            while not self._stop_flag.is_set():
                if self._stop_flag.is_set():
                    return
                response = self._call_claude()
                blocks = response.content
                tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
                # Visible text was already streamed to the UI via stream_chunk/stream_end
                # inside _call_claude — don't re-emit it as a "message" (that would duplicate
                # the bubble and double-write chat history).
                self._messages.append({"role": "assistant", "content":
                    [self._anthropic_block_to_dict(b) for b in blocks]})
                if not tool_uses or response.stop_reason != "tool_use":
                    # Surface non-tool stop reasons instead of returning silently.
                    if response.stop_reason == "max_tokens":
                        self._emit(AgentEvent("message",
                            "[response hit the length limit — say 'continue' to finish it]"))
                    elif response.stop_reason == "refusal":
                        details = getattr(response, "stop_details", None)
                        why = getattr(details, "explanation", None) or "declined for safety reasons"
                        self._emit(AgentEvent("message", f"[Claude declined: {why}]"))
                    return
                call_signature = json.dumps([
                    {"name": tu.name, "args": tools._to_plain(tu.input) if tu.input else {}}
                    for tu in tool_uses], sort_keys=True, default=str)
                repeated_call_rounds = (repeated_call_rounds + 1
                                        if call_signature == last_call_signature else 1)
                last_call_signature = call_signature
                if repeated_call_rounds >= 4:
                    self._emit(AgentEvent(
                        "error", "Stopped a repeated-action loop after Claude requested the "
                        "same operation four times."))
                    return
                tool_results = []
                for tu in tool_uses:
                    if self._stop_flag.is_set():
                        return
                    name, args = tu.name, tools._to_plain(tu.input) if tu.input else {}
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
                        tool_results.append((tu.id, result, name))
                        continue
                    if safety.needs_confirmation(risk):
                        pending = PendingConfirmation(name, args, reason)
                        self._emit(AgentEvent("confirm", pending))
                        if not pending.response.get():
                            result = {"ok": False, "error": "user denied this action"}
                            self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                            tool_results.append((tu.id, result, name))
                            continue
                    if name == "ask_claude":
                        result = {"ok": True, "note": "Ember is already running on Claude - no escalation needed"}
                    elif name == "pause_for_human":
                        result = self._handle_human_pause(args)
                    else:
                        fn = TOOL_DISPATCH.get(name)
                        if not fn:
                            result = {"ok": False, "error": f"unknown tool {name}"}
                        else:
                            try:
                                result = fn(**args)
                            except TypeError as e:
                                result = {"ok": False, "error": f"bad args: {e}"}
                            except Exception as e:
                                result = {"ok": False, "error": str(e)}
                    self._emit(AgentEvent("tool_result", {"name": name, "result": result}))
                    _brief = str(result.get("error") or result.get("action") or "")[:200]
                    memory.log_action(name, args, _brief)
                    try:
                        import audit
                        audit.record(name, args, risk, _brief)
                    except Exception:
                        pass
                    tool_results.append((tu.id, result, name))

                content = []
                for tu_id, result, name in tool_results:
                    sanitized = self._compact_result(result)
                    try:
                        text = json.dumps(sanitized, ensure_ascii=False, default=str)
                    except (TypeError, ValueError):
                        text = str(sanitized)
                    blocks_out = [{"type": "text", "text": text[:20000]}]
                    # Attach a tool-produced screenshot INSIDE its own tool_result so the model
                    # attributes the image to the call that produced it (and so parallel
                    # screenshots each keep their image, instead of only the last surviving).
                    if (name in ("take_screenshot", "capture_window", "browser_screenshot")
                            and result.get("ok") and result.get("image_b64")):
                        blocks_out.append({"type": "image", "source":
                            {"type": "base64", "media_type": "image/png", "data": result["image_b64"]}})
                    content.append({
                        "type": "tool_result",
                        "tool_use_id": tu_id,
                        "content": blocks_out,
                    })
                self._messages.append({"role": "user", "content": content})
        except Exception as e:
            self._emit(AgentEvent("error", f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1500]}"))
        finally:
            self._emit(AgentEvent("done"))

    def _anthropic_block_to_dict(self, b) -> dict:
        if hasattr(b, "model_dump"):
            return b.model_dump()
        return {"type": getattr(b, "type", "unknown")}

    def _call_claude(self):
        delays = [1, 3, 8]
        # On Opus 4.8 / 4.7 / 4.6 and Sonnet 4.6, let Claude reason with adaptive thinking
        # and a high effort budget — the recommended way to drive the newest models. Older
        # snapshots (e.g. Haiku 4.5) reject these params, so gate on model capability.
        kwargs = {}
        if models.supports_adaptive_thinking(self.active_model):
            kwargs["thinking"] = {"type": "adaptive"}
        if models.supports_effort(self.active_model):
            kwargs["output_config"] = {"effort": "high"}
        # We stream (below), so the SDK's non-streaming HTTP-timeout guard doesn't apply —
        # give adaptive thinking + the answer generous headroom.
        max_tokens = 32000 if kwargs else 4000
        # Prompt caching: the tool list (~20K tokens) + BASE_SYSTEM_PROMPT are byte-identical
        # on every call, so cache them once (breakpoint on the base block covers tools+base,
        # which render before it) and read them on every later step (~90% cheaper). The
        # volatile memory/recent-actions tail goes in a second, uncached system block.
        system = [
            {"type": "text", "text": BASE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ]
        extras = system_extras()
        if extras:
            system.append({"type": "text", "text": extras})
        for attempt in range(len(delays) + 1):
            streamed_any = False
            try:
                # Stream so the user sees text token-by-token (the UI already consumes
                # stream_chunk/stream_end and forwards them to the phone via update_stream).
                with self._client.messages.stream(
                    model=self.active_model,
                    max_tokens=max_tokens,
                    system=system,
                    tools=self._tools,
                    messages=self._messages,
                    **kwargs,
                ) as stream:
                    for delta in stream.text_stream:
                        if delta:
                            streamed_any = True
                            self._emit(AgentEvent("stream_chunk", delta))
                    final = stream.get_final_message()
                if streamed_any:
                    self._emit(AgentEvent("stream_end", None))
                return final
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                if streamed_any:
                    # Close out the partial bubble so a retry starts a fresh one.
                    self._emit(AgentEvent("stream_end", None))
                status = getattr(e, "status_code", None)
                if status in (429, 500, 502, 503, 504) and attempt < len(delays):
                    wait_s = delays[attempt]
                    self._emit(AgentEvent("message",
                        f"[Claude {status} - retrying in {wait_s}s ({attempt + 1}/{len(delays) + 1})]"))
                    time.sleep(wait_s)
                    continue
                raise

    def _handle_human_pause(self, args: dict) -> dict:
        pending = PendingHumanPause(
            reason=args.get("reason", "manual step required"),
            what_you_need=args.get("what_you_need", "complete the step then resume"),
        )
        self._emit(AgentEvent("human_pause", pending))
        note = pending.response.get()
        return {"ok": True, "resumed": True, "user_note": note or "(no note)"}
