"""Multitool utilities for Ember: quick system / network / security helpers that
make Ember more of a Swiss-army tool than just a chat agent. All read-only and local.
"""
from __future__ import annotations

import math
import os
import time
from pathlib import Path


def _tree_size(p: Path) -> int:
    if p.is_file():
        try:
            return p.stat().st_size
        except Exception:
            return 0
    total = 0
    for dirpath, _dirs, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except Exception:
                pass
    return total


def disk_usage(path: str = ".", top: int = 20) -> dict:
    """Biggest files & folders directly under a path (a quick 'du'). Read-only."""
    try:
        root = Path(path).expanduser()
        if not root.exists():
            return {"ok": False, "error": f"not found: {path}"}
        entries = []
        if root.is_dir():
            for child in root.iterdir():
                try:
                    entries.append((_tree_size(child), child.name, child.is_dir()))
                except Exception:
                    continue
        else:
            entries.append((root.stat().st_size, root.name, False))
        entries.sort(reverse=True)
        items = [{"name": n + ("/" if d else ""), "size_mb": round(s / 1048576, 2)}
                 for s, n, d in entries[:max(1, int(top))]]
        return {"ok": True, "path": str(root),
                "total_mb": round(sum(s for s, _, _ in entries) / 1048576, 2),
                "items": items}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_open_ports() -> dict:
    """Listening TCP/UDP ports on this machine and the owning process (a quick security
    check for unexpected services)."""
    try:
        import psutil
    except Exception:
        return {"ok": False, "error": "psutil not available"}
    try:
        rows = {}
        for c in psutil.net_connections(kind="inet"):
            listening = (c.status == getattr(psutil, "CONN_LISTEN", "LISTEN")) or \
                        (getattr(c, "type", None) == 2 and not c.raddr)  # UDP bound
            if not listening or not c.laddr:
                continue
            addr = f"{c.laddr.ip}:{c.laddr.port}"
            proto = "tcp" if c.type == 1 else "udp"
            pname = ""
            if c.pid:
                try:
                    pname = psutil.Process(c.pid).name()
                except Exception:
                    pname = ""
            rows[(addr, proto, c.pid)] = {"addr": addr, "proto": proto,
                                          "pid": c.pid, "process": pname}
        out = sorted(rows.values(), key=lambda r: r["addr"])
        return {"ok": True, "count": len(out), "listening": out}
    except Exception as e:
        return {"ok": False, "error": f"could not enumerate ports (may need privileges): {e}"}


_COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "letmein", "admin",
    "welcome", "iloveyou", "111111", "000000", "abc123", "monkey",
}


def _human_time(s: float) -> str:
    if s < 1:
        return "instant"
    for unit, n in (("years", 31557600), ("days", 86400), ("hours", 3600), ("minutes", 60)):
        if s >= n:
            return f"~{s / n:.1f} {unit}"
    return f"~{s:.0f} seconds"


def password_strength(password: str) -> dict:
    """Estimate a password's strength (entropy + rough offline crack time). Local only —
    nothing is sent anywhere."""
    pw = password or ""
    if not pw:
        return {"ok": False, "error": "empty password"}
    pool = 0
    if any(c.islower() for c in pw):
        pool += 26
    if any(c.isupper() for c in pw):
        pool += 26
    if any(c.isdigit() for c in pw):
        pool += 10
    if any(not c.isalnum() for c in pw):
        pool += 33
    pool = pool or 1
    bits = round(len(pw) * math.log2(pool), 1)
    secs = (2 ** bits / 2) / 1e10  # ~10B guesses/sec (offline GPU)
    if pw.lower() in _COMMON_PASSWORDS:
        rating = "very weak (common password)"
    elif bits < 40:
        rating = "weak"
    elif bits < 60:
        rating = "fair"
    elif bits < 80:
        rating = "strong"
    else:
        rating = "very strong"
    return {"ok": True, "length": len(pw), "entropy_bits": bits,
            "rating": rating, "est_offline_crack": _human_time(secs)}


def system_health() -> dict:
    """Quick health snapshot: uptime, CPU, memory, and disk usage."""
    try:
        import psutil
    except Exception:
        return {"ok": False, "error": "psutil not available"}
    try:
        up = time.time() - psutil.boot_time()
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(os.path.expanduser("~"))
        return {"ok": True,
                "uptime_hours": round(up / 3600, 1),
                "cpu_percent": psutil.cpu_percent(interval=0.3),
                "memory_percent": vm.percent,
                "memory_used_gb": round(vm.used / 1073741824, 1),
                "memory_total_gb": round(vm.total / 1073741824, 1),
                "disk_percent": du.percent,
                "disk_free_gb": round(du.free / 1073741824, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
