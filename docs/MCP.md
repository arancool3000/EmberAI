# Control Ember over MCP (Model Context Protocol)

Ember can act as an **MCP server** — the same way the Blender MCP server lets an AI control
Blender. Turn on Ember's MCP bridge and an MCP client such as **Claude Desktop**, **Cursor**,
or any other MCP-capable app can drive Ember's ~290 tools: move the mouse/keyboard, read the
screen, run shell, manage files, control the browser, organise Gmail, and more — all executed
inside your **running** Ember session, with Ember's own safety rules applied.

```
Claude Desktop / Cursor  ──stdio──►  ember_mcp_server.py  ──HTTP (loopback)──►  Ember (bridge)  ──►  tools
```

There are two pieces, mirroring blender-mcp:

* **The bridge** — a small server *inside* Ember. Loopback-only (`127.0.0.1`), token-secured,
  off by default. This is where tools actually run.
* **`ember_mcp_server.py`** — a standalone script your MCP client launches. It speaks MCP over
  stdio and forwards every call to the bridge. It needs no Ember imports and no API keys.

## 1. Turn on the bridge in Ember

Either:

* **Settings → “🔌 MCP bridge — let external MCP clients control Ember”**, then Save, **or**
* just ask Ember: **“start the MCP bridge”** (there are `start_mcp_bridge` / `stop_mcp_bridge` /
  `mcp_bridge_status` tools).

Ember prints the local URL (default `http://127.0.0.1:8770`) and writes the URL + a random
token to its support dir (`mcp_bridge.json`), which the MCP server reads automatically.

## 2. Install the MCP SDK

```bash
pip install mcp
```

(Already covered if you installed Ember from `requirements.txt`.)

## 3. Point your MCP client at Ember

**Claude Desktop** — add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ember": {
      "command": "python3",
      "args": ["/absolute/path/to/EmberAI/ember_mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. Ember's tools appear in the MCP tool list. Try: *“Take a screenshot and
tell me what's on screen,”* or *“Open a terminal and show disk usage.”*

**Cursor / other clients** — use the same command/args in that client's MCP config.

Check what's exposed without a client:

```bash
python3 ember_mcp_server.py --list
```

## Running the MCP server on a different machine

The bridge is loopback-only by design. To reach it from another host, SSH-forward the port and
pass the bridge URL + token explicitly:

```bash
ssh -L 8770:127.0.0.1:8770 you@ember-host
EMBER_BRIDGE_URL=http://127.0.0.1:8770 EMBER_BRIDGE_TOKEN=<token> python3 ember_mcp_server.py
```

(`--url` / `--token` flags work too.)

## Security

The bridge can run shell commands, so it is locked down:

* **Loopback only.** It binds `127.0.0.1` and refuses non-local peers. It is never bound to
  `0.0.0.0` and never exposed through the Ember Link tunnel.
* **Token required.** Every request needs the random bearer token written to `mcp_bridge.json`
  (file mode `600`).
* **Ember's capability mode is enforced.** If Ember is in *read-only* or *restricted* mode, the
  bridge honours it — MCP calls can't exceed what the app itself allows.
* **High-risk tools are blocked by default.** Actions that would pop a confirmation in the app
  (sending email, dangerous shell, typing sensitive text…) are refused over MCP, because there's
  no human at the MCP layer to approve them. Turn on **“Allow high-risk tools over MCP”** in
  Settings only if you fully trust the client.
* **Off by default.** Nothing listens until you enable the bridge.

Every bridge call is written to Ember's audit log, same as in-app tool use.
