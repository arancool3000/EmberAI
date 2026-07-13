"""Aggregate security check — one call that summarizes Ember's protections and posture."""
from __future__ import annotations


def security_checkup() -> dict:
    """Summarize protection status (antivirus, web protection, sandbox) + a simple score."""
    report: dict = {"ok": True}

    try:
        import antivirus
        st = antivirus.security_status()
        report["antivirus"] = {
            "engines": st.get("engines_available", []),
            "sandbox": st.get("sandbox_available"),
            "quarantine_items": st.get("quarantine_count", 0),
            "fileless_protection": st.get("fileless_monitor_running", False),
            "ioc_scan": st.get("ioc_scan", False),
        }
    except Exception as e:
        report["antivirus"] = {"error": str(e)}

    try:
        import fileless_guard
        fs = fileless_guard.fileless_guard_status()
        report["realtime_protection"] = {
            "fileless_monitor_running": bool(fs.get("running")),
            "processes_scanned": fs.get("processes_scanned", 0),
            "threats_found": fs.get("threats_found", 0),
        }
    except Exception as e:
        report["realtime_protection"] = {"error": str(e)}

    try:
        import download_guard
        report["realtime_protection"] = {
            **report.get("realtime_protection", {}),
            "download_monitor_running": download_guard.is_running(),
        }
    except Exception:
        pass

    try:
        import security_center
        sc = security_center.security_center_status()
        report["security_center"] = {
            "running": bool(sc.get("running")),
            "scan_cycles": sc.get("scan_cycles", 0),
            "threats_found": sc.get("threats_found", 0),
            "by_source": sc.get("by_source", {}),
        }
    except Exception as e:
        report["security_center"] = {"error": str(e)}

    try:
        import web_policy
        wp = web_policy.get_config()
        report["web_protection"] = {"enabled": bool(wp.get("enabled", False)),
                                    "online_reputation": bool(wp.get("online_reputation", False))}
    except Exception as e:
        report["web_protection"] = {"error": str(e)}

    try:
        import nettools
        c = nettools.network_connections()
        report["active_connections"] = c.get("count") if c.get("ok") else None
    except Exception:
        report["active_connections"] = None

    av = report.get("antivirus", {})
    wp = report.get("web_protection", {})
    rt = report.get("realtime_protection", {})
    scn = report.get("security_center", {})
    score = 0
    if av.get("engines"):
        score += 25
    if av.get("sandbox"):
        score += 10
    if av.get("fileless_protection") or rt.get("fileless_monitor_running"):
        score += 15  # always-on behavioral/fileless monitor
    if rt.get("download_monitor_running"):
        score += 5
    if scn.get("running"):
        score += 20  # unified always-on active scanning (network + persistence + sweeps)
    if wp.get("enabled"):
        score += 15
    if wp.get("online_reputation"):
        score += 10
    report["score"] = score
    report["rating"] = ("strong" if score >= 80 else "fair" if score >= 50 else "needs attention")
    recs = []
    if not av.get("engines"):
        recs.append("No scan engine detected — heuristics still apply; VirusTotal adds cloud lookups.")
    if not scn.get("running"):
        recs.append("Turn on the always-on Security Center in Settings → Security for continuous "
                    "scanning of processes, files, network and persistence.")
    if not (av.get("fileless_protection") or rt.get("fileless_monitor_running")):
        recs.append("Turn on real-time fileless protection in Settings → Security (always-active "
                    "process monitor for in-memory / LOLBin attacks).")
    if not wp.get("enabled"):
        recs.append("Turn on Web protection in Settings → Security.")
    if not av.get("sandbox"):
        recs.append("Install Docker (or rely on the built-in macOS sandbox) for safer 'run in sandbox'.")
    report["recommendations"] = recs
    return report
