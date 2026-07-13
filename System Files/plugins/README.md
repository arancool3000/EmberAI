# Writing an Ember plugin

Drop a `.py` file in this `plugins/` folder and its tools auto-register — no core
edits. The next time Ember starts (or when you call the `reload_plugins` tool) your
tools appear to the agent like any built-in tool.

## The contract

Your module must define a **module-level list named `EMBER_TOOLS`**. Each entry is a
dict describing one tool:

```python
def hello_plugin(who="world"):                     # the handler
    return {"ok": True, "message": f"Hello, {who}!"}

EMBER_TOOLS = [
    {
        "name": "hello_plugin",                    # globally-unique snake_case tool name
        "description": "Say hello from a plugin.", # one line shown to the agent
        "parameters": {                            # JSON-schema-ish, UPPERCASE types
            "type": "OBJECT",
            "properties": {
                "who": {"type": "STRING", "description": "who to greet"},
            },
            "required": [],                        # list of required property names
        },
        "handler": hello_plugin,                   # callable; receives args as kwargs
        "read_only": True,                         # optional, default False
    },
]
```

### Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Globally-unique `snake_case` name. Duplicate names (across plugins) are skipped — first wins. |
| `description` | yes | Short, clear sentence. The agent uses this to decide when to call your tool. |
| `parameters` | yes | A schema object. `type` is `"OBJECT"`; each property's `type` is uppercase (`STRING`, `INTEGER`, `NUMBER`, `BOOLEAN`, `ARRAY`, `OBJECT`). `required` is a list of property names. Use `{"type": "OBJECT", "properties": {}, "required": []}` for a no-arg tool. |
| `handler` | yes | The function. Called as `handler(**args)` — its parameter names must match the property names. |
| `read_only` | no | `True` = safe / read-only (no confirmation). Omitted/`False` = classified medium-risk by the safety layer. |

### Handler signature & return value

- Called with keyword arguments matching the parameter names, e.g.
  `hello_plugin(who="Ada")`.
- Give parameters sensible defaults so missing/optional args don't raise.
- **Always return a dict** in the Ember convention:
  - success: `{"ok": True, ...your data...}`
  - failure: `{"ok": False, "error": "what went wrong"}`
- Don't raise for expected problems — return `{"ok": False, "error": ...}`. (If you do
  raise, the host catches it and reports an error, but a clean error dict is better UX.)

### The `read_only` flag

Set `read_only: True` for tools that only read/compute and have no side effects — they
run without a confirmation prompt and are allowed even in read-only mode. Anything that
writes files, sends data, or changes the system should leave it off (default), so the
safety layer classifies it as medium-risk.

## Loading / reloading

- A broken plugin (import error, missing `EMBER_TOOLS`, invalid tool entry) is **skipped**
  and recorded under `errors` — it never crashes the loader or the rest of your plugins.
- After adding or editing a plugin file, call the **`reload_plugins`** tool, or restart
  Ember, to register the changes.
- Files named `__init__.py` or starting with `_` are ignored (use `_` to disable a plugin
  temporarily).

## Quick start

Ask Ember to run `create_plugin_template` with a name — it writes a minimal valid plugin
you can edit. Or copy `example_hello.py` in this folder.

## Management tools

- `list_plugins` — show installed plugins and their tools (read-only).
- `reload_plugins` — re-scan this folder and reload all tools.
- `create_plugin_template` — scaffold a new starter plugin file.
