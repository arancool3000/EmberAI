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

## Advanced Data Protection (`data_protect.py`)

iCloud is encrypted in transit and at rest, but by default **Apple holds the keys** — it is not
end-to-end encrypted unless the user turns on Apple's own Advanced Data Protection (iOS 16.2+).

**And that setting can be taken away.** In February 2025 Apple withdrew ADP from the United
Kingdom rather than comply with a secret Technical Capability Notice served by the Home Office
under the Investigatory Powers Act 2016 — a notice Apple was legally gagged from disclosing.
A first, worldwide-scoped notice was dropped in August 2025; a second, narrowed to UK users,
followed that October, and the challenges brought by Apple, Privacy International and Liberty
are before the Investigatory Powers Tribunal. UK users still cannot enable ADP. `adp_status`
reports this for the machine's region, because for those users Ember's encryption is not a
second layer — it is the only end-to-end layer they have.

This is the design argument for doing it locally: a cloud provider's strongest setting is
subject to jurisdiction and can be revoked without the user being told, whereas a key that
never leaves the user's machine cannot be served with a notice.

Ember adds a provider-independent layer *underneath* that: files of any type are encrypted on
your own machine before they ever land in a synced folder. What reaches Apple (or
Dropbox/OneDrive/Google Drive) is an opaque blob.

**Envelope encryption, so every device and person you choose can read it.** Each file gets its
own random content key (CEK), and the CEK is wrapped separately for each recipient:

- **the passphrase** — always present, so the data survives losing every device;
- **each enrolled device or person** — an X25519 public key added with `adp_add_recipient`.

Enrolling a device means exchanging public keys, not sharing the passphrase. The recipient list
holds public keys only, so it is safe to sync through iCloud itself, and any enrolled device can
both encrypt (for everyone) and decrypt. `adp_grant_access` re-wraps files already protected so
a newly-added device can read the back catalogue — the header is rewritten, the ciphertext body
is not.

- Payload: Fernet (AES-128-CBC + HMAC-SHA256) under the per-file CEK. Passphrase wrap:
  PBKDF2-HMAC-SHA256, 600,000 iterations, fresh 16-byte salt per file. Recipient wrap: X25519
  ECDH to an ephemeral key, HKDF-SHA256 bound to *both* public keys so a wrap cannot be replayed
  at a different recipient. Layout: `EMBERADP2 | header_len | header JSON | token`, extension
  `.ember`. Files in the earlier passphrase-only `EMBERADP1` format still decrypt, and
  `adp_grant_access` upgrades them.
- The header is not itself authenticated, but the payload is: tampering with it can only cause
  unwrapping to fail or yield a wrong CEK, which the body's HMAC then rejects. No forged
  plaintext can result.
- The passphrase and each device's private key live in `key_vault` (OS keychain where
  available). Neither is ever uploaded, written into an encrypted file, or returned by a tool.
- Writes go to a `.part` sibling then `os.replace`, so an interrupted run can't leave a
  truncated "protected" file whose original you then delete.
- Ember's own key material (`vault.enc`, `vault.key`, the recipient list) is never encrypted —
  sealing the vault with a key stored inside it would be unrecoverable.
- Shredding unencrypted originals is opt-in (`delete_original(s)`), reuses the existing file
  shredder, and is **high** risk. So is `adp_add_recipient` / `adp_grant_access`: handing
  another party the ability to decrypt deserves a prompt of its own.

Honest limits: this does **not** encrypt an existing iCloud Photos *library* — iCloud Photos
only syncs real images, so encrypted blobs can't live there. Export photos out of Photos,
protect them into iCloud Drive, and delete the originals yourself. Anything already uploaded
stays uploaded. Removing a recipient stops them receiving *new* files; it cannot un-see what
they could already decrypt, and `adp_remove_recipient` says so in its own result. And there is
**no recovery**: lose the passphrase *and* every enrolled device and the data is gone, to you
and to Ember alike. That is the property that makes the guarantee real.

### Closing the phone gap

Ember runs on macOS and Windows; photos taken on an iPhone sync to iCloud from the phone, and
**Apple exposes no hook for encrypting them first**. iOS Shortcuts has no "photo taken" trigger
either. So there is no way to filter that path — the only workable shape is to stop using it.
Three pieces support that:

- **`adp_watch.py`** — watch a folder and encrypt whatever lands in it. Turn iCloud Photos off,
  import from the phone into a local folder, point the watcher at it with the destination set to
  iCloud Drive. A file is only encrypted once its size and mtime stop changing *and* it is past
  `min_age`; encrypting a photo mid-copy would otherwise produce a valid encryption of half a
  JPEG, which looks like success. Shredding originals is opt-in and only ever runs after the
  encrypted file exists.
- **`/api/upload` in `remote_server.py`** — send photos from the phone over Ember Link, either
  from the Photos tab in the web app or from a Shortcuts automation. Credentials travel in
  headers (`X-Ember-Token`, or `X-Ember-Pin` on the LAN) rather than the query string, keeping
  the token out of tunnel and proxy logs. Uploads have their own `_MAX_UPLOAD_BYTES` ceiling,
  deliberately separate from the 2 MB `_MAX_POST_BYTES` used by `/api/event` and `/api/chat`, so
  raising one never widens the other.
- **`phone_intake.py`** — encrypts the upload straight from memory via
  `data_protect.encrypt_bytes`. **The plaintext is never written to this machine's disk**, so
  there is no temp file to shred, race against, or leave behind after a crash. Uploaded
  filenames are untrusted and reduced to a single safe component, so `../../.ssh/authorized_keys`
  becomes a plain name inside the destination folder.

`adp_phone_setup` issues the pairing token and prints the Shortcut recipe. That token grants
**full Ember Link access, not upload-only** — it is the existing pairing credential, and the
tool result says so. Revoke it with `revoke_pairings` if the phone is lost.

The honest limit stays: while iCloud Photos is on, Apple has the original before Ember sees
anything. Every surface leads with that — the tool advice, the setup dialog, and the phone page
itself.

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
- A phone pairing token used for photo uploads is a full Ember Link credential: anyone holding
  it can also drive the screen and keyboard remotely. There is no upload-only scope today.
- Auto-protect with "shred originals" runs unattended. A destination that silently becomes
  unwritable (an unmounted drive, a full disk) surfaces as failures in `adp_watch_status` and
  the Security panel, and the shred is skipped — but nothing interrupts you to say so.
- The encrypted-file key vault stores its key next to the ciphertext, so a local attacker with
  read access to your user directory can recover keys. The OS-keychain backend does not have
  this weakness — prefer it where available.

## Reporting a vulnerability

Please report security issues privately to the maintainer (see the repository owner) rather
than opening a public issue. Include steps to reproduce and the affected version.
