"""Phone intake — receive a photo from an iPhone and encrypt it before anything syncs.

This is the desktop half of closing the phone gap. iCloud Photos gives no hook for encrypting
a photo before Apple receives it, so the only workable shape is to stop using iCloud Photos for
what you care about and send those photos here instead: over Ember Link, either by picking them
in the phone's browser or from an iOS Shortcuts automation.

Two properties worth stating plainly:

* **The plaintext never touches this machine's disk.** The upload is held in memory and
  encrypted straight to the destination via ``data_protect.encrypt_bytes``. There is no
  plaintext temp file to shred, race against, or leave behind after a crash.
* **The destination is a normal protected file**, readable by the passphrase and every enrolled
  device, exactly like anything else Ember protects. Point it at iCloud Drive and only the
  ciphertext ever leaves the machine.

What this does NOT do: it cannot intercept iCloud Photos. If iCloud Photos is on, the phone has
already uploaded the original before Ember sees anything. This is a replacement for that path,
not a filter in front of it — ``advice()`` says so, and the setup instructions lead with it.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import data_protect

#: Uploads are held in memory, so this is a real memory ceiling per request, deliberately well
#: below data_protect.MAX_FILE_BYTES. Photos are 3-12 MB; this leaves room for video without
#: letting one request balloon the process.
MAX_UPLOAD_BYTES = 128 * 1024 * 1024

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _data_dir() -> Path:
    from app_data import data_dir
    return data_dir()


#: Module-level so tests can point it at a temp dir.
CONFIG_FILE = _data_dir() / "phone_intake.json"


# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------
def load_config() -> dict:
    try:
        p = Path(CONFIG_FILE)
        if p.exists():
            c = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(c, dict):
                return c
    except Exception:
        pass
    return {}


def save_config(cfg: dict) -> None:
    p = Path(CONFIG_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _default_dest() -> Path:
    """Prefer a real sync folder, so the default already does the useful thing."""
    for t in data_protect.icloud_targets():
        if t["exists"] and t["syncable"]:
            return Path(t["path"]) / "Ember Photos"
    return Path.home() / "Ember Photos"


def dest_dir() -> Path:
    cfg = load_config()
    return Path(cfg["dest"]).expanduser() if cfg.get("dest") else _default_dest()


def set_dest(path: str) -> dict:
    """Choose where protected uploads land."""
    p = Path(str(path)).expanduser()
    if p.exists() and not p.is_dir():
        return {"ok": False, "error": f"not a folder: {p}"}
    cfg = load_config()
    cfg["dest"] = str(p)
    save_config(cfg)
    return {"ok": True, "dest": str(p)}


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------
def safe_name(name: str) -> str:
    """Reduce an uploaded filename to something that cannot escape the destination folder.

    Anything a phone or a Shortcut sends is untrusted: "../../.ssh/authorized_keys" must become
    a plain filename in the destination, not a path. Everything outside a conservative set is
    replaced, and the result is finally passed through Path().name.
    """
    raw = str(name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]                       # drop any directory part
    raw = _SAFE_NAME.sub("_", raw).strip("._") or ""
    raw = Path(raw).name                            # belt and braces
    if not raw or raw in {".", ".."}:
        raw = time.strftime("upload-%Y%m%d-%H%M%S")
    return raw[:120]


def _unique(dest: Path) -> Path:
    """Never overwrite an existing protected file — a phone re-sending IMG_0001.jpg must not
    destroy the first one."""
    if not dest.exists():
        return dest
    stem, suffix = dest.name[:-len(data_protect.EXT)], data_protect.EXT
    for i in range(1, 1000):
        cand = dest.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
    return dest.with_name(f"{stem}-{int(time.time())}{suffix}")


def receive(filename: str, data: bytes) -> dict:
    """Encrypt an uploaded file straight into the destination. Never raises."""
    try:
        if not data:
            return {"ok": False, "error": "empty upload"}
        if len(data) > MAX_UPLOAD_BYTES:
            return {"ok": False, "error": f"upload exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB"}
        pw = data_protect._passphrase()
        if pw is None:
            return {"ok": False, "error": "data protection is not set up on the computer"}
        out_dir = dest_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        name = safe_name(filename)
        dest = _unique(out_dir / (name + data_protect.EXT))
        data_protect.encrypt_bytes(data, dest, pw, data_protect.load_recipients())
        return {"ok": True, "protected": str(dest), "name": name, "bytes": len(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Setup guidance
# ---------------------------------------------------------------------------
def advice() -> list[str]:
    """The honest preconditions, in the order they matter."""
    return [
        "Turn OFF iCloud Photos on the iPhone (Settings ▸ your name ▸ iCloud ▸ Photos). While "
        "it is on, Apple already has the original before Ember ever sees it — nothing here can "
        "change that.",
        "Keep the destination inside iCloud Drive if you still want the photos backed up. Only "
        "the encrypted copy syncs.",
        "Protected files do not preview as photos. Use Ember to open them again.",
    ]


def shortcut_recipe(url: str, token: str) -> list[str]:
    """The iOS Shortcuts automation, step by step.

    Shortcuts has no 'photo taken' trigger, so the automation is time- or Wi-Fi-based and picks
    up whatever arrived since it last ran.
    """
    return [
        "On the iPhone, open Shortcuts ▸ Automation ▸ + ▸ Time of Day (or 'When I connect to' "
        "your home Wi-Fi). Choose 'Run Immediately' and turn off 'Notify When Run'.",
        "Add action: Find Photos — Where: Date Taken is today. (Add 'Limit' if you want to cap "
        "each run.)",
        "Add action: Repeat with Each — over the Find Photos result.",
        f"Inside the Repeat, add: Get Contents of URL — URL: {url}/api/upload , Method: POST, "
        "Request Body: File ▸ Repeat Item.",
        f"On that same action, add Headers: 'X-Ember-Token' = {token} , and "
        "'X-Ember-Filename' = the photo's Name.",
        "Optional, once you trust it: add Delete Photos on the Repeat Item so the original "
        "leaves the phone after it has been protected.",
    ]


# ---------------------------------------------------------------------------
# Tools (exposed to the LLM)
# ---------------------------------------------------------------------------
def adp_phone_status() -> dict:
    """Report where phone uploads land and whether Ember Link is reachable."""
    try:
        import remote_server
        st = remote_server.status()
        return {"ok": True, "dest": str(dest_dir()),
                "link_running": bool(st.get("running")),
                "lan_url": st.get("url", ""), "public_url": remote_server.remote_url(),
                "paired_devices": remote_server.paired_count(),
                "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                "protection_configured": data_protect.is_set_up(),
                "advice": advice()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def adp_phone_set_folder(folder: str) -> dict:
    """Choose where protected phone uploads are saved (e.g. a folder inside iCloud Drive)."""
    return set_dest(folder)


def adp_phone_setup(public: bool = False) -> dict:
    """Produce the exact iOS Shortcuts automation for sending photos to this computer.

    Issues a fresh pairing token for the phone. `public` turns on the tunnel so the automation
    also works off the home Wi-Fi.
    """
    try:
        import remote_server
        if not data_protect.is_set_up():
            return {"ok": False, "error": "turn on data protection first (adp_setup)"}
        st = remote_server.status()
        if not st.get("running"):
            return {"ok": False, "error": "Ember Link is not running — start it first "
                                          "(start_remote_control)"}
        url = ""
        if public:
            r = remote_server.enable_remote()
            url = r.get("url") or remote_server.remote_url()
            if not url:
                return {"ok": False, "error": f"could not open a public tunnel: "
                                              f"{r.get('error', 'unknown')}"}
        else:
            url = st.get("url", "")
        if not url:
            return {"ok": False, "error": "no reachable URL for Ember Link"}
        url = url.rstrip("/")
        token = remote_server.issue_pair_token()
        return {"ok": True, "url": url, "token": token, "dest": str(dest_dir()),
                "reachable": "anywhere (public tunnel)" if public else "home Wi-Fi only",
                "steps": shortcut_recipe(url, token),
                "advice": advice(),
                "note": ("This token grants full Ember Link access, not just uploads. Revoke it "
                         "with revoke_pairings if the phone is lost.")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOL_DECLARATIONS = [
    {
        "name": "adp_phone_status",
        "description": "Report where photos sent from a phone are saved, whether Ember Link is "
                       "running, and what still needs doing on the phone.",
        "parameters": {"type": "OBJECT", "properties": {}, "required": []},
    },
    {
        "name": "adp_phone_set_folder",
        "description": "Choose the folder where protected photo uploads from a phone are saved, "
                       "e.g. a folder inside iCloud Drive.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "destination folder"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_phone_setup",
        "description": "Give the user the exact iOS Shortcuts automation that sends their new "
                       "photos to this computer to be encrypted. Issues a pairing token.",
        "parameters": {"type": "OBJECT", "properties": {
            "public": {"type": "BOOLEAN", "description": "also open a public tunnel so it works away from home Wi-Fi (default false)"}},
            "required": []},
    },
]

TOOL_DISPATCH = {
    "adp_phone_status": adp_phone_status,
    "adp_phone_set_folder": adp_phone_set_folder,
    "adp_phone_setup": adp_phone_setup,
}

READONLY_TOOLS = {"adp_phone_status"}
INTERACTION_TOOLS = {"adp_phone_set_folder", "adp_phone_setup"}
