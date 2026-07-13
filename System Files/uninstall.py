"""Uninstall Ember — cleanly remove Ember from this computer.

Two jobs, both callable by the agent:
  * find_instances()      — read-only: list every Ember install + data dir + login item found.
  * uninstall_instance()  — remove ONE specific Ember install (and, optionally, its data).
  * uninstall_all()       — remove EVERY Ember instance + data dir + login item.

Safety, because this deletes files:
  * DRY-RUN by default — nothing is removed unless confirm=True; otherwise you get the plan.
  * Trash-first — items go to the OS Trash/Recycle Bin (reversible) when send2trash is available,
    falling back to a real delete only if it isn't.
  * Marker-gated — a path is only ever removed if it's a REAL Ember install (contains main.py /
    System Files/main.py, or is an Ember.app), or is Ember's own data dir / login item. System
    and home roots, and anything too shallow, are always refused.
  * The currently-running install is reported but NOT deleted out from under the live process
    unless you explicitly pass include_running=True.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    """Ember's per-user data dir (settings/vault/usage/etc.) — matches the other modules."""
    from app_data import data_dir
    return data_dir()


def _base_dir() -> Path:
    """Directory of the running code (System Files/ in a source run, the bundle dir when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _running_install_root() -> Path:
    """The install FOLDER of the running Ember (the thing a user would delete).

    Source layout: <repo>/System Files/main.py -> the repo root is the install.
    Old flat layout: <dir>/main.py -> that dir. Frozen .app: the .app bundle."""
    try:
        import autostart
        app = autostart._macos_app_bundle()
        if app is not None:
            return app
    except Exception:
        pass
    base = _base_dir()
    if base.name == "System Files" and (base.parent / "System Files" / "main.py").exists():
        return base.parent
    return base


def _looks_like_ember_install(p: Path) -> bool:
    """True only for a real Ember install root — the marker check that keeps removal honest."""
    try:
        if not p.exists():
            return False
        if p.suffix == ".app" and "ember" in p.name.lower():
            return True
        if (p / "main.py").exists() and (p / "ui.py").exists():
            return True                                  # old flat source layout
        if (p / "System Files" / "main.py").exists():
            return True                                  # System Files/ layout
    except Exception:
        pass
    return False


def _candidate_install_roots() -> list:
    """Places an Ember install might live, plus the running one (de-duplicated)."""
    home = Path.home()
    cands: list[Path] = [_running_install_root()]
    if sys.platform == "darwin":
        cands += [Path("/Applications/Ember.app"), home / "Applications" / "Ember.app",
                  home / "Desktop" / "Ember.app", home / "Downloads" / "Ember.app"]
    elif sys.platform.startswith("win"):
        import os
        lad = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        cands += [Path(lad) / "Programs" / "Ember", Path(lad) / "Ember",
                  home / "Desktop" / "Ember", home / "Downloads" / "Ember"]
    else:
        cands += [home / "Ember", home / ".local" / "share" / "Ember",
                  home / "Desktop" / "Ember", home / "Downloads" / "Ember"]
    seen, out = set(), []
    for c in cands:
        try:
            rp = c.resolve()
        except Exception:
            rp = c
        if rp not in seen:
            seen.add(rp)
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def _safe_to_remove(path: Path) -> bool:
    """A hard gate: only remove real Ember installs, Ember's data dir, or a login-item file.
    Never a system/home root or anything suspiciously shallow."""
    try:
        p = path.resolve()
    except Exception:
        return False
    if len(p.parts) < 3:                       # e.g. "/", "/Applications", "C:\\" — refuse
        return False
    forbidden = {Path("/"), Path.home().resolve(), Path("/Applications"),
                 Path("/System"), Path("/Library"), Path("/usr"), Path("/etc"),
                 Path("/Users"), Path("/bin"), Path("/opt")}
    if p in {f.resolve() for f in forbidden if str(f)}:
        return False
    if _looks_like_ember_install(p):
        return True
    if p == _data_dir().resolve():
        return True
    if p.suffix == ".plist" and "ember" in p.name.lower():
        return True
    return False


def _trash_or_remove(path: Path, use_trash: bool) -> str:
    """Send to Trash when possible (reversible), else delete. Returns what happened."""
    if use_trash:
        try:
            import send2trash
            send2trash.send2trash(str(path))
            return "trashed"
        except Exception:
            pass
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except Exception:
            pass
    return "deleted"


# ---------------------------------------------------------------------------
# Discovery (read-only)
# ---------------------------------------------------------------------------

def find_instances() -> dict:
    """List every Ember install + data dir + login item detected on this computer (no changes)."""
    running = _running_install_root().resolve()
    installs = []
    for cand in _candidate_install_roots():
        if _looks_like_ember_install(cand):
            try:
                rp = cand.resolve()
            except Exception:
                rp = cand
            installs.append({
                "path": str(cand),
                "kind": ("app-bundle" if cand.suffix == ".app" else "source"),
                "running": (rp == running),
            })
    data = []
    dd = _data_dir()
    if dd.exists():
        data.append({"path": str(dd), "kind": "data"})
    autostart_on = False
    try:
        import autostart
        autostart_on = bool(autostart.is_installed())
    except Exception:
        pass
    return {"ok": True, "installs": installs, "data": data,
            "autostart": autostart_on, "count": len(installs),
            "running_install": str(running)}


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

def _remove_login_item() -> str:
    try:
        import autostart
        autostart.uninstall()
        return "removed"
    except Exception as e:
        return f"skip ({e})"


def uninstall_instance(path: str, confirm: bool = False, use_trash: bool = True,
                       remove_data: bool = False) -> dict:
    """Remove ONE Ember install by path. Dry-run unless confirm=True."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "error": f"no such path: {path}"}
    if not _safe_to_remove(p):
        return {"ok": False, "error": f"refused: {path} is not a recognised Ember install "
                                      "(safety guard — nothing was touched)."}
    running = (p.resolve() == _running_install_root().resolve())
    plan = [{"path": str(p), "kind": "install", "running": running}]
    if remove_data and _data_dir().exists():
        plan.append({"path": str(_data_dir()), "kind": "data"})
    if not confirm:
        return {"ok": True, "dry_run": True, "would_remove": plan,
                "note": ("This install is currently running — quit Ember first, or it may not "
                         "fully delete." if running else ""),
                "message": f"Dry run: would remove {len(plan)} item(s). Call again with confirm=true."}
    removed = []
    for item in plan:
        target = Path(item["path"])
        if _safe_to_remove(target):
            removed.append({"path": item["path"], "result": _trash_or_remove(target, use_trash)})
    return {"ok": True, "removed": removed,
            "message": f"Removed {len(removed)} item(s)."
                       + (" Quit Ember to finish removing the running copy." if running else "")}


def uninstall_all(confirm: bool = False, use_trash: bool = True,
                  include_running: bool = False, keep_data: bool = False) -> dict:
    """Remove EVERY Ember instance + data dir + login item. Dry-run unless confirm=True.

    The currently-running install is left in place unless include_running=True (you can't
    reliably delete the app while it's running — quit first)."""
    found = find_instances()
    running = _running_install_root().resolve()
    plan = []
    for inst in found["installs"]:
        is_running = Path(inst["path"]).resolve() == running
        if is_running and not include_running:
            continue
        if _safe_to_remove(Path(inst["path"])):
            plan.append({"path": inst["path"], "kind": "install", "running": is_running})
    if not keep_data:
        for d in found["data"]:
            plan.append({"path": d["path"], "kind": "data"})
    login = found["autostart"]

    if not confirm:
        skipped_running = [i["path"] for i in found["installs"]
                           if Path(i["path"]).resolve() == running and not include_running]
        return {"ok": True, "dry_run": True, "would_remove": plan,
                "would_remove_login_item": login,
                "skipped_running": skipped_running,
                "message": (f"Dry run: would remove {len(plan)} item(s)"
                            + (" + the login item" if login else "")
                            + (f"; the running install ({skipped_running[0]}) is kept — quit "
                               "Ember and pass include_running=true to remove it too."
                               if skipped_running else "")
                            + ". Call again with confirm=true to proceed.")}
    removed = []
    for item in plan:
        target = Path(item["path"])
        if _safe_to_remove(target):
            removed.append({"path": item["path"], "kind": item["kind"],
                            "result": _trash_or_remove(target, use_trash)})
    login_result = _remove_login_item() if login else "none"
    still_running = [i["path"] for i in found["installs"]
                     if Path(i["path"]).resolve() == running and not include_running]
    return {"ok": True, "removed": removed, "login_item": login_result,
            "message": f"Removed {len(removed)} item(s)"
                       + (" + the login item" if login else "") + "."
                       + (f" The running install ({still_running[0]}) was kept — quit Ember, "
                          "then delete that folder to finish." if still_running else "")}
