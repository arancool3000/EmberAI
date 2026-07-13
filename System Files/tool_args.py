"""Tool-call argument hygiene.

Models routinely send tool arguments with the wrong primitive type — "100" instead
of 100, "true" instead of True, 3.0 instead of 3 — which made well-formed calls fail
with "bad args: ... got str". This coerces each argument to the type its tool
declaration says it should be, before dispatch, so those calls just work.

Pure + stdlib so it can be unit tested without importing the (heavy) agent module.
"""
from __future__ import annotations

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
