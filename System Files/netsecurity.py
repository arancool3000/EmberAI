"""Network-security assessment — Wi-Fi encryption grading + remote-access exposure audit.

These are READ-ONLY, evidence-led checks you run on networks and machines you own or are
authorized to test. Nothing is changed and nothing is uploaded — Ember reads the OS's own
Wi-Fi/port state, grades it, and tells you the specific fix.

Two questions people actually ask:

  • "Is my Wi-Fi safe?"  -> `wifi_security()` reads the encryption of the network you're on
    (Open / WEP / WPA / WPA2 / WPA3) and grades it, because an *open* or *WEP* network means
    anyone nearby can read your traffic.
  • "Can anyone connect to my computer?"  -> `remote_access_audit()` finds remote-control
    services that are LISTENING (SSH, Remote Desktop, VNC/Screen Sharing, Telnet, SMB file
    sharing, WinRM) and, crucially, whether they're bound to loopback (safe), the local network,
    or every interface (reachable from anywhere) — plus whether the firewall is on.

The GRADING is pure: `assess_wifi()` and `assess_remote_exposure()` take already-gathered facts
and return a scored assessment, so the whole judgement is unit-tested with no OS, no network and
no root. The OS probes that gather those facts are thin wrappers behind an injectable runner.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from typing import Callable, Optional

# Injection point for tests: _RUNNER(cmd_list) -> stdout str ("" on any failure).
_RUNNER: Optional[Callable[[list], str]] = None


def _run(cmd: list, timeout: float = 8.0) -> str:
    if _RUNNER is not None:
        return _RUNNER(cmd) or ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (("\n" + r.stderr) if r.returncode and r.stderr else "")
    except Exception:
        return ""


def _rating(score: int) -> str:
    if score >= 90:
        return "Secure"
    if score >= 70:
        return "Mostly secure"
    if score >= 40:
        return "Needs attention"
    return "At risk"


# ===========================================================================
# Wi-Fi security
# ===========================================================================

# Canonical encryption tiers, worst -> best, with a base score and one-line verdict.
_WIFI_TIERS = {
    "open":    (5,   "critical", "Open network — traffic is unencrypted; anyone nearby can read it."),
    "wep":     (15,  "critical", "WEP encryption is broken and trivially cracked — treat as open."),
    "wpa":     (55,  "warning",  "WPA/TKIP is outdated and weak — prefer WPA2 (AES) or WPA3."),
    "wpa2":    (85,  "ok",       "WPA2 (AES) — solid, current encryption."),
    "wpa3":    (100, "ok",       "WPA3 — the strongest current Wi-Fi encryption."),
    "unknown": (60,  "warning",  "Couldn't determine the encryption — verify it's WPA2 or WPA3."),
}


def normalize_security(raw: str) -> str:
    """Map a platform's Wi-Fi security/authentication string to a canonical tier key."""
    s = (raw or "").strip().lower()
    if not s:
        return "unknown"
    # WPA3 shows up as "wpa3", or via its handshake "sae".
    if "wpa3" in s or "sae" in s:
        return "wpa3"
    if "wpa2" in s or "rsn" in s:
        return "wpa2"
    # Plain "wpa" / "wpa-psk" / "tkip" without a 2/3 -> original WPA.
    if "wpa" in s or "tkip" in s:
        return "wpa"
    if "wep" in s:
        return "wep"
    if any(t in s for t in ("open", "none", "unsecured", "no auth", "no encryption")):
        return "open"
    return "unknown"


def assess_wifi(ssid: Optional[str], security_raw: str) -> dict:
    """Grade the encryption of the connected Wi-Fi. Pure — takes the SSID + raw security string."""
    tier = normalize_security(security_raw)
    score, level, verdict = _WIFI_TIERS[tier]
    enterprise = bool(re.search(r"enterprise|802\.1x|eap", (security_raw or ""), re.I))
    if enterprise and tier in ("wpa", "wpa2", "wpa3"):
        score = min(100, score + 5)   # 802.1X adds per-user auth on top of the cipher
    findings = []
    if level != "ok":
        fix = {
            "open": "Connect only to password-protected (WPA2/WPA3) networks. On open Wi-Fi, use a "
                    "VPN so your traffic is encrypted.",
            "wep": "Change the router's security to WPA2 or WPA3 (WEP must not be used). Until then, "
                   "treat this network as public and use a VPN.",
            "wpa": "In the router settings, switch to WPA2 (AES) or WPA3.",
            "unknown": "Open your Wi-Fi settings and confirm the network uses WPA2 or WPA3.",
        }.get(tier, "")
        findings.append({"level": level, "title": _WIFI_TIERS[tier][2].split(" — ")[0],
                         "detail": verdict, "fix": fix})
    elif tier == "wpa2":
        findings.append({"level": "ok",
                         "title": "WPA2 (AES) encryption",
                         "detail": verdict,
                         "fix": "If your router and devices support it, WPA3 is even stronger."})
    else:
        findings.append({"level": "ok", "title": "WPA3 encryption", "detail": verdict, "fix": ""})

    open_or_broken = tier in ("open", "wep")
    return {
        "ok": True,
        "ssid": ssid or None,
        "security": tier.upper() if tier != "unknown" else "Unknown",
        "security_detail": (security_raw or "").strip() or None,
        "enterprise": enterprise,
        "score": score,
        "rating": _rating(score),
        "safe": not open_or_broken and level == "ok",
        "findings": findings,
        "summary": (f"{ssid or 'This network'}: {tier.upper()} — {_rating(score)}. " + verdict),
    }


# ---- OS probes (thin; parse the platform's own Wi-Fi tool) ----------------

def parse_macos_airport(text: str) -> tuple:
    """From `system_profiler SPAirPortDataType`, return (ssid, security) of the CURRENT network."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if "Current Network Information:" in line:
            # The next non-empty line is the SSID (indented, ends with ':').
            for j in range(i + 1, min(i + 3, len(lines))):
                ssid = lines[j].strip().rstrip(":").strip()
                if ssid:
                    sec = ""
                    for k in range(j + 1, min(j + 12, len(lines))):
                        m = re.match(r"\s*Security:\s*(.+)$", lines[k])
                        if m:
                            sec = m.group(1).strip()
                            break
                        if lines[k].strip().endswith(":") and not lines[k].strip().startswith("PHY"):
                            break   # ran into the next network block
                    return ssid, sec
    return None, ""


def parse_windows_netsh(text: str) -> tuple:
    """From `netsh wlan show interfaces`, return (ssid, 'authentication cipher')."""
    info = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip().lower()] = v.strip()
    ssid = info.get("ssid") or None
    sec = " ".join(x for x in (info.get("authentication"), info.get("cipher")) if x)
    return ssid, sec


def parse_linux_nmcli(text: str) -> tuple:
    """From `nmcli -t -f active,ssid,security dev wifi`, return (ssid, security) of the active row."""
    for line in (text or "").splitlines():
        # Fields are colon-separated; a ':' inside a value is escaped as '\:' by nmcli -t.
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 3 and parts[0].strip().lower() in ("yes", "*"):
            ssid = parts[1].replace("\\:", ":").strip()
            sec = parts[2].strip()
            return (ssid or None), sec
    return None, ""


def _gather_wifi() -> tuple:
    """(ssid, security_raw, error) for the current Wi-Fi, best-effort per platform."""
    if sys.platform == "darwin":
        txt = _run(["system_profiler", "SPAirPortDataType"], timeout=12)
        if not txt:
            return None, "", "couldn't read Wi-Fi info (system_profiler unavailable)"
        ssid, sec = parse_macos_airport(txt)
        return ssid, sec, ("" if ssid else "not connected to Wi-Fi")
    if sys.platform.startswith("win"):
        txt = _run(["netsh", "wlan", "show", "interfaces"], timeout=10)
        if not txt:
            return None, "", "couldn't read Wi-Fi info (netsh unavailable)"
        ssid, sec = parse_windows_netsh(txt)
        return ssid, sec, ("" if ssid else "not connected to Wi-Fi")
    if shutil.which("nmcli"):
        txt = _run(["nmcli", "-t", "-f", "active,ssid,security", "dev", "wifi"], timeout=10)
        ssid, sec = parse_linux_nmcli(txt)
        return ssid, sec, ("" if ssid else "not connected to Wi-Fi")
    return None, "", "no supported Wi-Fi tool on this platform"


def wifi_security() -> dict:
    """Assess the security of the Wi-Fi network this computer is currently connected to."""
    ssid, sec, err = _gather_wifi()
    if err and not ssid:
        return {"ok": False, "error": err}
    return assess_wifi(ssid, sec)


# ===========================================================================
# Remote-access exposure
# ===========================================================================

# Ports that expose a way to CONNECT INTO / CONTROL this machine (name, human description).
_REMOTE_SERVICES = {
    22:   ("SSH", "Remote Login / SSH"),
    23:   ("Telnet", "Telnet — unencrypted remote shell"),
    139:  ("NetBIOS", "NetBIOS / Windows file sharing"),
    445:  ("SMB", "Windows File & Printer Sharing (SMB)"),
    512:  ("rexec", "Berkeley remote exec"),
    513:  ("rlogin", "Berkeley remote login"),
    514:  ("rshell", "Berkeley remote shell"),
    3283: ("ARD", "Apple Remote Desktop"),
    3389: ("RDP", "Windows Remote Desktop"),
    5900: ("VNC", "Screen Sharing / VNC"),
    5901: ("VNC", "VNC display :1"),
    5902: ("VNC", "VNC display :2"),
    5903: ("VNC", "VNC display :3"),
    5985: ("WinRM", "Windows Remote Management (HTTP)"),
    5986: ("WinRM", "Windows Remote Management (HTTPS)"),
    5988: ("WBEM", "Remote management (WBEM)"),
}

# Plaintext protocols — extra dangerous whenever they're reachable at all.
_PLAINTEXT_PORTS = {23, 512, 513, 514}


def _addr_scope(ip: str) -> str:
    """Where a listening socket can be reached from: 'loopback' (safe), 'lan', or 'all' (any net)."""
    ip = (ip or "").strip().strip("[]")
    if ip in ("", "*"):
        return "all"
    if ip in ("0.0.0.0", "::", "0:0:0:0:0:0:0:0"):
        return "all"
    if ip.startswith("127.") or ip in ("::1", "0:0:0:0:0:0:0:1") or ip.lower() == "localhost":
        return "loopback"
    if ip.startswith("::ffff:127.") or ip.startswith("fe80"):
        return "loopback" if "127." in ip else "lan"
    # A concrete private/link-local address -> reachable on the local network.
    if (ip.startswith("10.") or ip.startswith("192.168.")
            or re.match(r"172\.(1[6-9]|2\d|3[01])\.", ip)
            or ip.startswith("169.254.") or ip.startswith("fd") or ip.startswith("fc")):
        return "lan"
    return "all"   # a public/other bound address -> treat as broadly reachable


def _split_addr(addr: str) -> tuple:
    """'ip:port' (incl. IPv6 '[::]:22') -> (ip, port|None)."""
    addr = (addr or "").strip()
    if "]" in addr:                       # [ipv6]:port
        host, _, port = addr.rpartition(":")
        return host.strip("[]"), _to_int(port)
    if addr.count(":") == 1:
        host, _, port = addr.partition(":")
        return host, _to_int(port)
    if ":" in addr:                       # bare ipv6 with no port
        return addr, None
    return addr, None


def _to_int(x) -> Optional[int]:
    try:
        return int(str(x).strip())
    except Exception:
        return None


def assess_remote_exposure(listening: list, established: Optional[list] = None,
                           firewall: Optional[dict] = None) -> dict:
    """Grade remote-access exposure. Pure.

    listening:  [{addr:'ip:port', proto, process, pid}] (as utilities.list_open_ports)
    established: [{local_port:int, remote:'ip:port', process}] — active inbound sessions
    firewall:   {'enabled': bool|None, 'detail': str}
    """
    findings = []
    exposed = []
    score = 100
    established = established or []
    firewall = firewall or {"enabled": None, "detail": ""}

    for row in listening or []:
        ip, port = _split_addr(row.get("addr", ""))
        if port not in _REMOTE_SERVICES:
            continue
        scope = _addr_scope(ip)
        if scope == "loopback":
            continue   # only reachable from this machine — not a remote exposure
        name, desc = _REMOTE_SERVICES[port]
        proc = row.get("process") or ""
        item = {"port": port, "service": name, "description": desc, "scope": scope,
                "address": row.get("addr", ""), "process": proc}
        exposed.append(item)
        plaintext = port in _PLAINTEXT_PORTS
        if scope == "all" or plaintext:
            level, pen = "critical", (40 if plaintext else 35)
        else:
            level, pen = "warning", 15
        score -= pen
        where = ("every network interface (reachable from anywhere on the network / internet)"
                 if scope == "all" else "the local network")
        fix = (f"If you don't use {name}, turn it off"
               + {
                   "RDP": " (System ▸ Remote Desktop → Off).",
                   "VNC": " (macOS: System Settings ▸ General ▸ Sharing ▸ Screen Sharing → Off).",
                   "SSH": " (macOS: Sharing ▸ Remote Login → Off; Linux: disable sshd).",
                   "Telnet": " immediately — it sends everything, including passwords, in clear text.",
                   "SMB": " (disable File Sharing) or restrict it to trusted networks.",
                   "WinRM": " (disable WinRM) or scope it to trusted hosts.",
                   "ARD": " (Sharing ▸ Remote Management → Off).",
               }.get(name, ".")
               + " If you do need it, require a strong password/keys and limit it to trusted "
                 "networks with the firewall.")
        findings.append({"level": level,
                         "title": f"{desc} is listening on {where}",
                         "detail": f"Port {port}/{row.get('proto', 'tcp')} "
                                   f"({row.get('addr', '')}){(' — ' + proc) if proc else ''}.",
                         "fix": fix})

    # Active inbound remote-control sessions (someone is connected to one of these ports right now).
    inbound = []
    for c in established:
        lp = _to_int(c.get("local_port"))
        if lp in _REMOTE_SERVICES:
            name, desc = _REMOTE_SERVICES[lp]
            inbound.append({"service": name, "remote": c.get("remote", ""), "port": lp})
            score -= 10
            findings.append({"level": "warning",
                             "title": f"Active {name} session from {c.get('remote', 'a remote host')}",
                             "detail": f"Something is connected to your {desc} (port {lp}) right now.",
                             "fix": "If that isn't you, disconnect it and change the account "
                                    "password; then disable the service if you don't need it."})

    fw_enabled = firewall.get("enabled")
    if fw_enabled is False:
        score -= 20
        findings.append({"level": "warning", "title": "Firewall is off",
                         "detail": firewall.get("detail") or "The system firewall is disabled.",
                         "fix": "Turn the firewall on so unrequested inbound connections are blocked "
                                "by default."})

    score = max(0, min(100, score))
    if not findings:
        findings.append({"level": "ok", "title": "No remote-access services exposed",
                         "detail": "No SSH/RDP/VNC/Telnet/SMB-style services are listening on a "
                                   "network-reachable address.",
                         "fix": ""})
    n_crit = sum(1 for f in findings if f["level"] == "critical")
    n_warn = sum(1 for f in findings if f["level"] == "warning")
    return {
        "ok": True,
        "score": score,
        "rating": _rating(score),
        "exposed_services": exposed,
        "inbound_sessions": inbound,
        "firewall": firewall,
        "findings": findings,
        "summary": (f"{_rating(score)} — "
                    + (f"{len(exposed)} remote-access service(s) reachable"
                       if exposed else "no remote-access services reachable")
                    + (f", {len(inbound)} active inbound session(s)" if inbound else "")
                    + (", firewall OFF" if fw_enabled is False else "")
                    + f". {n_crit} critical, {n_warn} warning."),
    }


# ---- OS probes for the remote audit ---------------------------------------

def _gather_listening() -> list:
    try:
        import utilities
        r = utilities.list_open_ports()
        return r.get("listening", []) if r.get("ok") else []
    except Exception:
        return []


def _gather_established_inbound() -> list:
    """Established sockets whose LOCAL port is a remote-access service (i.e. inbound sessions)."""
    try:
        import psutil
    except Exception:
        return []
    out = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status != getattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED"):
                continue
            if not c.laddr or not c.raddr:
                continue
            lp = getattr(c.laddr, "port", None)
            if lp in _REMOTE_SERVICES:
                proc = ""
                if c.pid:
                    try:
                        proc = psutil.Process(c.pid).name()
                    except Exception:
                        proc = ""
                out.append({"local_port": lp,
                            "remote": f"{c.raddr.ip}:{c.raddr.port}", "process": proc})
    except Exception:
        return out
    return out


def _gather_firewall() -> dict:
    """Best-effort system firewall state: {enabled: bool|None, detail: str}."""
    try:
        if sys.platform == "darwin":
            txt = _run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"])
            if not txt:
                return {"enabled": None, "detail": "couldn't read firewall state"}
            low = txt.lower()
            if "enabled" in low:
                return {"enabled": True, "detail": "Application Firewall is on."}
            if "disabled" in low:
                return {"enabled": False, "detail": "Application Firewall is off."}
            return {"enabled": None, "detail": txt.strip()[:120]}
        if sys.platform.startswith("win"):
            txt = _run(["netsh", "advfirewall", "show", "allprofiles", "state"])
            states = re.findall(r"State\s+(ON|OFF)", txt, re.I)
            if not states:
                return {"enabled": None, "detail": "couldn't read firewall state"}
            if any(s.upper() == "OFF" for s in states):
                return {"enabled": False, "detail": "One or more firewall profiles are OFF."}
            return {"enabled": True, "detail": "All firewall profiles are ON."}
        if shutil.which("ufw"):
            txt = _run(["ufw", "status"])
            if "inactive" in txt.lower():
                return {"enabled": False, "detail": "ufw is inactive."}
            if "active" in txt.lower():
                return {"enabled": True, "detail": "ufw is active."}
        return {"enabled": None, "detail": "firewall state unknown on this platform"}
    except Exception as e:
        return {"enabled": None, "detail": f"couldn't read firewall state: {e}"}


def remote_access_audit() -> dict:
    """Audit which remote-control services (SSH/RDP/VNC/Telnet/SMB/WinRM) are reachable, whether
    anyone is connected, and whether the firewall is on — with the specific fix for each."""
    return assess_remote_exposure(_gather_listening(), _gather_established_inbound(),
                                  _gather_firewall())


# ===========================================================================
# Combined report + tool layer
# ===========================================================================

def network_security_report() -> dict:
    """Run BOTH the Wi-Fi and remote-access assessments and combine them into one report."""
    wifi = wifi_security()
    remote = remote_access_audit()
    scores = [a["score"] for a in (wifi, remote) if a.get("ok") and "score" in a]
    overall = min(scores) if scores else None   # a chain is as safe as its weakest link
    return {"ok": True, "wifi": wifi, "remote": remote,
            "overall_score": overall,
            "overall_rating": _rating(overall) if overall is not None else "Unknown",
            "summary": "Network security: "
                       + (f"{_rating(overall)} (worst area drives the rating). " if overall is not None else "")
                       + f"Wi-Fi — {wifi.get('summary', wifi.get('error', 'n/a'))} "
                       + f"Remote access — {remote.get('summary', remote.get('error', 'n/a'))}"}


TOOL_DECLARATIONS = [
    {"name": "wifi_security",
     "description": "Check how secure the CURRENT Wi-Fi network is: reads its encryption "
                    "(Open / WEP / WPA / WPA2 / WPA3), grades it, and warns with a fix if the "
                    "network is open or weakly encrypted. Read-only.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "remote_access_audit",
     "description": "Audit remote-connection security: finds remote-control services that are "
                    "listening (SSH, Remote Desktop/RDP, VNC/Screen Sharing, Telnet, SMB file "
                    "sharing, WinRM), whether each is reachable from the local network or the whole "
                    "internet (vs loopback-only), any active inbound sessions, and whether the "
                    "firewall is on — each with the specific fix. Read-only.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "network_security_report",
     "description": "Full network-security report: runs both the Wi-Fi encryption check and the "
                    "remote-access exposure audit and returns one combined, graded result. Use for "
                    "'is my network / Wi-Fi / remote access secure?'. Read-only.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
]

TOOL_DISPATCH = {
    "wifi_security": wifi_security,
    "remote_access_audit": remote_access_audit,
    "network_security_report": network_security_report,
}

# All three only READ the OS's own Wi-Fi/port/firewall state and grade it — they change nothing
# and upload nothing — so they're safe/read-only.
READONLY_TOOLS = {"wifi_security", "remote_access_audit", "network_security_report"}
