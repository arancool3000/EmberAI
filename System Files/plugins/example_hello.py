"""Example Ember plugin — a friendly hello.

Copy this file as a starting point for your own plugin. See README.md (same folder)
for the full EMBER_TOOLS contract.
"""


def hello_plugin(who: str = "world") -> dict:
    """Return a friendly greeting. Read-only and side-effect free."""
    return {"ok": True, "message": f"Hello, {who}, from an Ember plugin!"}


EMBER_TOOLS = [
    {
        "name": "hello_plugin",
        "description": "Say hello from a plugin. Returns a friendly greeting message.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "who": {"type": "STRING", "description": "who to greet (default 'world')"},
            },
            "required": [],
        },
        "handler": hello_plugin,
        "read_only": True,
    },
]
