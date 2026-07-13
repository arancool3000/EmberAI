"""Tests for tool-call argument coercion (tool_args.py)."""
import tool_args

_DECLS = [
    {"name": "click", "parameters": {"properties": {
        "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
        "button": {"type": "STRING"}, "double": {"type": "BOOLEAN"}}}},
    {"name": "scroll", "parameters": {"properties": {
        "amount": {"type": "INTEGER"}, "factor": {"type": "NUMBER"}}}},
    {"name": "noparams", "parameters": {"properties": {}}},
]
_PT = tool_args.build_param_types(_DECLS)


def test_build_param_types():
    assert _PT["click"]["x"] == "INTEGER"
    assert _PT["click"]["double"] == "BOOLEAN"
    assert _PT["noparams"] == {}


def test_int_coercion_from_string_and_float():
    out = tool_args.coerce(_PT["click"], {"x": "100", "y": 250.0})
    assert out["x"] == 100 and isinstance(out["x"], int)
    assert out["y"] == 250 and isinstance(out["y"], int)


def test_bool_coercion():
    out = tool_args.coerce(_PT["click"], {"double": "true"})
    assert out["double"] is True
    out = tool_args.coerce(_PT["click"], {"double": "no"})
    assert out["double"] is False


def test_number_coercion():
    out = tool_args.coerce(_PT["scroll"], {"factor": "1.5", "amount": "3"})
    assert out["factor"] == 1.5 and out["amount"] == 3


def test_unknown_keys_and_none_preserved():
    out = tool_args.coerce(_PT["click"], {"x": "5", "weird": "keep", "button": None})
    assert out["weird"] == "keep" and out["button"] is None and out["x"] == 5


def test_uncoercible_left_alone():
    out = tool_args.coerce(_PT["click"], {"x": "not-a-number"})
    assert out["x"] == "not-a-number"


def test_empty_schema_is_noop():
    assert tool_args.coerce({}, {"a": "1"}) == {"a": "1"}


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn(); print(f"PASS  {fn.__name__}"); passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
