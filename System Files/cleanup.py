"""System cleanup: reclaim space from temp/cache, inspect startup items.
Destructive actions default to a dry run."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


def _temp_dirs() -> list[Path]:
    dirs = [Path(tempfile.gettempdir())]
    home = Path.home()
    if sys.platform == "darwin":
        dirs += [home / "Library" / "Caches", home / "Library" / "Logs"]
    elif sys.platform.startswith("win"):
        for e in ("TEMP", "TMP"):
            if os.environ.get(e):
                dirs.append(Path(os.environ[e]))
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    else:
        dirs += [home / ".cache"]
    seen, out = [], []
    for d in dirs:
        try:
            rd = d.resolve()
        except Exception:
            rd = d
        if d.exists() and rd not in seen:
            seen.append(rd)
            out.append(d)
    return out


def clean_temp(dry_run: bool = True, max_age_days: int = 0) -> dict:
    """Scan temp/cache dirs and report reclaimable space. With dry_run=False, delete
    files older than max_age_days (0 = all eligible). Files modified in the last hour
    are always skipped (they may be in use)."""
    cutoff = (time.time() - max_age_days * 86400) if max_age_days else None
    total = files = freed = deleted = errors = 0
    dirs = _temp_dirs()
    for d in dirs:
        for dirpath, _dirs, fs in os.walk(d, onerror=lambda e: None):
            for f in fs:
                p = Path(dirpath) / f
                try:
                    st = p.stat()
                except Exception:
                    continue
                if time.time() - st.st_mtime < 3600:
                    continue
                if cutoff and st.st_mtime > cutoff:
                    continue
                total += st.st_size
                files += 1
                if not dry_run:
                    try:
                        p.unlink()
                        freed += st.st_size
                        deleted += 1
                    except Exception:
                        errors += 1
    return {"ok": True, "dry_run": dry_run, "scanned_dirs": [str(x) for x in dirs],
            "candidate_files": files, "reclaimable_mb": round(total / 1048576, 2),
            "deleted_files": deleted, "freed_mb": round(freed / 1048576, 2), "errors": errors}


def list_startup_items() -> dict:
    """List login/startup items (read-only)."""
    items = []
    home = Path.home()
    if sys.platform == "darwin":
        for d in (home / "Library/LaunchAgents", Path("/Library/LaunchAgents"),
                  Path("/Library/LaunchDaemons")):
            if d.exists():
                for p in d.glob("*.plist"):
                    items.append({"name": p.stem, "path": str(p), "scope": "launchd"})
    elif sys.platform.startswith("win"):
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
        if startup.exists():
            for p in startup.iterdir():
                items.append({"name": p.name, "path": str(p), "scope": "startup-folder"})
        try:
            import winreg
            for hive, root in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                               (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
                try:
                    k = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Run")
                    i = 0
                    while True:
                        try:
                            name, val, _ = winreg.EnumValue(k, i)
                            i += 1
                            items.append({"name": name, "path": val, "scope": f"{root}\\Run"})
                        except OSError:
                            break
                except Exception:
                    pass
        except Exception:
            pass
    else:
        for d in (home / ".config/autostart", Path("/etc/xdg/autostart")):
            if d.exists():
                for p in d.glob("*.desktop"):
                    items.append({"name": p.stem, "path": str(p), "scope": "autostart"})
    return {"ok": True, "count": len(items), "items": items[:200]}
