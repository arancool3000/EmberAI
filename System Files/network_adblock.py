"""System-wide ad / tracker blocker for Ember.

Blocks ads for EVERY app on the machine (not just Ember's browser) by sinkholing ad,
tracker and telemetry DOMAINS in the OS hosts file — the same technique Pi-hole uses.
A blocked domain resolves to 0.0.0.0, so the request never leaves the machine and the ad
never comes back over Wi-Fi.

Safety / reversibility:
  * Every entry lives inside ONE clearly delimited block in the hosts file
    (`# >>> Ember Ad Blocker >>>` … `# <<<`). enable() rewrites exactly that block;
    disable() removes exactly that block. Nothing else in the hosts file is touched.
  * The original hosts file is backed up once before the first edit.
  * Writing the hosts file needs admin rights, so enable()/disable() go through a single
    privileged step (macOS: one osascript admin prompt that also flushes the DNS cache).
  * Hermetically testable: set $EMBER_HOSTS_FILE to a temp path and the module writes it
    directly (no privilege, no DNS flush) so tests never touch the real system.

User lists (blocked-extra + allowlist) + the on/off state persist in adblock.json.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_BEGIN = "# >>> Ember Ad Blocker >>>"
_END = "# <<< Ember Ad Blocker <<<"

# A solid built-in blocklist of the worst ad / tracker / telemetry domains. Extend at runtime
# with add_domain(), or pull a big public list (e.g. StevenBlack) via update_from_url().
_BUILTIN_BLOCKLIST = {
    # Google ads / analytics / tag
    "doubleclick.net", "googleadservices.com", "googlesyndication.com", "google-analytics.com",
    "googletagmanager.com", "googletagservices.com", "adservice.google.com", "pagead2.googlesyndication.com",
    "partner.googleadservices.com", "www.googleadservices.com", "analytics.google.com",
    # Facebook / Meta tracking
    "connect.facebook.net", "an.facebook.com", "pixel.facebook.com", "graph.facebook.com",
    # Amazon ads
    "amazon-adsystem.com", "aax.amazon-adsystem.com", "c.amazon-adsystem.com",
    # Big ad exchanges / SSPs / DSPs
    "adnxs.com", "adsrvr.org", "rubiconproject.com", "pubmatic.com", "openx.net", "casalemedia.com",
    "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "yieldmo.com", "33across.com",
    "sharethrough.com", "smartadserver.com", "spotxchange.com", "spotx.tv", "teads.tv",
    "moatads.com", "adform.net", "adroll.com", "media.net", "bidswitch.net", "contextweb.com",
    "districtm.io", "gumgum.com", "indexww.com", "lijit.com", "rfihub.com", "scorecardresearch.com",
    "quantserve.com", "quantcount.com", "zedo.com", "advertising.com", "adtechus.com", "adtech.de",
    # Analytics / session / telemetry
    "hotjar.com", "mixpanel.com", "segment.com", "segment.io", "fullstory.com", "amplitude.com",
    "mouseflow.com", "crazyegg.com", "optimizely.com", "branch.io", "appsflyer.com", "adjust.com",
    "kochava.com", "bugsnag.com", "newrelic.com", "nr-data.net", "chartbeat.com", "chartbeat.net",
    "sentry.io", "bluekai.com", "krxd.net", "demdex.net", "everesttech.net", "agkn.com",
    "exelator.com", "rlcdn.com", "tapad.com", "crwdcntrl.net", "addthis.com", "sharethis.com",
    "2mdn.net", "serving-sys.com", "flashtalking.com", "yadro.ru", "mc.yandex.ru",
    # Misc trackers / popunders / "push" spam
    "onesignal.com", "pushwoosh.com", "propellerads.com", "popads.net", "popcash.net",
    "revcontent.com", "mgid.com", "adblade.com", "exoclick.com", "trafficjunky.net", "juicyads.com",
}


# ---------------------------------------------------------------------------
# Persistence (user blocklist additions, allowlist, on/off state)
# ---------------------------------------------------------------------------

def _support_dir() -> Path:
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        d = Path(override)
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        d = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Ember"
    else:
        d = Path.home() / ".ember"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    return _support_dir() / "adblock.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("extra", [])
            data.setdefault("allow", [])
            data.setdefault("enabled", False)
            return data
    except Exception:
        pass
    return {"extra": [], "allow": [], "enabled": False}


def _save_state(st: dict) -> None:
    try:
        _state_path().write_text(json.dumps(st, indent=2), "utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hosts file
# ---------------------------------------------------------------------------

def _hosts_path() -> Path:
    override = os.environ.get("EMBER_HOSTS_FILE")
    if override:
        return Path(override)
    if sys.platform.startswith("win"):
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def _read_hosts() -> str:
    try:
        return _hosts_path().read_text("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_block(text: str) -> str:
    """Return `text` with any existing Ember ad-block section removed."""
    if _BEGIN not in text:
        return text
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == _BEGIN:
            skipping = True
            continue
        if line.strip() == _END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _normalize_domain(d: str) -> str:
    d = (d or "").strip().lower()
    for pre in ("http://", "https://"):
        if d.startswith(pre):
            d = d[len(pre):]
    d = d.split("/")[0].split(":")[0].strip(".")
    return d


def blocklist() -> set:
    """Effective set of blocked domains: builtin ∪ user-extra − allowlist."""
    st = _load_state()
    extra = {_normalize_domain(x) for x in st.get("extra", [])}
    allow = {_normalize_domain(x) for x in st.get("allow", [])}
    return ({_normalize_domain(x) for x in _BUILTIN_BLOCKLIST} | extra) - allow - {""}


def _render_block(domains) -> str:
    lines = [_BEGIN,
             "# Managed by Ember — do not edit by hand; use the Ad Blocker in Ember.",
             "# Disable with: Ember ▸ Ad blocker ▸ Disable (removes this whole block)."]
    for d in sorted(domains):
        lines.append(f"0.0.0.0 {d}")
        lines.append(f":: {d}")            # also sinkhole IPv6 lookups
    lines.append(_END)
    return "\n".join(lines)


def _backup_once(current: str) -> None:
    try:
        bak = _support_dir() / "hosts.backup"
        if not bak.exists() and current.strip():
            bak.write_text(current, "utf-8")
    except Exception:
        pass


def _apply_hosts(new_text: str, flush: bool = True) -> tuple[bool, str]:
    """Write the hosts file. In tests ($EMBER_HOSTS_FILE set) write directly; otherwise do a
    single privileged copy (+ DNS flush) so the user sees one admin prompt."""
    path = _hosts_path()
    if os.environ.get("EMBER_HOSTS_FILE"):
        try:
            path.write_text(new_text, "utf-8")
            return True, "written (test mode)"
        except Exception as e:
            return False, str(e)
    try:
        tmp = Path(tempfile.mkdtemp(prefix="ember_hosts_")) / "hosts.new"
        tmp.write_text(new_text, "utf-8")
        if sys.platform == "darwin":
            inner = f"cp {shlex.quote(str(tmp))} {shlex.quote(str(path))}"
            if flush:
                inner += " && dscacheutil -flushcache && killall -HUP mDNSResponder"
            script = f'do shell script "{inner}" with administrator privileges'
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=90)
            ok = r.returncode == 0
            return ok, (r.stderr or r.stdout or "").strip() if not ok else "applied"
        if sys.platform.startswith("win"):
            # Elevated copy + DNS flush via PowerShell UAC prompt.
            cmd = (f"Copy-Item -Force '{tmp}' '{path}'; ipconfig /flushdns")
            ps = (f"Start-Process -Verb RunAs -Wait powershell "
                  f"-ArgumentList '-NoProfile','-Command',\"{cmd}\"")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=90)
            return r.returncode == 0, (r.stderr or "applied").strip()
        # Linux: needs sudo (may prompt in the terminal Ember was launched from).
        r = subprocess.run(["sudo", "cp", str(tmp), str(path)], capture_output=True, text=True, timeout=90)
        if r.returncode == 0 and flush:
            subprocess.run(["sudo", "resolvectl", "flush-caches"], capture_output=True, timeout=20)
        return r.returncode == 0, (r.stderr or "applied").strip()
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def adblock_status() -> dict:
    """Whether the system-wide ad blocker is on, and how many domains it blocks."""
    st = _load_state()
    active = _BEGIN in _read_hosts()
    return {"ok": True, "enabled": bool(active), "state_flag": bool(st.get("enabled")),
            "blocked_domains": len(blocklist()),
            "user_added": len(st.get("extra", [])), "allowlisted": len(st.get("allow", [])),
            "hosts_file": str(_hosts_path())}


def adblock_enable() -> dict:
    """Turn ON system-wide ad blocking (writes the sinkhole block to the hosts file).
    Needs admin rights — the OS will prompt once."""
    current = _read_hosts()
    _backup_once(current)
    body = _strip_block(current).rstrip()
    new_text = (body + "\n\n" + _render_block(blocklist()) + "\n")
    ok, detail = _apply_hosts(new_text, flush=True)
    if ok:
        st = _load_state(); st["enabled"] = True; _save_state(st)
        return {"ok": True, "enabled": True, "blocked_domains": len(blocklist()),
                "message": f"System-wide ad blocking ON — {len(blocklist())} domains sinkholed."}
    return {"ok": False, "error": f"could not update the hosts file: {detail}"}


def adblock_disable() -> dict:
    """Turn OFF system-wide ad blocking (removes Ember's block from the hosts file)."""
    current = _read_hosts()
    if _BEGIN not in current:
        st = _load_state(); st["enabled"] = False; _save_state(st)
        return {"ok": True, "enabled": False, "message": "ad blocking was already off"}
    new_text = _strip_block(current)
    ok, detail = _apply_hosts(new_text, flush=True)
    if ok:
        st = _load_state(); st["enabled"] = False; _save_state(st)
        return {"ok": True, "enabled": False, "message": "System-wide ad blocking OFF."}
    return {"ok": False, "error": f"could not update the hosts file: {detail}"}


def adblock_add_domain(domain: str = "") -> dict:
    """Block an extra domain system-wide (re-applies if the blocker is on)."""
    d = _normalize_domain(domain)
    if not d or "." not in d:
        return {"ok": False, "error": "give a domain like ads.example.com"}
    st = _load_state()
    if d not in st["extra"]:
        st["extra"].append(d)
    if d in st["allow"]:
        st["allow"].remove(d)
    _save_state(st)
    res = {"ok": True, "added": d, "blocked_domains": len(blocklist())}
    if _BEGIN in _read_hosts():
        adblock_enable()
        res["reapplied"] = True
    return res


def adblock_allow_domain(domain: str = "") -> dict:
    """Allow (un-block) a domain the blocker would otherwise sinkhole."""
    d = _normalize_domain(domain)
    if not d:
        return {"ok": False, "error": "give a domain to allow"}
    st = _load_state()
    if d not in st["allow"]:
        st["allow"].append(d)
    if d in st["extra"]:
        st["extra"].remove(d)
    _save_state(st)
    res = {"ok": True, "allowed": d, "blocked_domains": len(blocklist())}
    if _BEGIN in _read_hosts():
        adblock_enable()
        res["reapplied"] = True
    return res


def adblock_lists() -> dict:
    """The user-managed lists (for a management UI): custom-blocked + allowlisted domains,
    plus totals. The built-in/StevenBlack domains aren't enumerated (too many) — just counted."""
    st = _load_state()
    extra = sorted(st.get("extra", []))
    allow = sorted(st.get("allow", []))
    total = len(blocklist())
    return {"ok": True, "extra": extra, "allow": allow,
            "blocked_domains": total, "builtin": max(0, total - len(extra)),
            "enabled": bool(_BEGIN in _read_hosts())}


def adblock_remove(domain: str = "") -> dict:
    """Forget a domain entirely — drop it from BOTH the custom-block and allow lists (so it
    falls back to default behaviour). Re-applies if the blocker is on."""
    d = _normalize_domain(domain)
    if not d:
        return {"ok": False, "error": "give a domain to remove"}
    st = _load_state()
    changed = False
    for key in ("extra", "allow"):
        if d in st.get(key, []):
            st[key].remove(d)
            changed = True
    if not changed:
        return {"ok": False, "error": f"{d} isn't in your custom lists"}
    _save_state(st)
    res = {"ok": True, "removed": d, "blocked_domains": len(blocklist())}
    if _BEGIN in _read_hosts():
        adblock_enable()
        res["reapplied"] = True
    return res


def adblock_update_from_url(url: str = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts") -> dict:
    """Pull a big public hosts blocklist (default: StevenBlack) and merge its domains in.
    Re-applies if the blocker is currently on."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Ember-AdBlock"})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:
        return {"ok": False, "error": f"could not fetch list: {e}"}
    added = 0
    st = _load_state()
    have = set(st["extra"]) | {_normalize_domain(x) for x in _BUILTIN_BLOCKLIST}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1"):
            d = _normalize_domain(parts[1])
            if d and d != "localhost" and d not in have:
                st["extra"].append(d); have.add(d); added += 1
    _save_state(st)
    res = {"ok": True, "added": added, "blocked_domains": len(blocklist())}
    if _BEGIN in _read_hosts():
        adblock_enable()
        res["reapplied"] = True
    return res


# ---------------------------------------------------------------------------
# Host wiring
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS = [
    {"name": "adblock_status",
     "description": "Report whether system-wide ad/tracker blocking is on and how many domains it blocks.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "adblock_enable",
     "description": "Turn ON system-wide ad blocking for ALL apps (sinkholes ad/tracker domains in the "
                    "hosts file). Needs admin rights; the OS prompts once.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "adblock_disable",
     "description": "Turn OFF system-wide ad blocking (removes Ember's hosts-file block).",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "adblock_add_domain",
     "description": "Block an extra domain system-wide.",
     "parameters": {"type": "OBJECT", "properties": {"domain": {"type": "STRING"}}, "required": ["domain"]}},
    {"name": "adblock_allow_domain",
     "description": "Allow (un-block) a domain the ad blocker would otherwise block.",
     "parameters": {"type": "OBJECT", "properties": {"domain": {"type": "STRING"}}, "required": ["domain"]}},
    {"name": "adblock_update_from_url",
     "description": "Merge a big public hosts blocklist (default StevenBlack) for far stronger coverage.",
     "parameters": {"type": "OBJECT", "properties": {"url": {"type": "STRING"}}, "required": []}},
    {"name": "adblock_lists",
     "description": "List the user's custom-blocked + allow-listed domains and totals.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "adblock_remove",
     "description": "Forget a domain entirely (drop it from both the custom-block and allow lists).",
     "parameters": {"type": "OBJECT", "properties": {"domain": {"type": "STRING"}}, "required": ["domain"]}},
]

TOOL_DISPATCH = {
    "adblock_status": adblock_status,
    "adblock_enable": adblock_enable,
    "adblock_disable": adblock_disable,
    "adblock_add_domain": adblock_add_domain,
    "adblock_allow_domain": adblock_allow_domain,
    "adblock_update_from_url": adblock_update_from_url,
    "adblock_lists": adblock_lists,
    "adblock_remove": adblock_remove,
}

READONLY_TOOLS = {"adblock_status", "adblock_lists"}
INTERACTION_TOOLS = {"adblock_enable", "adblock_disable", "adblock_add_domain",
                     "adblock_allow_domain", "adblock_update_from_url", "adblock_remove"}
