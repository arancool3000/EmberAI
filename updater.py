"""In-app auto-updater for the Ember desktop app — macOS (.app) and Windows (onedir).

Flow: fetch latest.json from GitHub Releases -> pick this OS's download -> compare to
version.__version__ -> download -> verify sha256 -> unpack -> swap the running install via
a detached relaunch helper (with a backup + rollback) -> relaunch.

Platform specifics:
- macOS: ad-hoc-signed .app; unpack with `ditto` (preserves symlinks + signature), strip the
  com.apple.quarantine xattr, swap the .app via a bash helper.
- Windows: PyInstaller onedir folder; unpack with `zipfile`, swap the install folder via a
  batch helper (robocopy /MOVE with rollback), relaunch Ember.exe.

Robust by construction: any failure raises (caller surfaces it and aborts), the running
install is kept as a `.old` backup during the swap and rolled back on failure, and the whole
feature is a silent no-op in dev (non-frozen) and until version.GITHUB_OWNER is configured.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import version


def current_version() -> str:
    return version.__version__


def is_frozen_app() -> bool:
    """True only when running as a built app on macOS or Windows (where self-update works)."""
    return bool(getattr(sys, "frozen", False)) and version.platform_key() is not None


def install_root() -> Path | None:
    """What we swap on update: the .app bundle (macOS) or the install folder (Windows)."""
    if not is_frozen_app():
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:           # .../Ember.app/Contents/MacOS/Ember
            if parent.suffix == ".app":
                return parent
        return None
    return exe.parent                        # .../Ember/Ember.exe -> .../Ember


# Back-compat alias (older callers / tests).
def app_bundle_path() -> Path | None:
    return install_root()


def can_self_update() -> bool:
    """Self-update is possible only as a configured, frozen app we can write over."""
    if not is_frozen_app() or not version.is_configured():
        return False
    root = install_root()
    return bool(root and os.access(root.parent, os.W_OK))


def _manifest_download(manifest: dict) -> tuple[str, str]:
    """Return (url, sha256) for this OS, falling back to the predictable release-asset URL."""
    key = version.platform_key() or "macos"
    d = (manifest.get("downloads") or {}).get(key) or {}
    url = d.get("url") or manifest.get("url") or version.latest_download_url(key)
    sha = (d.get("sha256") or manifest.get("sha256") or "").strip().lower()
    return url, sha


def check_for_update(timeout: float = 8.0) -> dict | None:
    """Return the manifest dict if a newer version is published for this OS, else None.
    Network/parse failures return None (a failed check must never disrupt the app)."""
    if not version.is_configured():
        return None
    try:
        req = urllib.request.Request(version.manifest_url(),
                                     headers={"User-Agent": "Ember-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            manifest = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    latest = str(manifest.get("version", ""))
    if latest and version.is_newer(latest, current_version()):
        return manifest
    return None


def _download(url: str, dest: Path, progress=None, timeout: float = 60.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Ember-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    try:
                        progress(min(100, int(done * 100 / total)))
                    except Exception:
                        pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_payload(extract_dir: Path) -> Path:
    """Locate the new install inside the extracted archive: the .app (mac) or the folder
    containing the Ember executable (Windows)."""
    if sys.platform == "darwin":
        apps = list(extract_dir.glob("*.app")) or list(extract_dir.rglob("*.app"))
        if not apps:
            raise RuntimeError("update archive did not contain an .app bundle")
        return apps[0]
    exe_name = Path(sys.executable).name  # e.g. Ember.exe
    for exe in [extract_dir / exe_name, *extract_dir.rglob(exe_name)]:
        if exe.exists():
            return exe.parent
    raise RuntimeError(f"update archive did not contain {exe_name}")


def download_and_stage(manifest: dict, progress=None) -> Path:
    """Download + verify + unpack the update. Returns the staged install path
    (the new .app on macOS, or the new install folder on Windows). Raises on failure."""
    url, expected_sha = _manifest_download(manifest)
    tmp = Path(tempfile.mkdtemp(prefix="ember_update_"))
    zpath = tmp / "Ember.zip"
    _download(url, zpath, progress=progress)

    if expected_sha:
        actual = _sha256(zpath)
        if actual != expected_sha:
            raise RuntimeError(f"checksum mismatch (expected {expected_sha[:12]}…, "
                               f"got {actual[:12]}…) — refusing to install")

    # Defense in depth: scan the downloaded archive before unpacking it.
    try:
        import antivirus
        scan = antivirus.scan_file(str(zpath), deep=True)
        if scan.get("verdict") == "malicious":
            raise RuntimeError("update archive flagged as malicious by the on-device "
                               "scanner — refusing to install")
    except RuntimeError:
        raise
    except Exception:
        pass

    extract_dir = tmp / "extracted"
    extract_dir.mkdir()
    if sys.platform == "darwin":
        res = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zpath), str(extract_dir)],
                             capture_output=True, text=True, timeout=180)
        if res.returncode != 0:
            raise RuntimeError(f"could not unpack update: {res.stderr.strip()[:200]}")
    else:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(extract_dir)

    payload = _find_payload(extract_dir)
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(payload)],
                       capture_output=True)
    return payload


def apply_update_and_relaunch(staged: Path) -> None:
    """Swap the staged install over the running one (after we exit) and relaunch.
    The caller MUST quit the app right after this returns."""
    target = install_root()
    if not target:
        raise RuntimeError("not running as a frozen app — cannot self-update")
    pid = os.getpid()
    if sys.platform == "darwin":
        _spawn_macos_swap(staged, target, pid)
    elif sys.platform.startswith("win"):
        _spawn_windows_swap(staged, target, pid)
    else:
        raise RuntimeError("self-update not supported on this platform")


def _spawn_macos_swap(staged: Path, target: Path, pid: int) -> None:
    backup = f"{target}.old"
    t, n, b = shlex.quote(str(target)), shlex.quote(str(staged)), shlex.quote(backup)
    helper = (
        "#!/bin/bash\n"
        f"while /bin/kill -0 {pid} 2>/dev/null; do sleep 0.4; done\n"
        "sleep 0.3\n"
        f"/bin/rm -rf {b} 2>/dev/null\n"
        f"/bin/mv {t} {b} 2>/dev/null\n"
        f"if /usr/bin/ditto {n} {t}; then\n"
        f"  /bin/rm -rf {b} 2>/dev/null\n"
        f"  /usr/bin/xattr -dr com.apple.quarantine {t} 2>/dev/null\n"
        "else\n"
        f"  /bin/rm -rf {t} 2>/dev/null; /bin/mv {b} {t} 2>/dev/null\n"
        "fi\n"
        f"/usr/bin/open {t}\n"
    )
    helper_path = Path(tempfile.mkdtemp(prefix="ember_swap_")) / "swap.sh"
    helper_path.write_text(helper)
    helper_path.chmod(0o755)
    subprocess.Popen(["/bin/bash", str(helper_path)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _spawn_windows_swap(staged: Path, target: Path, pid: int) -> None:
    exe = Path(sys.executable).name
    backup = f"{target}.old"
    # Wait for this process to exit, swap the folder (robocopy /MOVE), rollback on failure,
    # relaunch, then delete the helper.
    bat = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL && (timeout /t 1 /nobreak >NUL & goto wait)\r\n'
        "timeout /t 1 /nobreak >NUL\r\n"
        f'if exist "{backup}" rmdir /S /Q "{backup}"\r\n'
        f'move "{target}" "{backup}" >NUL\r\n'
        f'robocopy "{staged}" "{target}" /E /MOVE >NUL\r\n'
        "if %ERRORLEVEL% GEQ 8 (\r\n"
        f'  if exist "{target}" rmdir /S /Q "{target}"\r\n'
        f'  move "{backup}" "{target}" >NUL\r\n'
        ") else (\r\n"
        f'  if exist "{backup}" rmdir /S /Q "{backup}"\r\n'
        ")\r\n"
        f'start "" "{target}\\{exe}"\r\n'
        'del "%~f0"\r\n'
    )
    helper_path = Path(tempfile.mkdtemp(prefix="ember_swap_")) / "swap.bat"
    helper_path.write_text(bat, encoding="utf-8")
    DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS|NEW_GROUP|NO_WINDOW
    subprocess.Popen(["cmd", "/c", str(helper_path)], creationflags=DETACHED,
                     close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
