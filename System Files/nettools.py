"""Network toolkit: TCP port scan, LAN device discovery, Wi-Fi info.
Diagnostic tools — use them on systems/networks you own or are authorized to test."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys

_COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 139, 143, 389, 443, 445, 587, 631,
                 993, 995, 1433, 1521, 2049, 3000, 3306, 3389, 5432, 5900, 6379,
                 8000, 8080, 8443, 9000]


def scan_host_ports(host: str = "127.0.0.1", ports=None, timeout: float = 0.4) -> dict:
    """TCP connect-scan a host for open ports (defaults to common ports)."""
    try:
        ip = socket.gethostbyname(host)
    except Exception as e:
        return {"ok": False, "error": f"could not resolve {host}: {e}"}
    if ports is None:
        plist = list(_COMMON_PORTS)
    elif isinstance(ports, str):
        plist = [int(x) for x in re.findall(r"\d+", ports)]
    else:
        plist = [int(x) for x in ports]
    plist = [p for p in plist if 0 < p < 65536][:300]
    open_ports = []
    for p in plist:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, p)) == 0:
                try:
                    svc = socket.getservbyport(p)
                except Exception:
                    svc = ""
                open_ports.append({"port": p, "service": svc})
        except Exception:
            pass
        finally:
            s.close()
    return {"ok": True, "host": host, "ip": ip, "scanned": len(plist), "open": open_ports}


def network_devices() -> dict:
    """Devices currently seen on the local network (parsed from the ARP table)."""
    if not shutil.which("arp"):
        return {"ok": False, "error": "arp not available"}
    try:
        r = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10)
        devs = []
        for line in (r.stdout or "").splitlines():
            m = re.search(r"\(?(\d+\.\d+\.\d+\.\d+)\)?\s+at\s+([0-9a-fA-F:]+)", line)
            if m:
                devs.append({"ip": m.group(1), "mac": m.group(2)})
        return {"ok": True, "count": len(devs), "devices": devs}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def network_connections() -> dict:
    """List active (established) network connections + owning process — a quick security
    monitor of what's talking to the network."""
    conns = []
    try:
        if shutil.which("lsof"):
            r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"],
                               capture_output=True, text=True, timeout=12)
            for line in (r.stdout or "").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9 and "->" in parts[8]:
                    conns.append({"process": parts[0], "pid": parts[1],
                                  "remote": parts[8].split("->")[-1]})
        elif shutil.which("netstat"):
            r = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=12)
            for line in (r.stdout or "").splitlines():
                if "ESTAB" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        conns.append({"remote": parts[4]})
        else:
            return {"ok": False, "error": "no lsof/netstat available"}
        seen, uniq = set(), []
        for c in conns:
            key = (c.get("process"), c.get("remote"))
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        return {"ok": True, "count": len(uniq), "connections": uniq[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def wifi_info() -> dict:
    """Current Wi-Fi network name + signal (best-effort, platform-specific)."""
    try:
        if sys.platform == "darwin":
            ap = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
                  "Versions/Current/Resources/airport")
            exe = "airport" if shutil.which("airport") else (ap if os.path.exists(ap) else None)
            if not exe:
                return {"ok": False, "error": "airport tool not found"}
            r = subprocess.run([exe, "-I"], capture_output=True, text=True, timeout=8)
            info = {}
            for line in (r.stdout or "").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
            return {"ok": True, "ssid": info.get("SSID"), "rssi": info.get("agrCtlRSSI"),
                    "channel": info.get("channel")}
        if sys.platform.startswith("win"):
            r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                               capture_output=True, text=True, timeout=8)
            info = {}
            for line in (r.stdout or "").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip().lower()] = v.strip()
            return {"ok": True, "ssid": info.get("ssid"), "signal": info.get("signal"),
                    "state": info.get("state")}
        if shutil.which("iwgetid"):
            r = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=8)
            return {"ok": True, "ssid": (r.stdout or "").strip() or None}
        return {"ok": False, "error": "no Wi-Fi tool available on this platform"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
