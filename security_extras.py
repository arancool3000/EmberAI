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
        }
    except Exception as e:
        report["antivirus"] = {"error": str(e)}

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
    score = 0
    if av.get("engines"):
        score += 40
    if av.get("sandbox"):
        score += 20
    if wp.get("enabled"):
        score += 30
    if wp.get("online_reputation"):
        score += 10
    report["score"] = score
    report["rating"] = ("strong" if score >= 80 else "fair" if score >= 50 else "needs attention")
    recs = []
    if not av.get("engines"):
        recs.append("No scan engine detected — heuristics still apply; VirusTotal adds cloud lookups.")
    if not wp.get("enabled"):
        recs.append("Turn on Web protection in Settings → Security.")
    if not av.get("sandbox"):
        recs.append("Install Docker (or rely on the built-in macOS sandbox) for safer 'run in sandbox'.")
    report["recommendations"] = recs
    return report
