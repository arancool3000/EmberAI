"""Hermetic tests for panic.py — the emergency-lockdown orchestration, arming, debounce, and
restore. All OS actions are replaced with recording hooks, so nothing real happens.
Run: python test_panic.py"""
import panic

_REAL_PLATFORM = panic.sys.platform
_REAL_RUN = panic._run


def _recorder():
    calls = []
    def mk(name, ok=True):
        def fn():
            calls.append(name)
            return {"ok": ok, "detail": f"{name} done"}
        return fn
    return calls, mk


def _install(ok=True):
    calls, mk = _recorder()
    panic.set_hooks(lock_screen=mk("lock", ok), cut_network=mk("cut", ok),
                    restore_network=mk("restore", ok), kill_ai=mk("kill", ok))
    return calls


def _reset():
    panic.set_hooks(lock_screen=None, cut_network=None, restore_network=None, kill_ai=None)
    panic.arm_auto(False)
    panic._last_auto_at = 0.0


def test_manual_lockdown_runs_all_steps_in_order():
    calls = _install()
    r = panic.panic_lockdown(reason="test", source="manual", _ts=1000.0)
    assert r["ok"] and r["source"] == "manual"
    assert calls == ["kill", "cut", "lock"]          # AI first, then network, then screen
    assert set(r["succeeded"]) == {"stop_ai", "cut_network", "lock_screen"}
    assert r["failed"] == []
    _reset()


def test_selective_steps():
    calls = _install()
    panic.panic_lockdown(lock=False, cut_network=False, kill_ai=True, _ts=1.0)
    assert calls == ["kill"]
    _reset()


def test_failed_step_is_reported_not_raised():
    _install(ok=False)
    r = panic.panic_lockdown(_ts=2.0)
    assert r["ok"] is True                            # the call itself never fails
    assert set(r["failed"]) == {"stop_ai", "cut_network", "lock_screen"}
    assert r["succeeded"] == []
    _reset()


def test_auto_panic_requires_armed_and_critical():
    calls = _install()
    # not armed -> nothing
    assert panic.maybe_auto_panic("malicious", "evil", _ts=10.0) is None
    assert calls == []
    panic.arm_auto(True)
    # armed but only suspicious -> nothing
    assert panic.maybe_auto_panic("suspicious", "meh", _ts=11.0) is None
    assert calls == []
    # armed + malicious -> fires (use a realistic large timestamp so it's outside the debounce
    # window relative to the reset baseline of 0.0)
    ev = panic.maybe_auto_panic("malicious", "reverse shell", category="network", _ts=10000.0)
    assert ev and ev["source"] == "auto" and "reverse shell" in ev["reason"]
    assert calls == ["kill", "cut", "lock"]
    _reset()


def test_auto_panic_debounced():
    calls = _install()
    panic.arm_auto(True)
    assert panic.maybe_auto_panic("malicious", "a", _ts=100.0) is not None
    # within the debounce window -> suppressed
    assert panic.maybe_auto_panic("malicious", "b", _ts=105.0) is None
    # after the window -> fires again
    assert panic.maybe_auto_panic("malicious", "c", _ts=100.0 + panic._AUTO_MIN_INTERVAL + 1) is not None
    _reset()


def _fake_run_sequence(results):
    """results: {cmd_prefix_tuple: (ok, detail)} - matched by the command's first 1-2 args."""
    calls = []
    def fake(cmd, timeout=8.0):
        calls.append(cmd)
        for prefix, res in results.items():
            if tuple(cmd[:len(prefix)]) == prefix:
                return res
        return (False, "unexpected command")
    return calls, fake


def test_default_lock_screen_prefers_the_lock_shortcut_on_macos():
    # Control+Command+Q (the OS-native Lock Screen shortcut) must be tried FIRST and, if it
    # works, CGSession must never even be invoked.
    panic.sys.platform = "darwin"
    calls, panic._run = _fake_run_sequence({("osascript",): (True, "")})
    try:
        r = panic._default_lock_screen()
        assert r == {"ok": True, "detail": "screen locked"}
        assert len(calls) == 1 and calls[0][0] == "osascript"
    finally:
        panic.sys.platform, panic._run = _REAL_PLATFORM, _REAL_RUN


def test_default_lock_screen_falls_back_to_cgsession():
    panic.sys.platform = "darwin"
    calls, panic._run = _fake_run_sequence({
        ("osascript",): (False, "not authorized"),
        ("/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",): (True, ""),
    })
    try:
        r = panic._default_lock_screen()
        assert r["ok"] is True
        assert [c[0] for c in calls] == [
            "osascript",
            "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"]
    finally:
        panic.sys.platform, panic._run = _REAL_PLATFORM, _REAL_RUN


def test_default_lock_screen_honestly_reports_failure_instead_of_claiming_locked():
    # Regression guard: this used to silently fall through to `pmset displaysleepnow` (puts the
    # DISPLAY to sleep, not a real lock - no guaranteed re-authentication) and still report
    # ok=True/"screen locked", so "Lock PC" was quietly just sleeping the display. Now it must
    # report ok=False and must NOT claim the screen was locked.
    panic.sys.platform = "darwin"
    calls, panic._run = _fake_run_sequence({
        ("osascript",): (False, "not authorized"),
        ("/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",): (False, "no such file"),
        ("pmset",): (True, ""),
    })
    try:
        r = panic._default_lock_screen()
        assert r["ok"] is False
        assert "locked" not in r["detail"].lower() or "could not lock" in r["detail"].lower()
        assert [c[0] for c in calls] == [
            "osascript",
            "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
            "pmset"]
    finally:
        panic.sys.platform, panic._run = _REAL_PLATFORM, _REAL_RUN


def test_cut_network_disables_wired_services_not_just_wifi_on_macos():
    # A panic cut that only kills Wi-Fi leaves Ethernet/Thunderbolt (and a live C2 channel) up
    # while reporting success. It must disable EVERY network service.
    panic.sys.platform = "darwin"
    calls = []
    def fake_run(cmd, timeout=8.0):
        calls.append(cmd)
        if cmd[:2] == ["networksetup", "-listallhardwareports"]:
            return (True, "Hardware Port: Wi-Fi\nDevice: en0\n")
        if cmd[:2] == ["networksetup", "-listallnetworkservices"]:
            return (True, "An asterisk (*) denotes a disabled service.\n"
                          "Wi-Fi\nEthernet\nThunderbolt Bridge\n")
        return (True, "")
    panic._run = fake_run
    try:
        r = panic._default_cut_network()
        assert r["ok"] is True
        joined = [" ".join(c) for c in calls]
        assert any("setairportpower en0 off" in j for j in joined)                 # Wi-Fi off
        assert any("-setnetworkserviceenabled Ethernet off" in j for j in joined)   # wired off too
        assert any("-setnetworkserviceenabled Thunderbolt Bridge off" in j for j in joined)
    finally:
        panic._run, panic.sys.platform = _REAL_RUN, _REAL_PLATFORM


def test_cut_network_disables_all_windows_interfaces_not_just_wifi():
    panic.sys.platform = "win32"
    calls = []
    def fake_run(cmd, timeout=8.0):
        calls.append(cmd)
        if cmd[:4] == ["netsh", "interface", "show", "interface"]:
            return (True,
                    "Admin State    State          Type             Interface Name\n"
                    "-------------------------------------------------------------\n"
                    "Enabled        Connected      Dedicated        Ethernet\n"
                    "Enabled        Connected      Dedicated        Wi-Fi\n"
                    "Enabled        Connected      Loopback         Loopback Pseudo-Interface 1\n")
        return (True, "")
    panic._run = fake_run
    try:
        r = panic._default_cut_network()
        assert r["ok"] is True
        joined = [" ".join(c) for c in calls]
        assert any("set interface Ethernet admin=disable" in j for j in joined)
        assert any("set interface Wi-Fi admin=disable" in j for j in joined)
        assert not any("Loopback" in j for j in joined)   # loopback is skipped
    finally:
        panic._run, panic.sys.platform = _REAL_RUN, _REAL_PLATFORM


def test_restore_and_status():
    calls = _install()
    panic.arm_auto(True)
    panic.restore_network()
    assert "restore" in calls
    st = panic.status()
    assert st["ok"] and st["armed"] is True
    _reset()
    assert panic.status()["armed"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} panic tests passed")
