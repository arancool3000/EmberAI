"""VPN location manager for Ember (bring-your-own WireGuard).

Important: Ember is **not** a VPN provider with its own servers. This manages
WireGuard configuration profiles *you* add — from a provider like Mullvad or
ProtonVPN, or your own server — and connects through the system's `wg-quick`.
Add one `.conf` per location, then connect / switch / disconnect.

Requires WireGuard tools installed (`brew install wireguard-tools`), and bringing
an interface up needs admin rights. Everything degrades gracefully and reports
clearly when WireGuard or privileges are missing — it never pretends to be
connected when it isn't.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_LOCK = threading.RLock()

# Suggested location labels to organize the configs you add (purely organizational).
SUGGESTED_LOCATIONS = [
    "us-newyork", "us-losangeles", "uk-london", "de-frankfurt", "nl-amsterdam",
    "fr-paris", "ch-zurich", "se-stockholm", "es-madrid", "it-milan",
    "jp-tokyo", "sg-singapore", "au-sydney", "ca-toronto", "br-saopaulo",
]


def _support_dir() -> Path:
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Ember"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _vpn_dir() -> Path:
    d = _support_dir() / "vpn"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _vpn_dir() / "locations.json"


def _load() -> dict:
    try:
        p = _index_path()
        if p.exists():
            return json.loads(p.read_text("utf-8"))
    except Exception:
        pass
    return {"locations": {}}


def _save(d: dict) -> None:
    try:
        _index_path().write_text(json.dumps(d, indent=2), "utf-8")
    except Exception:
        pass


def _which(n: str) -> str | None:
    p = shutil.which(n)
    if p:
        return p
    # Also look in common locations not always on a GUI app's PATH (Homebrew, the
    # WireGuard app's helpers, Nix, etc.) so we don't falsely report "not installed".
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/run/current-system/sw/bin",
              "/Applications/WireGuard.app/Contents/MacOS"):
        c = Path(d) / n
        if c.exists():
            return str(c)
    return None


def wireguard_available() -> bool:
    return bool(_which("wg-quick"))


def add_location(name: str, config_path: str) -> dict:
    """Register a WireGuard .conf under a location name (the file is copied into Ember)."""
    name = (name or "").strip().lower()
    if not name:
        return {"ok": False, "error": "a location name is required"}
    src = Path(config_path).expanduser()
    if not src.exists() or not src.is_file():
        return {"ok": False, "error": f"config not found: {config_path}"}
    dest = _vpn_dir() / f"{name}.conf"
    try:
        shutil.copy2(src, dest)
        os.chmod(dest, 0o600)
    except Exception as e:
        return {"ok": False, "error": f"could not import config: {e}"}
    with _LOCK:
        idx = _load()
        idx["locations"][name] = {"conf": str(dest)}
        _save(idx)
    return {"ok": True, "added": name}


def remove_location(name: str) -> dict:
    name = (name or "").strip().lower()
    with _LOCK:
        idx = _load()
        entry = idx["locations"].pop(name, None)
        _save(idx)
    if entry:
        try:
            Path(entry["conf"]).unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "removed": name}
    return {"ok": False, "error": f"no location named '{name}'"}


def list_locations() -> dict:
    idx = _load()
    return {
        "ok": True,
        "locations": sorted(idx.get("locations", {}).keys()),
        "suggested": SUGGESTED_LOCATIONS,
        "wireguard_installed": wireguard_available(),
        "note": ("Add your provider's WireGuard .conf per location with add_vpn_location. "
                 "Ember is not a VPN provider; it connects through configs you supply. "
                 "No Homebrew needed — the free WireGuard app from the Mac App Store works too."),
    }


def _active_interfaces() -> list[str]:
    """Names of currently-up WireGuard interfaces (empty if none / wg missing)."""
    wg = _which("wg")
    if not wg:
        return []
    try:
        r = subprocess.run([wg, "show", "interfaces"], capture_output=True, text=True, timeout=8)
        return (r.stdout or "").split()
    except Exception:
        return []


def status() -> dict:
    """Report whether a WireGuard tunnel is up and the current public IP."""
    ifaces = _active_interfaces()
    pub = None
    try:
        import requests
        pub = requests.get("https://api.ipify.org", timeout=6).text.strip()
    except Exception:
        pub = None
    return {
        "ok": True,
        "connected": bool(ifaces),
        "active_interfaces": ifaces,
        "public_ip": pub,
        "wireguard_installed": wireguard_available(),
    }


def connect(name: str) -> dict:
    """Bring up the WireGuard tunnel for a saved location (needs wg-quick + admin rights)."""
    name = (name or "").strip().lower()
    wgq = _which("wg-quick")
    if not wgq:
        return {"ok": False, "error": "WireGuard isn't installed. Get the free WireGuard app from "
                "the Mac App Store (no terminal/Homebrew needed), then add your provider's .conf."}
    idx = _load()
    entry = idx.get("locations", {}).get(name)
    if not entry:
        return {"ok": False, "error": f"no location named '{name}' — add it with add_vpn_location"}
    conf = entry["conf"]
    try:
        r = subprocess.run([wgq, "up", conf], capture_output=True, text=True, timeout=40)
        if r.returncode == 0:
            return {"ok": True, "connected": name, "status": status()}
        err = (r.stderr or r.stdout or "").strip()
        if "permission" in err.lower() or "must be run as root" in err.lower() or "operation not permitted" in err.lower():
            return {"ok": False, "error": "VPN connect needs admin rights — run with sudo, "
                    f"e.g. `sudo wg-quick up {conf}`.", "detail": err[:300]}
        return {"ok": False, "error": err[:300] or "wg-quick failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def disconnect(name: str | None = None) -> dict:
    """Tear down a tunnel (or all active tunnels if no name given)."""
    wgq = _which("wg-quick")
    if not wgq:
        return {"ok": False, "error": "WireGuard not installed."}
    targets = []
    if name:
        entry = _load().get("locations", {}).get((name or "").strip().lower())
        targets = [entry["conf"]] if entry else [name]
    else:
        targets = _active_interfaces()
    if not targets:
        return {"ok": True, "disconnected": [], "note": "no active tunnel"}
    done = []
    for t in targets:
        try:
            r = subprocess.run([wgq, "down", t], capture_output=True, text=True, timeout=40)
            if r.returncode == 0:
                done.append(t)
        except Exception:
            pass
    return {"ok": True, "disconnected": done, "status": status()}
