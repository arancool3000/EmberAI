"""Emergency lockdown — Ember's "panic button" / hard safety boundary.

A cloud agent can't really contain itself; a LOCAL one can. On trigger — a button, or an armed
auto-response to a CRITICAL threat — Ember instantly: stops its own AI activity, cuts the
network, and locks the screen. That contains a compromise (or a runaway agent) in seconds.

Every OS action goes through an injectable hook (set_hooks) so it's unit-testable and so the app
can plug in its own "stop the agent" routine. Best-effort + isolated: each step runs on its own
and reports ok/failed; one failure never blocks the others, and nothing raises.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time

_LOCK = threading.RLock()
_armed = False                      # auto-lockdown on a critical threat (off by default)
_last_event: dict | None = None
_last_auto_at = 0.0
_AUTO_MIN_INTERVAL = 20.0           # don't auto-fire more than once per ~20s


# ---------------------------------------------------------------------------
# OS actions (best-effort; overridable via set_hooks for the app + tests)
# ---------------------------------------------------------------------------
def _run(cmd: list, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.returncode == 0, (r.stderr or r.stdout or "").strip())
    except Exception as e:
        return (False, str(e))


def _wifi_devices() -> list:
    """macOS Wi-Fi hardware ports (usually ['en0'])."""
    ok, out = _run(["networksetup", "-listallhardwareports"])
    if not ok:
        return ["en0"]
    devs, take = [], False
    for line in out.splitlines():
        if "Wi-Fi" in line or "AirPort" in line:
            take = True
        elif take and line.strip().startswith("Device:"):
            devs.append(line.split(":", 1)[1].strip()); take = False
    return devs or ["en0"]


def _default_lock_screen() -> dict:
    if sys.platform == "darwin":
        # Control+Command+Q is the OS-native "Lock Screen" shortcut - reliable across modern
        # macOS versions (needs Accessibility, which Ember already requests for automation).
        # CGSession -suspend used to be the standard trick but its path has broken/gone missing
        # on newer macOS releases, and used to silently fall through straight to
        # `pmset displaysleepnow` - visually similar (screen goes black) but NOT a real lock (no
        # guaranteed re-authentication), so "Lock PC" was quietly just sleeping the display.
        ok, d = _run(["osascript", "-e",
                      'tell application "System Events" to keystroke "q" using {control down, command down}'])
        if ok:
            return {"ok": True, "detail": d or "screen locked"}
        ok, d = _run(["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                      "-suspend"])
        if ok:
            return {"ok": True, "detail": d or "screen locked"}
        # Last resort: only puts the DISPLAY to sleep, not a real lock - report this honestly
        # (ok=False) instead of claiming "locked" when the session wasn't actually secured.
        _run(["pmset", "displaysleepnow"])
        return {"ok": False, "detail": "could not lock the screen (grant Ember Accessibility "
                                        "access) - put the display to sleep instead"}
    if sys.platform.startswith("win"):
        ok, d = _run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return {"ok": ok, "detail": d or "workstation locked"}
    ok, d = _run(["loginctl", "lock-session"])
    if not ok:
        ok, d = _run(["xdg-screensaver", "lock"])
    return {"ok": ok, "detail": d or "session locked"}


def _network_services(want_disabled: bool) -> list:
    """macOS network SERVICE names (Wi-Fi, Ethernet, Thunderbolt Bridge, …). `want_disabled`
    filters to services currently disabled (True) or enabled (False) - a leading '*' in
    `networksetup -listallnetworkservices` marks a disabled one."""
    ok, out = _run(["networksetup", "-listallnetworkservices"])
    if not ok:
        return []
    svcs = []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("an asterisk"):
            continue
        is_disabled = s.startswith("*")
        name = s.lstrip("*").strip()
        if name and (is_disabled == want_disabled):
            svcs.append(name)
    return svcs


def _win_interfaces() -> list:
    """All non-loopback Windows network interface names, so a panic cut disables EVERY adapter,
    not just one hardcoded 'Wi-Fi' (which is also wrong on non-English Windows)."""
    ok, out = _run(["netsh", "interface", "show", "interface"])
    if not ok:
        return ["Wi-Fi"]
    names = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0] in ("Enabled", "Disabled") and \
                parts[2] in ("Dedicated", "Internal"):
            name = parts[3].strip()
            if name and "loopback" not in name.lower():
                names.append(name)
    return names or ["Wi-Fi"]


def _set_network(enable: bool) -> dict:
    state = "on" if enable else "off"
    if sys.platform == "darwin":
        # A panic 'cut' that only kills Wi-Fi leaves Ethernet/Thunderbolt (and a live C2 channel)
        # fully up while reporting success. Power Wi-Fi off (fast, no admin) AND enable/disable
        # every WIRED service too.
        results = [_run(["networksetup", "-setairportpower", dev, state]) for dev in _wifi_devices()]
        for svc in _network_services(want_disabled=enable):  # cut: disable currently-enabled; restore: enable disabled
            results.append(_run(["networksetup", "-setnetworkserviceenabled", svc, state]))
        ok = any(r[0] for r in results)
        return {"ok": ok, "detail": (f"all network interfaces {state}" if ok
                                     else "; ".join(r[1] for r in results)[:200])}
    if sys.platform.startswith("win"):
        admin = "enable" if enable else "disable"
        results = [_run(["netsh", "interface", "set", "interface", name, f"admin={admin}"])
                   for name in _win_interfaces()]
        ok = any(r[0] for r in results)
        detail = (f"all interfaces {admin}" if ok
                  else ("; ".join(r[1] for r in results)[:180] + " (needs admin)"))
        return {"ok": ok, "detail": detail}
    ok, d = _run(["nmcli", "networking", "on" if enable else "off"])
    return {"ok": ok, "detail": d or f"networking {state}"}


def _default_cut_network() -> dict:
    return _set_network(False)


def _default_restore_network() -> dict:
    return _set_network(True)


def _default_kill_ai() -> dict:
    """Best-effort: stop the local LLM (Ollama). The app overrides this to ALSO stop Ember's
    own running agent turn."""
    if sys.platform.startswith("win"):
        ok, d = _run(["taskkill", "/IM", "ollama.exe", "/F"])
    else:
        ok, d = _run(["pkill", "-f", "ollama"])
    return {"ok": ok, "detail": d or "stopped local AI"}


_HOOKS = {
    "lock_screen": _default_lock_screen,
    "cut_network": _default_cut_network,
    "restore_network": _default_restore_network,
    "kill_ai": _default_kill_ai,
}


def set_hooks(**hooks) -> None:
    """Override OS actions. The app sets kill_ai (to stop its agent); tests inject recorders.
    Unknown keys are ignored; pass None to reset a key to its default."""
    defaults = {"lock_screen": _default_lock_screen, "cut_network": _default_cut_network,
                "restore_network": _default_restore_network, "kill_ai": _default_kill_ai}
    for k, fn in hooks.items():
        if k in _HOOKS:
            _HOOKS[k] = fn if callable(fn) else defaults[k]


# ---------------------------------------------------------------------------
# Arming the auto-response
# ---------------------------------------------------------------------------
def arm_auto(enabled: bool) -> None:
    global _armed
    _armed = bool(enabled)


def is_armed() -> bool:
    return _armed


def last_event() -> dict | None:
    return _last_event


# ---------------------------------------------------------------------------
# The lockdown
# ---------------------------------------------------------------------------
def _call(hook: str) -> dict:
    fn = _HOOKS.get(hook)
    if not fn:
        return {"ok": False, "detail": "no handler"}
    try:
        res = fn()
        if isinstance(res, dict):
            return {"ok": bool(res.get("ok")), "detail": str(res.get("detail", ""))[:300]}
        return {"ok": bool(res), "detail": ""}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def panic_lockdown(lock: bool = True, cut_network: bool = True, kill_ai: bool = True,
                   reason: str = "", source: str = "manual", _ts: float | None = None) -> dict:
    """Engage the lockdown. Runs the requested steps (stop AI, cut network, lock screen)
    best-effort and returns what happened. `source` is 'manual' or 'auto'."""
    global _last_event
    actions = []
    # Order: stop the AI first (halt any in-flight action), then cut the network, then lock.
    if kill_ai:
        actions.append({"action": "stop_ai", **_call("kill_ai")})
    if cut_network:
        actions.append({"action": "cut_network", **_call("cut_network")})
    if lock:
        actions.append({"action": "lock_screen", **_call("lock_screen")})
    event = {
        "ok": True, "source": source, "reason": reason or "manual lockdown",
        "at": (_ts if _ts is not None else time.time()),
        "actions": actions,
        "succeeded": [a["action"] for a in actions if a["ok"]],
        "failed": [a["action"] for a in actions if not a["ok"]],
    }
    with _LOCK:
        _last_event = event
    return event


def restore_network() -> dict:
    """Re-enable networking after a lockdown."""
    return {"ok": True, "result": _call("restore_network")}


def maybe_auto_panic(severity: str, detail: str = "", category: str = "",
                     _ts: float | None = None) -> dict | None:
    """Called by the security layer on each finding. If auto-lockdown is ARMED and this is a
    CRITICAL (malicious) threat, engage the lockdown (debounced). Returns the event or None."""
    global _last_auto_at
    if not _armed or severity != "malicious":
        return None
    now = _ts if _ts is not None else time.time()
    with _LOCK:
        if now - _last_auto_at < _AUTO_MIN_INTERVAL:
            return None
        _last_auto_at = now
    return panic_lockdown(reason=f"auto: {category or 'threat'} — {detail}"[:200],
                          source="auto", _ts=now)


def status() -> dict:
    return {"ok": True, "armed": _armed, "last_event": _last_event}
