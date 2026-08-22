# Control Ember over MCP (Model Context Protocol)

Ember is a complete **MCP server** for **ChatGPT**, **Claude Desktop / Claude Code**, Cursor,
and other MCP clients.
It mirrors the live canonical registry, including plugin/runtime tools, so every available Ember
tool is discoverable: move the mouse/keyboard, read the
screen, run shell, manage files, control the browser, organise Gmail, and more — all executed
inside your **running** Ember session, with Ember's own safety rules applied.

**Every local Ember capability is free.** There is no Pro tool tier, plan gate, licence check,
or paid MCP feature. External services can still require their own credentials or subscription
(for example an email account, model API, VPN provider, or VirusTotal key).

Ember's **MCP live-chat mode does not require a model API key**. ChatGPT or Claude remains the
model host and voluntarily calls Ember's MCP tools. This uses whatever access you already have in
that client; MCP does not bypass its plan limits, permissions, tool-call limits, or terms.

```
ChatGPT ──Secure MCP Tunnel──► Streamable HTTP ┐
Claude / Cursor ─────stdio─────────────────────┴─► ember_mcp_server.py ─► Ember bridge ─► tools + live chat
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

## 4. Connect Claude (the same capabilities)

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

The Settings button writes this file and starts Ember's bridge for you. Claude receives the same
live-chat and computer-control tools as ChatGPT; there is no reduced Claude tool list.

**Claude Code** — register the same stdio server at user scope:

```bash
claude mcp add ember --scope user -- python3 "/absolute/path/to/EmberAI/System Files/ember_mcp_server.py"
claude mcp get ember
```

Anthropic documents local stdio servers and the `claude mcp` commands in its official
[MCP guide](https://docs.anthropic.com/en/docs/claude-code/mcp).

## 5. Live chat: send Ember messages to ChatGPT or Claude

After connecting the MCP server, paste this once into the ChatGPT/Claude conversation you want
to use as Ember's model:

> Connect to Ember live chat. Call `ember_live_connect` with your client/model name. Then call
> `ember_live_wait` and wait for messages from the Ember UI. For every message, publish useful
> `thinking`, `working`, or `using_tool` status with `ember_live_set_status`; use Ember's screen,
> mouse, keyboard, browser, file, or shell tools as needed; return the answer with
> `ember_live_reply`; then call `ember_live_wait` again using the latest cursor. Continue until
> Ember sends cancel/disconnect.

When it connects, the **MCP** chip beside Ember's composer turns on and shows the client name.
Messages typed into Ember now go to that MCP conversation and its status/reply appears directly
in the Ember UI. Uncheck the chip or type `/local` to use Ember's configured local/API model;
type `/mcp` to switch back.

| Tool | Purpose |
|---|---|
| `ember_live_connect` | Attach this ChatGPT/Claude conversation and get a session ID. |
| `ember_live_wait` | Long-poll for an Ember message or cancellation; repeat after timeouts. |
| `ember_live_set_status` | Show `thinking`, `working`, `using_tool`, `idle`, `done`, or `error`. |
| `ember_live_reply` | Stream or deliver the final response into Ember's chat. |
| `ember_live_session` | Inspect connection and pending-message state. |
| `ember_live_disconnect` | End the live session cleanly. |

MCP clients impose tool-call time limits, so `ember_live_wait` uses bounded long polling (up to
45 seconds) and returns `call_again: true` on a quiet timeout. The client must keep calling it.
No desktop AI client is guaranteed to run an infinite background turn; if it stops waiting, ask
it to resume the Ember live-chat loop.

## 6. Desktop control and the independent Ember pointer

Both clients receive Ember's complete live registry, including `take_screenshot`,
`read_screen_text`, `smart_click`, `click`, `move_mouse`, `drag`, `type_text`, `press_key`,
browser tools, files, and shell. Enable **Detached** pointer mode in Ember to show the smooth
agent pointer without stealing focus into the Ember window. The pointer overlay is click-through;
the actual target application receives the click.

For reliable UI work, clients should inspect before acting and prefer labeled controls:

1. `take_screenshot` or `read_screen_text`
2. `smart_click("visible label")` for labeled targets
3. `click(x, y)` only for unlabeled coordinates
4. inspect again to verify the visible result

## 7. Other MCP clients

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
