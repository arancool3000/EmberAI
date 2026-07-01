"""Norton-style security suite for Ember — a "My Norton"-style dashboard, a Software Updater,
and dark-web monitoring, built on top of Ember's existing protections.

Ember already covers most of Norton 360's pillars: real-time antivirus + fileless/behavioral
protection (antivirus / fileless_guard / security_center), Smart-Firewall-style connection
scanning (security_center.scan_network), Safe Web (web_policy), Password Manager (key_vault /
browser_passwords), Secure VPN (vpn), Dark-Web/breach checks (productivity_tools.email_breach_check
/ privacy.password_pwned_check), cleanup/optimisation (cleanup), and an emergency lockdown
(panic). This module adds the missing pieces and ties them into ONE scored dashboard:

  * Software Updater  — outdated apps + pending OS updates (Norton's Software Updater).
  * Security Score    — a single 0-100 grade across every protection, with prioritised fixes.
  * Dark-web monitor  — surfaces breach checks for an email in the dashboard.

The score is a PURE function (compute_dashboard) of collected signals, so it's unit-tested
without touching the OS; the collectors are best-effort and never raise.
"""
from __future__ import annotations

import re
import subprocess
import sys

# Weighted components of the security score (sum = 100), Norton-dashboard style.
_WEIGHTS = {
    "realtime_protection": 18,   # always-on Security Center running
    "malware_engine": 12,        # antivirus enabled
    "fileless_protection": 10,   # behavioral/in-memory monitor
    "web_protection": 8,         # Safe Web
    "network_monitoring": 8,     # Smart-Firewall-style connection scanning
    "updates_current": 14,       # Software Updater: nothing pending
    "no_active_threats": 10,     # nothing quarantined/flagged right now
    "safe_open": 4,              # AI hold-unconfirmed-files
    "password_vault": 6,         # encrypted key vault in use
    "vpn_available": 6,          # a VPN location is configured
    "auto_lockdown": 4,          # panic auto-lockdown armed
}

_LABELS = {
    "realtime_protection": "Real-time protection (Security Center)",
    "malware_engine": "Malware scanning engine",
    "fileless_protection": "Fileless / behavioral protection",
    "web_protection": "Safe Web (malicious-site blocking)",
    "network_monitoring": "Smart firewall (connection monitoring)",
    "updates_current": "Software & OS up to date",
    "no_active_threats": "No active threats",
    "safe_open": "AI safe-open for risky files",
    "password_vault": "Password vault in use",
    "vpn_available": "VPN configured",
    "auto_lockdown": "Auto-lockdown armed",
}

_FIX = {
    "realtime_protection": "Turn on the always-on Security Center (Settings → Security).",
    "malware_engine": "Install ClamAV or set a VirusTotal key for stronger scanning.",
    "fileless_protection": "Enable real-time fileless protection (Settings → Security).",
    "web_protection": "Enable Safe Web / malicious-site blocking.",
    "network_monitoring": "Enable continuous network scanning in the Security Center.",
    "updates_current": "Install pending software / OS updates (see the Software Updater).",
    "no_active_threats": "Review and clear flagged items in the Security Center.",
    "safe_open": "Turn on 'AI-scan unconfirmed files and hold until I confirm'.",
    "password_vault": "Store your API keys in the encrypted vault instead of plaintext.",
    "vpn_available": "Add a WireGuard VPN location for encrypted browsing.",
    "auto_lockdown": "Arm auto-lockdown so a critical threat triggers an instant lockdown.",
}


# ---------------------------------------------------------------------------
# Software Updater
# ---------------------------------------------------------------------------
def _run(cmd: list, timeout: float = 60.0) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.returncode == 0, (r.stdout or "") + ("\n" + r.stderr if r.stderr else ""))
    except Exception as e:
        return (False, str(e))


def _parse_macos_softwareupdate(output: str) -> list:
    """Parse `softwareupdate -l` output into a list of update names. Prefers the '* Label:' lines
    (one per update); falls back to 'Title:' lines on older formats."""
    labels, titles = [], []
    for line in (output or "").splitlines():
        s = line.strip()
        m = re.match(r"\*?\s*Label:\s*(.+)$", s)
        if m:
            labels.append(m.group(1).strip())
            continue
        m = re.match(r"Title:\s*(.+?),", s)
        if m:
            titles.append(m.group(1).strip())
    items = labels or titles
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it); out.append(it)
    return out


def _brew_outdated(output: str) -> list:
    return [ln.split()[0] for ln in (output or "").splitlines() if ln.strip()]


def software_update_check() -> dict:
    """Norton-style Software Updater: list outdated apps + pending OS updates. Best-effort and
    read-only (it never installs anything). Returns {ok, total, os_updates, app_updates}."""
    os_updates: list = []
    app_updates: list = []
    notes: list = []
    if sys.platform == "darwin":
        ok, out = _run(["softwareupdate", "-l"], timeout=120)
        if ok or out:
            if "No new software available" in out:
                pass
            else:
                os_updates = _parse_macos_softwareupdate(out)
        import shutil
        if shutil.which("brew"):
            okb, outb = _run(["brew", "outdated", "--quiet"], timeout=120)
            if okb:
                app_updates = _brew_outdated(outb)
        else:
            notes.append("Install Homebrew to also check app updates.")
    elif sys.platform.startswith("win"):
        try:
            import tools
            r = tools.get_windows_updates(days=90)
            if isinstance(r, dict) and r.get("ok"):
                os_updates = [u.get("Title", str(u)) if isinstance(u, dict) else str(u)
                              for u in (r.get("updates") or r.get("events") or [])]
        except Exception as e:
            notes.append(f"Windows Update check unavailable: {e}")
        import shutil
        if shutil.which("winget"):
            okw, outw = _run(["winget", "upgrade"], timeout=120)
            if okw:
                app_updates = [ln.split()[0] for ln in outw.splitlines()[2:] if ln.strip()][:50]
    else:
        import shutil
        if shutil.which("apt"):
            _run(["apt-get", "update"], timeout=60)
            ok, out = _run(["apt", "list", "--upgradable"], timeout=60)
            if ok:
                app_updates = [ln.split("/")[0] for ln in out.splitlines()
                               if "/" in ln and "Listing" not in ln]
    total = len(os_updates) + len(app_updates)
    return {"ok": True, "total": total, "os_updates": os_updates[:50],
            "app_updates": app_updates[:80], "notes": notes,
            "summary": (f"{total} update(s) available "
                        f"({len(os_updates)} system, {len(app_updates)} app)."
                        if total else "Everything is up to date.")}


# ---------------------------------------------------------------------------
# Unified dashboard (pure score + collectors)
# ---------------------------------------------------------------------------
def compute_dashboard(signals: dict) -> dict:
    """PURE: turn a dict of boolean protection signals into a Norton-style score + grade +
    per-component breakdown + prioritised recommendations. Unit-tested with no OS access."""
    components = []
    score = 0
    for key, weight in _WEIGHTS.items():
        ok = bool(signals.get(key))
        if ok:
            score += weight
        components.append({"key": key, "label": _LABELS[key], "ok": ok, "weight": weight})
    score = max(0, min(100, score))
    grade = ("A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60
             else "D" if score >= 40 else "F")
    rating = ("excellent" if score >= 90 else "strong" if score >= 75
              else "fair" if score >= 60 else "weak" if score >= 40 else "at risk")
    # recommendations: failing components, worst (highest-weight) first
    recs = [{"key": c["key"], "fix": _FIX[c["key"]], "weight": c["weight"]}
            for c in components if not c["ok"]]
    recs.sort(key=lambda r: r["weight"], reverse=True)
    return {"ok": True, "score": score, "grade": grade, "rating": rating,
            "components": components,
            "recommendations": [r["fix"] for r in recs],
            # Same recommendations, but with the component "key" kept alongside the fix text
            # (recommendations above stays plain strings for existing callers) — this is what
            # lets the UI put a real per-item action button next to each one instead of a
            # static list nobody can act on directly.
            "recommendation_items": [{"key": r["key"], "fix": r["fix"]} for r in recs]}


def _collect_signals(update_total: int | None = None) -> dict:
    """Best-effort gather of live protection signals (each guarded; defaults to False)."""
    s = {k: False for k in _WEIGHTS}
    try:
        import antivirus
        cfg = antivirus.get_config()
        st = antivirus.security_status()
        s["malware_engine"] = bool(cfg.get("enabled")) and bool(st.get("engines_available"))
        s["fileless_protection"] = bool(cfg.get("fileless_protection"))
        s["safe_open"] = bool(cfg.get("ai_scan_on_open")) and bool(cfg.get("require_confirm_unconfirmed"))
        s["no_active_threats"] = int(st.get("quarantine_count", 0)) == 0
    except Exception:
        pass
    try:
        import security_center
        sc = security_center.security_center_status()
        s["realtime_protection"] = bool(sc.get("running"))
        s["network_monitoring"] = bool(sc.get("running"))
    except Exception:
        pass
    try:
        import web_policy
        s["web_protection"] = bool(web_policy.get_config().get("enabled"))
    except Exception:
        pass
    try:
        import key_vault
        s["password_vault"] = len(key_vault.list_keys()) > 0
    except Exception:
        pass
    try:
        import vpn
        locs = vpn.list_vpn_locations()
        s["vpn_available"] = bool((locs.get("locations") if isinstance(locs, dict) else locs))
    except Exception:
        pass
    try:
        import panic
        s["auto_lockdown"] = panic.is_armed()
    except Exception:
        pass
    if update_total is not None:
        s["updates_current"] = update_total == 0
    return s


def security_dashboard(check_updates: bool = True, email: str = "") -> dict:
    """The 'My Ember Security' overview: one score across every protection, plus the Software
    Updater result and (optional) a dark-web breach check for an email. Read-only."""
    upd = software_update_check() if check_updates else {"ok": True, "total": None}
    signals = _collect_signals(upd.get("total"))
    dash = compute_dashboard(signals)
    dash["updates"] = upd
    if email:
        try:
            import productivity_tools
            dash["dark_web"] = productivity_tools.email_breach_check(email)
        except Exception as e:
            dash["dark_web"] = {"ok": False, "error": str(e)}
    dash["summary"] = (f"Security score {dash['score']}/100 (grade {dash['grade']}, "
                       f"{dash['rating']}). " + (upd.get("summary", "") if check_updates else ""))
    return dash


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {"name": "security_dashboard",
     "description": "Norton-style security overview: a 0-100 score across every protection "
                    "(antivirus, firewall/network, web, updates, vault, VPN, lockdown), with "
                    "prioritised fixes, the Software Updater result, and an optional dark-web "
                    "breach check for an email.",
     "parameters": {"type": "OBJECT", "properties": {
        "check_updates": {"type": "BOOLEAN"},
        "email": {"type": "STRING", "description": "optional, for a dark-web breach check"}},
        "required": []}},
    {"name": "software_update_check",
     "description": "List outdated apps + pending OS updates (Software Updater). Read-only; never "
                    "installs anything.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "security_dashboard": security_dashboard,
    "software_update_check": software_update_check,
}

READONLY_TOOLS = {"security_dashboard", "software_update_check"}
INTERACTION_TOOLS: set = set()
