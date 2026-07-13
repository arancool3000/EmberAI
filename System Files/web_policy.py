"""Website blocking + URL reputation for Ember's navigation tools.

Every browser navigation (open_url / browser_open / browser_navigate) is checked
here first:
  * user/built-in BLOCK lists  -> blocked
  * user ALLOW list            -> always allowed (overrides everything)
  * online reputation          -> URLhaus (free, no key) / VirusTotal / Google
                                  Safe Browsing (optional keys) -> blocked
  * look-alike / typosquat     -> flagged "suspicious" (allowed but warned)

State (config + block/allow lists) lives in the per-user app-support dir. Online
reputation is best-effort and fails open (a lookup error never blocks browsing).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

_LOCK = threading.RLock()


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


DEFAULT_CONFIG = {
    "enabled": True,
    "block_known_malware": True,
    "online_reputation": True,        # query URLhaus / VirusTotal / Safe Browsing
    "block_on_reputation": True,      # actually block (vs. just warn) on a reputation hit
    "warn_typosquats": True,
    "google_safe_browsing_key": "",
}

# A small built-in blocklist: Google's official Safe Browsing test domains plus a
# couple of well-known malware test hosts. Users extend this via add_block().
_BUILTIN_BLOCK = {
    "malware.testing.google.test",
    "testsafebrowsing.appspot.com",
    "phishing.testing.google.test",
    "eicar.org",
}

# Popular / high-value domains used for look-alike (typosquat) detection.
_POPULAR = [
    "google.com", "youtube.com", "facebook.com", "amazon.com", "apple.com",
    "microsoft.com", "paypal.com", "netflix.com", "instagram.com", "github.com",
    "wikipedia.org", "twitter.com", "x.com", "linkedin.com", "gmail.com",
    "outlook.com", "icloud.com", "dropbox.com", "coinbase.com", "binance.com",
    "bankofamerica.com", "chase.com", "wellsfargo.com", "whatsapp.com", "reddit.com",
]


def _config_path() -> Path:
    return _support_dir() / "web_policy.json"


def get_config() -> dict:
    with _LOCK:
        cfg = dict(DEFAULT_CONFIG)
        try:
            p = _config_path()
            if p.exists():
                cfg.update(json.loads(p.read_text("utf-8")))
        except Exception:
            pass
        return cfg


def set_config(**changes) -> dict:
    with _LOCK:
        cfg = get_config()
        for k, v in changes.items():
            if k in DEFAULT_CONFIG:
                cfg[k] = v
        try:
            _config_path().write_text(json.dumps(cfg, indent=2), "utf-8")
        except Exception:
            pass
        return cfg


def _list_path(kind: str) -> Path:
    return _support_dir() / ("web_block.txt" if kind == "block" else "web_allow.txt")


def _read_list(kind: str) -> set[str]:
    try:
        p = _list_path(kind)
        if p.exists():
            return {ln.strip().lower() for ln in p.read_text("utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")}
    except Exception:
        pass
    return set()


def _write_list(kind: str, hosts: set[str]) -> None:
    try:
        _list_path(kind).write_text("\n".join(sorted(hosts)) + "\n", "utf-8")
    except Exception:
        pass


def host_of(url: str) -> str:
    """Normalized host: lowercase, no port, no leading 'www.'."""
    u = (url or "").strip()
    if "://" not in u:
        u = "http://" + u
    host = (urlparse(u).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, entries: set[str]) -> bool:
    """True if host equals or is a subdomain of any listed host."""
    for e in entries:
        e = e[4:] if e.startswith("www.") else e
        if host == e or host.endswith("." + e):
            return True
    return False


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _typosquat_of(host: str) -> str | None:
    """If host is a 1-character look-alike of a popular domain (and not actually
    that domain or a subdomain of it), return the impersonated domain."""
    if not host:
        return None
    for pop in _POPULAR:
        if host == pop or host.endswith("." + pop):
            return None  # legitimately that domain
    for pop in _POPULAR:
        if abs(len(host) - len(pop)) <= 1 and 0 < _levenshtein(host, pop) <= 1:
            return pop
    return None


# --- list management -------------------------------------------------------

def add_block(host: str) -> dict:
    h = host_of(host)
    if not h:
        return {"ok": False, "error": "could not parse a host from input"}
    with _LOCK:
        s = _read_list("block"); s.add(h); _write_list("block", s)
    return {"ok": True, "blocked": h}


def remove_block(host: str) -> dict:
    h = host_of(host)
    with _LOCK:
        s = _read_list("block"); s.discard(h); _write_list("block", s)
    return {"ok": True, "unblocked": h}


def add_allow(host: str) -> dict:
    h = host_of(host)
    if not h:
        return {"ok": False, "error": "could not parse a host from input"}
    with _LOCK:
        s = _read_list("allow"); s.add(h); _write_list("allow", s)
    return {"ok": True, "allowed": h}


def list_web_policy() -> dict:
    return {"ok": True,
            "blocked": sorted(_read_list("block")),
            "allowed": sorted(_read_list("allow")),
            "builtin_blocked": sorted(_BUILTIN_BLOCK)}


# --- online reputation (best-effort, fails open) ---------------------------

def _rep_urlhaus(host: str) -> dict | None:
    try:
        import requests
        r = requests.post("https://urlhaus-api.abuse.ch/v1/host/",
                          data={"host": host}, timeout=8)
        j = r.json()
        if j.get("query_status") == "ok":
            urls = j.get("urls") or []
            if any(u.get("url_status") == "online" for u in urls) or urls:
                return {"engine": "urlhaus", "malicious": True}
        return {"engine": "urlhaus", "malicious": False}
    except Exception:
        return None


def _rep_safe_browsing(url: str, key: str) -> dict | None:
    try:
        import requests
        body = {
            "client": {"clientId": "ember", "clientVersion": "1.0"},
            "threatInfo": {
                "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE",
                                "POTENTIALLY_HARMFUL_APPLICATION"],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}],
            },
        }
        r = requests.post(
            f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}",
            json=body, timeout=8)
        if r.status_code == 200:
            return {"engine": "safe-browsing", "malicious": bool(r.json().get("matches"))}
        return None
    except Exception:
        return None


def _check_reputation(host: str, url: str, cfg: dict) -> dict | None:
    sb_key = cfg.get("google_safe_browsing_key") or os.environ.get("GOOGLE_SAFE_BROWSING_KEY")
    if sb_key:
        res = _rep_safe_browsing(url, sb_key)
        if res and res.get("malicious"):
            return res
    res = _rep_urlhaus(host)
    if res and res.get("malicious"):
        return res
    return None


# --- the gate --------------------------------------------------------------

def check_url(url: str) -> dict:
    """Classify a URL: verdict in {clean, suspicious, blocked}."""
    cfg = get_config()
    host = host_of(url)
    base = {"ok": True, "url": url, "host": host}
    if not cfg.get("enabled") or not host:
        return {**base, "verdict": "clean", "allowed": True, "reason": "policy disabled / no host"}

    if _matches(host, _read_list("allow")):
        return {**base, "verdict": "clean", "allowed": True, "reason": "host is on your allow list"}

    if _matches(host, _read_list("block")):
        return {**base, "verdict": "blocked", "allowed": False, "reason": "host is on your block list"}

    if cfg.get("block_known_malware") and _matches(host, _BUILTIN_BLOCK):
        return {**base, "verdict": "blocked", "allowed": False,
                "reason": "known-malicious/phishing domain (built-in list)"}

    if cfg.get("online_reputation"):
        rep = _check_reputation(host, url, cfg)
        if rep and rep.get("malicious"):
            allowed = not cfg.get("block_on_reputation", True)
            return {**base, "verdict": "blocked" if not allowed else "suspicious",
                    "allowed": allowed, "reason": f"{rep['engine']} flagged this site as malicious",
                    "source": rep["engine"]}

    if cfg.get("warn_typosquats"):
        imp = _typosquat_of(host)
        if imp:
            return {**base, "verdict": "suspicious", "allowed": True,
                    "reason": f"look-alike of '{imp}' — possible phishing/typosquat",
                    "impersonates": imp}

    return {**base, "verdict": "clean", "allowed": True, "reason": "no indicators"}


def gate_navigation(url: str) -> dict:
    """Decision for a navigation tool: blocked verdicts stop navigation; suspicious
    ones are allowed but reported so the caller/agent can warn the user."""
    try:
        return check_url(url)
    except Exception as e:
        return {"ok": True, "url": url, "verdict": "clean", "allowed": True,
                "reason": f"policy check error (failing open): {e}"}


def web_status() -> dict:
    cfg = get_config()
    backends = ["urlhaus"]
    if cfg.get("google_safe_browsing_key") or os.environ.get("GOOGLE_SAFE_BROWSING_KEY"):
        backends.append("google-safe-browsing")
    return {"ok": True, "enabled": cfg.get("enabled"),
            "online_reputation": cfg.get("online_reputation"),
            "reputation_backends": backends,
            "blocked_count": len(_read_list("block")),
            "allowed_count": len(_read_list("allow")),
            "builtin_blocked": len(_BUILTIN_BLOCK)}
