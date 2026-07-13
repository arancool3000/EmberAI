# Control Ember over MCP (Model Context Protocol)

Ember is a complete **MCP server** for **ChatGPT**, Claude Desktop, Cursor, and other MCP clients.
It mirrors the live canonical registry, including plugin/runtime tools, so every available Ember
tool is discoverable: move the mouse/keyboard, read the
screen, run shell, manage files, control the browser, organise Gmail, and more — all executed
inside your **running** Ember session, with Ember's own safety rules applied.

**Every local Ember capability is free.** There is no Pro tool tier, plan gate, licence check,
or paid MCP feature. External services can still require their own credentials or subscription
(for example an email account, model API, VPN provider, or VirusTotal key).

```
ChatGPT ──Secure MCP Tunnel──► Streamable HTTP ┐
Claude / Cursor ─────stdio─────────────────────┴─► ember_mcp_server.py ─► Ember bridge ─► tools
```

There are two pieces, mirroring blender-mcp:

* **The bridge** — a small server *inside* Ember. Loopback-only (`127.0.0.1`), token-secured,
  off by default. This is where tools actually run.
* **`ember_mcp_server.py`** — a standalone MCP adapter supporting stdio, Streamable HTTP, and
  SSE. It preserves each tool's real schema and publishes ChatGPT impact annotations.

## 1. Turn on the bridge in Ember

Either:

* **Settings → “🔌 MCP bridge — let external MCP clients control Ember”**, then Save, **or**
* just ask Ember: **“start the MCP bridge”** (there are `start_mcp_bridge` / `stop_mcp_bridge` /
  `mcp_bridge_status` tools).

Ember prints the local URL (default `http://127.0.0.1:8770`) and writes the URL + a random
token to its support dir (`mcp_bridge.json`), which the MCP server reads automatically.

## 2. Install the stable MCP SDK

```bash
pip install 'mcp>=1.27,<2'
```

(Already covered if you installed Ember from `requirements.txt`.)

## 3. Connect ChatGPT

1. In Ember Settings, choose **Start ChatGPT MCP**. Ember starts the bridge and a loopback
   Streamable-HTTP endpoint at `http://127.0.0.1:8781/mcp`.
2. In ChatGPT developer mode, create an app using **Secure MCP Tunnel** and use that local URL.
3. Refresh the app's metadata after Ember updates so ChatGPT sees new tools and descriptions.

The endpoint remains loopback-only. Ember refuses `0.0.0.0` and other network binds; do not expose
computer-control tools directly to the internet. See OpenAI's official
[connection guide](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt).

Manual launch:

```bash
python3 ember_mcp_server.py --transport streamable-http --port 8781
```

## 4. Other MCP clients

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
python3 ember_mcp_server.py --doctor
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

Every tool includes `readOnlyHint`, `openWorldHint`, and `destructiveHint` so ChatGPT can present
the right impact and approval experience. All tools are listed; blocking a high-risk invocation is
a safety policy, not a plan/paywall restriction.
