# Ember — Security Model

Ember is an AI agent that can see your screen and control your mouse, keyboard, shell,
files, and browser. That power is the product, and it's also the thing to protect. This
document is an honest description of the trust boundaries, what's hardened, and what isn't.

**No software can be made "impossible to hack."** What we can do — and do — is keep the
attack surface small, require real authentication on every remote entry point, apply a
safety/confirmation layer to dangerous actions, and fail closed. This page tells you where
those controls are so you can reason about your own risk.

## Trust boundaries (where an attacker could try to get in)

| Surface | Exposure | Control |
|---|---|---|
| **Ember Link** (phone remote, port 8765) | LAN, and the public internet when the tunnel is on | 6-digit PIN (LAN-only) + 32-byte pairing token; brute-force lockout; **raw remote shell is LAN-only** |
| **MCP bridge** (`ember_bridge.py`) | Loopback only (127.0.0.1) | Off by default; random token; Host-header check (anti DNS-rebind); capability mode enforced; high-risk tools blocked |
| **Local model (Ollama)** | Loopback only (127.0.0.1:11434) | Hardcoded to localhost; Ember never pulls models/manifests, so the Ollama pull CVEs aren't reachable through Ember |
| **Cloud model providers** | Outbound HTTPS to your chosen provider | Your API key only goes to that provider's host; keys stored in the encrypted vault |
| **Auto-updater** | Outbound HTTPS to GitHub | TLS verified via certifi; **download host pinned to github.com**; SHA-256 checked when the manifest provides it |

## Ember Link (the biggest remote surface)

Ember Link mirrors your screen to a phone and injects input. It's the main way a *network*
attacker could try to reach you, so it's the most locked-down:

- **The PIN never leaves your LAN.** The 6-digit PIN is only accepted from genuine private
  LAN addresses; tunnel-relayed requests arrive via loopback and are treated as non-LAN, so
  the PIN can't authorize them (`remote_server._is_lan_ip`). Constant-time comparison; 5
  failures → 2-minute lockout.
- **Remote access needs a 32-byte pairing token**, minted only after a successful LAN pairing.
  It's unguessable; it is *not* rate-limited on purpose (so an attacker can't lock out your
  real devices). Treat any "magic link" that contains a token as a root credential — anyone
  who gets that link gets control.
- **Arbitrary remote shell is disabled over the internet.** The phone's "run a command" box
  (`macro_cmd`) now runs **only** from a device on the same Wi-Fi. Over the tunnel it's
  refused, so a stolen roaming token can move the mouse and type but **cannot run shell
  commands** on your machine. (Mouse/keyboard/chat still work remotely.)
- Remote chat drives the agent, which still applies the full safety/confirmation layer below
  — high-risk actions requested remotely surface a confirmation on the desktop.
- Request bodies are size-capped; the server auto-stops after 30 minutes idle.

## The safety / capability layer (applies to every backend)

Every tool call — from the Gemini, Claude, OpenAI, or Ollama backend, and from the MCP
bridge — passes through `safety.py`:

- **Risk classification** (`classify`) tags each action low/medium/high.
- **Capability mode** (`mode_allows`): you can put Ember in `read_only` or `restricted` mode;
  those caps are enforced everywhere, including over MCP.
- **Confirmation**: high-risk actions (dangerous shell, sending email, typing secrets) require
  human approval in the app. Over the MCP bridge, high-risk actions are **blocked** unless you
  explicitly opt in, because there's no human at that layer to approve them.
- Every tool call is written to an append-only **audit log**, and secrets are scrubbed from
  logs/screenshots by `redaction.py`.

## Secrets

API keys are stored in the OS keychain when available, otherwise in a Fernet-encrypted vault
(`key_vault.py`) — never in plaintext `settings.json` when the vault is on. Tools only ever see
masked previews of keys. If you set a custom OpenAI-compatible `base_url`, your key is sent to
that host — only point it at endpoints you trust.

## "Bleeding llama" / local-LLM concerns

Ember talks to Ollama on `127.0.0.1:11434` only, and never triggers model pulls or manifest
downloads, so the known Ollama/llama.cpp remote-code-execution CVEs (Probllama path traversal
on `/api/pull`, and the `llama-cpp-python` Jinja SSTI) are not reachable through Ember. Model
output is parsed as JSON for tool calls — it is never `eval`'d, shelled, or used as a filesystem
path. Keep your local Ollama bound to loopback (its default) and don't expose it.

## Known residual risks (honest list)

- A holder of a valid pairing token can still drive input and chat remotely (by design — that's
  the feature). Revoke tokens by re-pairing / clearing them if a device is lost.
- The auto-updater pins the download host (github.com), checks the SHA-256, and can now verify
  an **Ed25519 signature** on the manifest (`update_signing.py`): once the maintainer runs
  `python sign_release.py keygen`, commits `update_pubkey.pem`, and signs each `latest.json`,
  Ember refuses any update whose manifest isn't validly signed — closing the compromised-channel
  gap. Until a public key is bundled the check is inert (no behaviour change). Note this is
  *update authenticity*, which is distinct from **OS code-signing**: making macOS/Windows stop
  warning about an "unidentified developer" requires a paid Apple/Microsoft developer certificate
  and notarization, which is an account/credential step, not a code change.
- The encrypted-file key vault stores its key next to the ciphertext, so a local attacker with
  read access to your user directory can recover keys. The OS-keychain backend does not have
  this weakness — prefer it where available.

## Reporting a vulnerability

Please report security issues privately to the maintainer (see the repository owner) rather
than opening a public issue. Include steps to reproduce and the affected version.
