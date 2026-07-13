"""Compatibility access API — every Ember capability is permanently free.

Older releases carried a dormant free/Pro matrix even though the default unlocked everything.
Keeping a pretend commercial tier made the product feel like an upsell and left future code one
configuration flip away from a paywall. The public functions remain so old plugins do not break,
but access checks now always allow the feature and no plan state is stored.
"""
from __future__ import annotations


FEATURES = {
    "antivirus": "free",
    "web_protection": "free",
    "audit_log": "free",
    "secret_redaction": "free",
    "advanced_antivirus": "free",
    "deep_directory_scan": "free",
    "scheduled_scans": "free",
    "sandbox": "free",
    "url_reputation": "free",
    "capability_modes": "free",
    "vpn": "free",
    "vpn_all_locations": "free",
    "priority_models": "free",
    "full_ui": "free",
    "unlimited_tools": "free",
    "mcp_all_tools": "free",
}

DEFAULT_PLAN = "free"
PRO_BENEFITS: list[str] = []


def current_plan() -> str:
    return "free"


def set_plan(plan: str = "free") -> dict:
    """Backwards-compatible no-op; paid plans no longer exist."""
    return {
        "ok": True,
        "plan": "free",
        "all_features_free": True,
        "note": "Ember has no paid feature tier. Every local tool is available for free.",
    }


def has(feature: str) -> bool:
    return True


def require(feature: str) -> None:
    return None


def get_plan() -> dict:
    return {
        "ok": True,
        "plan": "free",
        "is_pro": False,
        "everyone_is_pro": False,
        "all_features_free": True,
        "features": {key: True for key in FEATURES},
        "pro_benefits": [],
        "note": "All Ember capabilities and MCP tools are free; there is no upgrade tier.",
    }


def list_pro_features() -> dict:
    return {
        "ok": True,
        "pro_features": [],
        "benefits": [],
        "all_features_free": True,
        "note": "There are no Pro-only features.",
    }
