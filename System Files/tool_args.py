"""Tool-call argument hygiene.

Models routinely send tool arguments with the wrong primitive type — "100" instead
of 100, "true" instead of True, 3.0 instead of 3 — which made well-formed calls fail
with "bad args: ... got str". This coerces each argument to the type its tool
declaration says it should be, before dispatch, so those calls just work.

Pure + stdlib so it can be unit tested without importing the (heavy) agent module.
"""
from __future__ import annotations

import inspect

_TRUE = {"true", "1", "yes", "on", "y", "t"}
_FALSE = {"false", "0", "no", "off", "n", "f", ""}


def build_param_types(declarations) -> dict:
    """From a list of tool declarations, build {tool_name: {param: TYPE}}.

    TYPE is the upper-cased Gemini schema type ("INTEGER","NUMBER","BOOLEAN","STRING",
    "ARRAY","OBJECT")."""
    out: dict[str, dict] = {}
    for d in declarations or []:
        name = d.get("name")
        if not name:
            continue
        props = ((d.get("parameters") or {}).get("properties") or {})
        types_ = {}
        for pname, spec in props.items():
            t = (spec or {}).get("type")
            if isinstance(t, str):
                types_[pname] = t.upper()
        out[name] = types_
    return out


def _to_int(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if s.lstrip("+-").isdigit():
            return int(s)
        try:
            f = float(s)
            if f.is_integer():
                return int(f)
        except ValueError:
            pass
    return v


def _to_number(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return v
    return v


def _to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return v


def coerce(param_types: dict, args: dict) -> dict:
    """Return a copy of `args` with each value coerced to the declared type.
    Unknown/extra keys are left untouched (some tools legitimately accept extras)."""
    if not isinstance(args, dict) or not param_types:
        return args
    out = dict(args)
    for k, v in list(out.items()):
        t = param_types.get(k)
        if t is None or v is None:
            continue
        try:
            if t == "INTEGER":
                out[k] = _to_int(v)
            elif t == "NUMBER":
                out[k] = _to_number(v)
            elif t == "BOOLEAN":
                out[k] = _to_bool(v)
            elif t == "STRING" and not isinstance(v, (str, list, dict)):
                out[k] = str(v)
        except Exception:
            pass
    return out


def validate_call(fn, args: dict, tool_name: str = "tool") -> dict | None:
    """Return a useful argument error before Python raises a vague ``TypeError``.

    Tool declarations and implementations can drift, and models sometimes invent plausible
    options. A machine-readable response lets the model repair the call immediately instead of
    repeating it. Callables that intentionally accept ``**kwargs`` remain unrestricted.
    """
    if not callable(fn) or not isinstance(args, dict):
        return None
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return None
    accepted = [
        name for name, p in parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    unexpected = sorted(str(name) for name in args if name not in accepted)
    missing = sorted(
        name for name, p in parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and p.default is inspect.Parameter.empty and name not in args
    )
    if not unexpected and not missing:
        return None
    problems = []
    if unexpected:
        problems.append("unexpected " + ", ".join(repr(name) for name in unexpected))
    if missing:
        problems.append("missing required " + ", ".join(repr(name) for name in missing))
    accepted_text = ", ".join(accepted) if accepted else "no arguments"
    return {
        "ok": False,
        "error": f"invalid arguments for {tool_name}: {'; '.join(problems)}. Accepted: {accepted_text}",
        "error_code": "invalid_arguments",
        "unexpected_args": unexpected,
        "missing_args": missing,
        "accepted_args": accepted,
        "retryable": True,
        "hint": "Correct the arguments before retrying; do not repeat the identical call.",
    }
