"""Ember entry point."""
import faulthandler
import sys
import traceback

# If a native library segfaults (e.g. a macOS event tap / Quartz call), dump a Python
# traceback to stderr instead of dying silently with "python3 quit unexpectedly". This
# prints the exact module/function that triggered the crash, right in the terminal.
faulthandler.enable()


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
    try:
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
