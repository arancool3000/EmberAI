"""Ember entry point."""
import faulthandler
import sys
import traceback

# If a native library segfaults (e.g. a macOS event tap / Quartz call), dump a Python
# traceback to stderr instead of dying silently with "python3 quit unexpectedly". This
# prints the exact module/function that triggered the crash, right in the terminal.
faulthandler.enable()


def _ensure_valid_cwd():
    """Guard against a deleted/invalid working directory.

    If the process's current working directory no longer exists (the launch folder was
    moved, renamed, or cleaned up — common with the unsigned-app / updater flow), any
    library that internally calls os.getcwd() — httpx/anyio inside the model SDK — raises a
    bare 'FileNotFoundError: [Errno 2] No such file or directory' (note: no filename). That
    surfaced as 'Agent init failed — check your API key'. Repair it by switching to a
    directory that is guaranteed to exist."""
    import os
    try:
        os.getcwd()
        return
    except Exception:
        pass
    for d in (os.path.expanduser("~"), "/"):
        try:
            os.chdir(d)
            return
        except Exception:
            continue


def _fix_gui_path():
    """A macOS app launched from Finder inherits only a minimal PATH (/usr/bin:/bin:…), so
    Homebrew dirs are missing. That breaks two things in-process:
      • shutil.which('flac') (used by SpeechRecognition) can't find a NATIVE flac, so it falls
        back to the bundled flac-mac — which on a downloaded build is often the wrong CPU type
        ('[Errno 86] Bad CPU type'). With brew's flac on PATH it uses that instead.
      • any tool that shells out to brew/ffmpeg/etc.
    So we prepend the standard Homebrew + system dirs to THIS process's PATH at startup."""
    import os
    if sys.platform.startswith("win"):
        return
    extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin",
             "/usr/local/bin", "/usr/local/sbin",
             "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    merged = []
    for d in extra + os.environ.get("PATH", "").split(os.pathsep):
        if d and d not in merged:
            merged.append(d)
    os.environ["PATH"] = os.pathsep.join(merged)


def _set_taskbar_app_id():
    """Tell Windows that this process is its own app (not just generic pythonw.exe).
    Required so the taskbar groups Ember separately, uses the Ember icon, and lets
    the user pin it as a distinct app from other Python processes."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ember.AIAgent.1")
    except Exception:
        pass


def _reset_tcc_if_new_build():
    """macOS keys Screen Recording / Accessibility grants to the app's exact code signature.
    A rebuilt .app has a NEW signature, so the old grant goes stale — it LOOKS granted but
    silently fails. Detect a changed binary (new build) and clear those grants so macOS
    re-prompts and the new build actually gets working permissions. Only for the frozen .app."""
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return
    try:
        import subprocess
        from pathlib import Path
        exe = Path(sys.executable)
        st = exe.stat()
        fp = f"{st.st_size}-{int(st.st_mtime)}"  # changes whenever the binary is rebuilt
        support = Path.home() / "Library" / "Application Support" / "Ember"
        support.mkdir(parents=True, exist_ok=True)
        marker = support / "build_fingerprint.txt"
        prev = marker.read_text().strip() if marker.exists() else ""
        if prev == fp:
            return  # same build — existing permissions are still valid
        for service in ("ScreenCapture", "Accessibility"):
            subprocess.run(["tccutil", "reset", service, "com.ember.aiagent"],
                           capture_output=True, timeout=10)
        marker.write_text(fp)
        subprocess.run(["osascript", "-e",
            "display notification \"New version detected — re-grant Screen Recording and "
            "Accessibility in System Settings, then reopen Ember.\" with title \"Ember\""],
            capture_output=True, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    # Quit-proof global hotkey: a frozen build re-runs ITSELF in listener-only mode
    # (installed by hotkey_daemon). Handle that before any heavy GUI imports.
    if "--hotkey-listener" in sys.argv:
        import ember_hotkey_listener
        _combo = "ctrl+shift+space"
        if "--combo" in sys.argv:
            try:
                _combo = sys.argv[sys.argv.index("--combo") + 1]
            except Exception:
                pass
        sys.exit(ember_hotkey_listener.run(_combo))
    try:
        _ensure_valid_cwd()   # repair a deleted CWD before anything calls os.getcwd()
        _fix_gui_path()       # put Homebrew on PATH so flac/brew resolve when launched from Finder
        _set_taskbar_app_id()
        # If this is a freshly-built .app, clear stale macOS permission grants so they re-prompt.
        _reset_tcc_if_new_build()
        # Single-instance check + kill any leftover DebugAI processes first.
        from single_instance import acquire_or_summon, kill_old_debugai
        kill_old_debugai()
        listener = acquire_or_summon()
        if listener is None:
            # Another Ember is already running; we've already told it to summon itself.
            sys.exit(0)

        # Purge any quarantined files past their auto-delete grace period.
        try:
            import antivirus
            antivirus.startup()
        except Exception:
            pass

        from ui import main
        main(instance_listener=listener)
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Run install.bat first, or: pip install -r requirements.txt")
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        input("\nPress Enter to exit…")
        sys.exit(1)
