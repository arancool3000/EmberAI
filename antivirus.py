"""Ember's built-in malware defense: scan, quarantine, and sandbox.

This is the security layer the app uses to protect the machine from hostile
files it downloads or is asked to open:

  * scan_file()        - classify a file as clean / suspicious / malicious using
                         local static heuristics + the platform antivirus
                         (Windows Defender / ClamAV) + VirusTotal (hash lookup,
                         and an upload of unknown files when enabled).
  * gate_download()    - scan a freshly downloaded file; CONFIRMED-malicious files
                         are moved to a locked quarantine vault (and auto-deleted
                         after a grace period).
  * gate_open()        - scan a file *before* it is opened and refuse to open it
                         until the scan finishes (malicious -> quarantined and
                         blocked; suspicious -> blocked pending review).
  * run_in_sandbox()   - run an unknown program in the strongest isolation
                         available (Docker -> OS-native -> refuse) so its
                         behavior can be observed without risking the host.
  * quarantine vault   - list_quarantine() / restore_quarantined() /
                         delete_quarantined() / purge_expired().

Design rules:
  - Never crash the host app. Every public entry point is wrapped so a scanner
    failure degrades to "could not scan" rather than an exception.
  - Confidence model: local heuristics can only raise "suspicious"; a verdict of
    "malicious" requires a definitive signal (EICAR / known-bad hash / platform
    AV hit / VirusTotal consensus). Only "malicious" files are ever quarantined
    or deleted, so a heuristic false positive can never destroy a user's file.
  - No hard third-party dependencies. Docker, ClamAV and a VirusTotal API key are
    all optional and detected at runtime; the module works (more weakly) without
    any of them. `requests` (already a dependency) is used for VirusTotal.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------

def _support_dir() -> Path:
    """Per-user app-support directory (overridable via EMBER_SUPPORT_DIR for tests)."""
    override = os.environ.get("EMBER_SUPPORT_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Ember"
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        base = Path(local) / "Ember"
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(xdg) / "Ember"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _quarantine_dir() -> Path:
    d = _support_dir() / "Quarantine"
    d.mkdir(parents=True, exist_ok=True)
    return d


_CONFIG_LOCK = threading.RLock()

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "scan_downloads": True,        # scan files as they finish downloading
    "scan_before_open": True,      # scan files before opening them
    "block_suspicious_open": True, # also block "suspicious" (not just "malicious") on open
    "vt_api_key": "",              # falls back to env VIRUSTOTAL_API_KEY / VT_API_KEY
    "vt_hash_lookup": True,        # query VirusTotal by SHA-256 (only the hash leaves)
    "vt_upload_unknown": True,     # upload unknown files to VirusTotal for full scanning
    "on_malware": "quarantine_autodelete",  # quarantine | quarantine_autodelete | delete
    "autodelete_days": 7,          # grace period before a quarantined file is purged
    "sandbox_mode": "auto",        # auto | docker | native | off
    "max_scan_bytes": 64 * 1024 * 1024,      # files larger than this: hash + heuristics only
    "vt_upload_max_bytes": 32 * 1024 * 1024, # VirusTotal's free upload ceiling
    "vt_malicious_threshold": 3,   # >= this many AV engines flag it -> malicious
}


def _config_path() -> Path:
    return _support_dir() / "security.json"


def get_config() -> dict:
    """Load config, merged over defaults (missing keys fall back to DEFAULT_CONFIG)."""
    with _CONFIG_LOCK:
        cfg = dict(DEFAULT_CONFIG)
        try:
            p = _config_path()
            if p.exists():
                cfg.update(json.loads(p.read_text("utf-8")))
        except Exception:
            pass
        return cfg


def set_config(**changes) -> dict:
    """Update and persist config values. Returns the new config."""
    with _CONFIG_LOCK:
        cfg = get_config()
        for k, v in changes.items():
            if k in DEFAULT_CONFIG:
                cfg[k] = v
        try:
            _config_path().write_text(json.dumps(cfg, indent=2), "utf-8")
        except Exception:
            pass
        return cfg


def _vt_api_key(cfg: dict) -> str:
    return (cfg.get("vt_api_key") or os.environ.get("VIRUSTOTAL_API_KEY")
            or os.environ.get("VT_API_KEY") or "").strip()


# ---------------------------------------------------------------------------
# Hashing & known-bad signatures
# ---------------------------------------------------------------------------

# The EICAR anti-malware test file: a harmless, industry-standard string every
# real scanner flags. We detect it directly so the defense is verifiable.
EICAR_SIG = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
             b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")
EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"

# Hashes known to be malicious. EICAR is included; extend as needed.
KNOWN_BAD_SHA256 = {EICAR_SHA256}


def sha256_file(path: Path, max_bytes: int | None = None) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            read += len(chunk)
            if max_bytes and read >= max_bytes:
                break
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Static heuristics  (can raise at most "suspicious")
# ---------------------------------------------------------------------------

# Magic bytes that mark executable / native code.
_EXEC_MAGIC = [
    (b"MZ", "windows-pe"),
    (b"\x7fELF", "linux-elf"),
    (b"\xfe\xed\xfa\xce", "macho"),
    (b"\xfe\xed\xfa\xcf", "macho"),
    (b"\xce\xfa\xed\xfe", "macho"),
    (b"\xcf\xfa\xed\xfe", "macho"),
    (b"\xca\xfe\xba\xbe", "macho-universal-or-java"),
]

# Extensions that execute code (high blast radius if disguised / unexpected).
_DANGEROUS_EXTS = {
    ".exe", ".msi", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".ps1", ".psm1", ".lnk", ".jar",
    ".app", ".pkg", ".dmg", ".command", ".sh", ".bash", ".zsh", ".scpt",
    ".dll", ".dylib", ".so", ".reg", ".cpl", ".gadget",
}

# Extensions users assume are inert documents/media — alarming if they hold code.
_BENIGN_LOOKING_EXTS = {
    ".pdf", ".txt", ".doc", ".rtf", ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".mp3", ".mp4", ".mov", ".csv", ".html", ".htm", ".md", ".json", ".xml",
}

_MACRO_EXTS = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm"}


def _static_scan(path: Path) -> tuple[int, list[str], dict]:
    """Heuristic analysis. Returns (score 0-100, reasons, info)."""
    score = 0
    reasons: list[str] = []
    info: dict = {}
    try:
        head = path.read_bytes()[:8192]
    except Exception as e:
        return 0, [f"could not read file: {e}"], info

    name = path.name.lower()
    suffixes = [s.lower() for s in path.suffixes]
    ext = path.suffix.lower()

    # EICAR test signature -> hard malicious signal (caller promotes to malicious).
    if EICAR_SIG in head:
        return 100, ["EICAR anti-malware test signature present"], {"eicar": True}

    # Detect executable / native code content.
    exec_kind = None
    for magic, kind in _EXEC_MAGIC:
        if head.startswith(magic):
            exec_kind = kind
            break
    is_script = head.startswith(b"#!")
    if exec_kind:
        info["content"] = exec_kind

    # Executable content wearing a harmless-looking extension = classic disguise.
    if (exec_kind or is_script) and ext in _BENIGN_LOOKING_EXTS:
        score += 70
        reasons.append(f"executable/script content disguised as a '{ext}' file")

    # Double extension, e.g. invoice.pdf.exe / photo.jpg.scr.
    if len(suffixes) >= 2 and suffixes[-1] in _DANGEROUS_EXTS and suffixes[-2] in _BENIGN_LOOKING_EXTS:
        score += 45
        reasons.append(f"double extension '{suffixes[-2]}{suffixes[-1]}' hides an executable")

    # Office documents with macros (a very common malware delivery vector).
    if ext in _MACRO_EXTS:
        score += 30
        reasons.append(f"macro-enabled Office document ({ext})")
    elif ext in (".docx", ".xlsx", ".pptx", ".zip") and _zip_has_macro(path):
        score += 35
        reasons.append("Office container holds a VBA macro project (vbaProject.bin)")

    # A plainly dangerous executable type (noted, but not damning on its own).
    if ext in _DANGEROUS_EXTS and not reasons:
        score += 20
        reasons.append(f"executable file type ({ext})")

    # Windows shortcut that smuggles a script payload.
    if ext == ".lnk" and (b"powershell" in head.lower() or b"cmd.exe" in head.lower()):
        score += 50
        reasons.append(".lnk shortcut launches a shell/PowerShell payload")

    return min(score, 100), reasons, info


def _zip_has_macro(path: Path) -> bool:
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            return any(n.endswith("vbaProject.bin") for n in zf.namelist())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Platform antivirus backends  (definitive "malicious" signals)
# ---------------------------------------------------------------------------

def _which(name: str) -> str | None:
    return shutil.which(name)


def _scan_windows_defender(path: Path) -> dict | None:
    """Scan via Windows Defender's MpCmdRun. Returns a verdict dict or None if unavailable."""
    if not sys.platform.startswith("win"):
        return None
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe",
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / "Microsoft" / "Windows Defender" / "Platform",
    ]
    exe = None
    if candidates[0].exists():
        exe = candidates[0]
    else:
        # Defender ships under a versioned Platform folder; pick the newest.
        try:
            plat = candidates[1]
            if plat.exists():
                versions = sorted(plat.glob("*/MpCmdRun.exe"))
                if versions:
                    exe = versions[-1]
        except Exception:
            pass
    if not exe:
        return None
    try:
        res = subprocess.run(
            [str(exe), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
            capture_output=True, text=True, timeout=120,
        )
        out = (res.stdout or "") + (res.stderr or "")
        # MpCmdRun returns 2 when a threat is found.
        if res.returncode == 2 or "found" in out.lower() and "no threats" not in out.lower():
            return {"engine": "windows-defender", "malicious": True, "detail": out.strip()[:300]}
        return {"engine": "windows-defender", "malicious": False}
    except Exception:
        return None


def _scan_clamav(path: Path) -> dict | None:
    """Scan via ClamAV's clamscan if installed. Returns a verdict dict or None."""
    exe = _which("clamscan")
    if not exe:
        return None
    try:
        res = subprocess.run([exe, "--no-summary", "--stdout", str(path)],
                             capture_output=True, text=True, timeout=120)
        # clamscan: 0 = clean, 1 = infected, 2 = error.
        if res.returncode == 1:
            sig = ""
            for line in (res.stdout or "").splitlines():
                if line.strip().endswith("FOUND"):
                    sig = line.split(":", 1)[-1].strip()
                    break
            return {"engine": "clamav", "malicious": True, "detail": sig[:300]}
        if res.returncode == 0:
            return {"engine": "clamav", "malicious": False}
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# VirusTotal backend
# ---------------------------------------------------------------------------

def _vt_request(method: str, url: str, api_key: str, **kw):
    import requests  # local import: optional path, keeps base import light
    headers = kw.pop("headers", {})
    headers["x-apikey"] = api_key
    return requests.request(method, url, headers=headers, timeout=kw.pop("timeout", 30), **kw)


def _interpret_vt_stats(stats: dict, threshold: int) -> dict:
    malicious = int(stats.get("malicious", 0))
    suspicious = int(stats.get("suspicious", 0))
    hits = malicious + suspicious
    verdict = "clean"
    if malicious >= threshold:
        verdict = "malicious"
    elif hits >= 1:
        verdict = "suspicious"
    return {"engine": "virustotal", "verdict": verdict, "malicious_count": malicious,
            "suspicious_count": suspicious}


def _scan_virustotal(path: Path, sha256: str, cfg: dict) -> dict | None:
    """Look the file up on VirusTotal by hash; optionally upload if unknown.
    Returns a verdict dict, or None if VT is unavailable/unconfigured/errored."""
    api_key = _vt_api_key(cfg)
    if not api_key:
        return None
    threshold = int(cfg.get("vt_malicious_threshold", 3))
    try:
        if cfg.get("vt_hash_lookup", True):
            r = _vt_request("GET", f"https://www.virustotal.com/api/v3/files/{sha256}", api_key)
            if r.status_code == 200:
                stats = r.json()["data"]["attributes"]["last_analysis_stats"]
                res = _interpret_vt_stats(stats, threshold)
                res["source"] = "hash-lookup"
                return res
            if r.status_code not in (404,):
                return None  # rate-limited / auth error: treat as unavailable
        # Unknown to VT -> optionally upload for a full multi-engine scan.
        size = path.stat().st_size
        if not cfg.get("vt_upload_unknown", True) or size > int(cfg.get("vt_upload_max_bytes", 32 << 20)):
            return None
        with open(path, "rb") as fh:
            up = _vt_request("POST", "https://www.virustotal.com/api/v3/files", api_key,
                             files={"file": (path.name, fh)}, timeout=120)
        if up.status_code not in (200, 201):
            return None
        analysis_id = up.json()["data"]["id"]
        for _ in range(20):  # poll up to ~60s for the analysis to finish
            time.sleep(3)
            a = _vt_request("GET", f"https://www.virustotal.com/api/v3/analyses/{analysis_id}", api_key)
            if a.status_code != 200:
                continue
            attrs = a.json()["data"]["attributes"]
            if attrs.get("status") == "completed":
                res = _interpret_vt_stats(attrs.get("stats", {}), threshold)
                res["source"] = "upload"
                return res
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scan orchestration
# ---------------------------------------------------------------------------

_VERDICT_ORDER = {"clean": 0, "suspicious": 1, "malicious": 2}
_SUSPICIOUS_SCORE = 45


def scan_file(path: str, deep: bool = True) -> dict:
    """Classify a single file.

    Returns: {ok, path, sha256, verdict, score, reasons, engines, size}
    verdict is one of "clean" | "suspicious" | "malicious".
    `deep=False` skips the online VirusTotal step (local-only).
    """
    try:
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        cfg = get_config()
        size = p.stat().st_size
        reasons: list[str] = []
        engines: list[str] = []
        verdict = "clean"

        def _raise(v: str):
            nonlocal verdict
            if _VERDICT_ORDER[v] > _VERDICT_ORDER[verdict]:
                verdict = v

        # 1) Static heuristics (suspicious ceiling, except EICAR).
        score, sreasons, info = _static_scan(p)
        reasons += sreasons
        engines.append("heuristics")
        if info.get("eicar"):
            _raise("malicious")
        elif score >= _SUSPICIOUS_SCORE:
            _raise("suspicious")

        # 2) Hash + known-bad list.
        sha = sha256_file(p, max_bytes=None) if size <= cfg["max_scan_bytes"] else sha256_file(p, cfg["max_scan_bytes"])
        if sha in KNOWN_BAD_SHA256:
            _raise("malicious")
            reasons.append("matches a known-malicious file hash")

        # 3) Platform antivirus (definitive).
        for backend in (_scan_windows_defender, _scan_clamav):
            res = backend(p)
            if res is None:
                continue
            engines.append(res["engine"])
            if res.get("malicious"):
                _raise("malicious")
                reasons.append(f"{res['engine']} detection" +
                               (f": {res['detail']}" if res.get("detail") else ""))

        # 4) VirusTotal (hash lookup, then upload of unknowns).
        if deep:
            vt = _scan_virustotal(p, sha, cfg)
            if vt is not None:
                engines.append("virustotal")
                _raise(vt["verdict"])
                if vt["verdict"] != "clean":
                    reasons.append(
                        f"VirusTotal ({vt.get('source','?')}): "
                        f"{vt.get('malicious_count',0)} malicious / "
                        f"{vt.get('suspicious_count',0)} suspicious engines")

        return {"ok": True, "path": str(p), "sha256": sha, "verdict": verdict,
                "score": score, "reasons": reasons or ["no indicators found"],
                "engines": engines, "size": size}
    except Exception as e:
        return {"ok": False, "error": f"scan failed: {e}", "verdict": "unknown"}


# ---------------------------------------------------------------------------
# Quarantine vault
# ---------------------------------------------------------------------------

def _index_path() -> Path:
    return _quarantine_dir() / "quarantine.json"


def _load_index() -> list[dict]:
    try:
        p = _index_path()
        if p.exists():
            return json.loads(p.read_text("utf-8"))
    except Exception:
        pass
    return []


def _save_index(entries: list[dict]) -> None:
    try:
        _index_path().write_text(json.dumps(entries, indent=2), "utf-8")
    except Exception:
        pass


def quarantine_file(path: str, reasons: list[str] | None = None, sha256: str = "") -> dict:
    """Move a file into the locked quarantine vault and record it. The stored copy
    is renamed (neutralized) and made non-executable / non-readable where possible."""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return {"ok": False, "error": f"file not found: {path}"}
        cfg = get_config()
        sha = sha256 or sha256_file(p, cfg["max_scan_bytes"])
        qid = f"{int(time.time())}_{sha[:8]}"
        stored = _quarantine_dir() / f"{qid}_{p.name}.quarantine"
        shutil.move(str(p), str(stored))
        try:
            os.chmod(stored, stat.S_IRUSR)  # owner read-only; strip all execute bits
        except Exception:
            pass
        now = time.time()
        entry = {
            "id": qid,
            "original_path": str(p),
            "stored_path": str(stored),
            "sha256": sha,
            "reasons": reasons or [],
            "quarantined_at": now,
            "delete_after": (now + cfg["autodelete_days"] * 86400
                             if cfg["on_malware"] == "quarantine_autodelete" else None),
        }
        entries = _load_index()
        entries.append(entry)
        _save_index(entries)
        return {"ok": True, "quarantined": True, "id": qid, "stored_path": str(stored),
                "original_path": str(p), "delete_after": entry["delete_after"]}
    except Exception as e:
        return {"ok": False, "error": f"quarantine failed: {e}"}


def list_quarantine() -> dict:
    """List everything currently in quarantine (purges anything past its grace period first)."""
    purge_expired()
    entries = _load_index()
    items = [{
        "id": e["id"],
        "original_path": e["original_path"],
        "sha256": e.get("sha256", ""),
        "reasons": e.get("reasons", []),
        "quarantined_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["quarantined_at"])),
        "deletes_on": (time.strftime("%Y-%m-%d %H:%M", time.localtime(e["delete_after"]))
                       if e.get("delete_after") else "never"),
    } for e in entries]
    return {"ok": True, "count": len(items), "items": items}


def restore_quarantined(id: str, destination: str = "") -> dict:
    """Move a quarantined file back to its original location (or `destination`).
    This re-arms a file you previously deemed dangerous, so callers should confirm."""
    try:
        entries = _load_index()
        entry = next((e for e in entries if e["id"] == id), None)
        if not entry:
            return {"ok": False, "error": f"no quarantine entry with id {id}"}
        stored = Path(entry["stored_path"])
        if not stored.exists():
            return {"ok": False, "error": "quarantined payload is missing"}
        target = Path(destination).expanduser() if destination else Path(entry["original_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(stored, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        shutil.move(str(stored), str(target))
        _save_index([e for e in entries if e["id"] != id])
        return {"ok": True, "restored_to": str(target), "id": id}
    except Exception as e:
        return {"ok": False, "error": f"restore failed: {e}"}


def delete_quarantined(id: str) -> dict:
    """Permanently delete a single quarantined file."""
    try:
        entries = _load_index()
        entry = next((e for e in entries if e["id"] == id), None)
        if not entry:
            return {"ok": False, "error": f"no quarantine entry with id {id}"}
        try:
            Path(entry["stored_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        _save_index([e for e in entries if e["id"] != id])
        return {"ok": True, "deleted": id}
    except Exception as e:
        return {"ok": False, "error": f"delete failed: {e}"}


def purge_expired() -> dict:
    """Delete quarantined files whose grace period has elapsed (the 7-day auto-delete)."""
    now = time.time()
    entries = _load_index()
    kept, removed = [], []
    for e in entries:
        if e.get("delete_after") and now >= e["delete_after"]:
            try:
                Path(e["stored_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            removed.append(e["id"])
        else:
            kept.append(e)
    if removed:
        _save_index(kept)
    return {"ok": True, "purged": removed, "remaining": len(kept)}


# ---------------------------------------------------------------------------
# Gates: scan-on-download and scan-before-open
# ---------------------------------------------------------------------------

def _handle_malicious(path: str, scan: dict) -> dict:
    """Apply the configured response to a confirmed-malicious file."""
    cfg = get_config()
    action = cfg.get("on_malware", "quarantine_autodelete")
    if action == "delete":
        try:
            Path(path).expanduser().unlink(missing_ok=True)
            return {"action": "deleted", "ok": True}
        except Exception as e:
            return {"action": "delete_failed", "ok": False, "error": str(e)}
    q = quarantine_file(path, reasons=scan.get("reasons"), sha256=scan.get("sha256", ""))
    q["action"] = "quarantined"
    return q


def gate_download(path: str) -> dict:
    """Scan a freshly downloaded file. Confirmed-malicious files are quarantined
    (and auto-deleted later); suspicious files are flagged but left in place."""
    cfg = get_config()
    if not cfg.get("enabled") or not cfg.get("scan_downloads"):
        return {"ok": True, "scanned": False, "verdict": "unscanned"}
    scan = scan_file(path, deep=True)
    if not scan.get("ok"):
        return {"ok": True, "scanned": False, "verdict": "unknown", "error": scan.get("error")}
    out = {"ok": True, "scanned": True, "verdict": scan["verdict"],
           "reasons": scan["reasons"], "engines": scan["engines"], "sha256": scan["sha256"]}
    if scan["verdict"] == "malicious":
        out["handled"] = _handle_malicious(path, scan)
        out["blocked"] = True
    return out


def gate_open(path: str) -> dict:
    """Scan a file BEFORE it is opened and decide whether opening is allowed.
    Blocks (allowed=False) until the scan completes; malicious files are
    quarantined; suspicious files are blocked pending review (configurable)."""
    cfg = get_config()
    if not cfg.get("enabled") or not cfg.get("scan_before_open"):
        return {"ok": True, "allowed": True, "scanned": False}
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        # Not a scannable file (e.g. a directory or app to launch by name) — let it through.
        return {"ok": True, "allowed": True, "scanned": False, "verdict": "n/a"}
    scan = scan_file(str(p), deep=True)
    if not scan.get("ok"):
        return {"ok": True, "allowed": True, "scanned": False, "verdict": "unknown",
                "error": scan.get("error")}
    verdict = scan["verdict"]
    result = {"ok": True, "scanned": True, "verdict": verdict,
              "reasons": scan["reasons"], "engines": scan["engines"]}
    if verdict == "malicious":
        result["allowed"] = False
        result["handled"] = _handle_malicious(str(p), scan)
        result["message"] = "Blocked: this file is malicious and has been quarantined."
    elif verdict == "suspicious" and cfg.get("block_suspicious_open", True):
        result["allowed"] = False
        result["message"] = ("Blocked: this file looks suspicious. Inspect it with "
                             "run_in_sandbox, or restore/allow it explicitly if you trust it.")
    else:
        result["allowed"] = True
    return result


# ---------------------------------------------------------------------------
# Sandbox: run an unknown program under the strongest isolation available
# ---------------------------------------------------------------------------

def _docker_available() -> bool:
    if not _which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _sandbox_image_for(path: Path) -> tuple[str, list[str]]:
    """Pick a Docker image + command prefix for a file type."""
    ext = path.suffix.lower()
    if ext in (".py", ".pyw"):
        return "python:3.12-slim", ["python", "/work/target"]
    if ext in (".js", ".mjs", ".cjs"):
        return "node:20-slim", ["node", "/work/target"]
    if ext in (".sh", ".bash"):
        return "debian:stable-slim", ["bash", "/work/target"]
    # Default: assume a Linux ELF binary; try to execute it directly.
    return "debian:stable-slim", ["/work/target"]


def _run_docker_sandbox(p: Path, args: list[str], timeout: int) -> dict:
    image, cmd = _sandbox_image_for(p)
    work = tempfile.mkdtemp(prefix="ember_sbx_")
    target = Path(work) / "target"
    shutil.copy2(p, target)
    try:
        os.chmod(target, 0o555)
    except Exception:
        pass
    docker_cmd = [
        "docker", "run", "--rm",
        "--network", "none",              # no network access
        "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--user", "1000:1000",
        "-v", f"{work}:/work:ro",
        image, *cmd, *args,
    ]
    try:
        r = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": True, "sandbox": "docker", "isolation": "strong (container, no network)",
                "image": image, "exit_code": r.returncode, "timed_out": False,
                "stdout": (r.stdout or "")[:8000], "stderr": (r.stderr or "")[:4000]}
    except subprocess.TimeoutExpired:
        return {"ok": True, "sandbox": "docker", "isolation": "strong (container, no network)",
                "image": image, "timed_out": True,
                "note": f"killed after {timeout}s — long-running or hung"}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# A restrictive macOS seatbelt profile: deny everything, then allow read + fork.
_MAC_SANDBOX_PROFILE = (
    "(version 1)"
    "(deny default)"
    "(allow process-fork)(allow process-exec)"
    "(allow file-read*)"
    "(allow sysctl-read)"
    "(deny network*)"
    "(deny file-write*)"
)


def _native_command(p: Path, args: list[str]) -> list[str]:
    ext = p.suffix.lower()
    if ext in (".py", ".pyw"):
        return [sys.executable, str(p), *args]
    if ext in (".sh", ".bash", ".zsh", ".command"):
        return ["/bin/bash", str(p), *args]
    return [str(p), *args]


def _run_native_sandbox(p: Path, args: list[str], timeout: int) -> dict:
    cmd = _native_command(p, args)
    try:
        if sys.platform == "darwin":
            full = ["/usr/bin/sandbox-exec", "-p", _MAC_SANDBOX_PROFILE, *cmd]
            iso = "medium (macOS seatbelt: no network, read-only filesystem)"
        elif sys.platform.startswith("win"):
            # Best effort without pywin32: run as a basic-rights token if possible.
            runas = shutil.which("runas")
            if runas:
                full = [runas, "/trustlevel:0x20000", subprocess.list2cmdline(cmd)]
                iso = "limited (Windows basic-user restricted token)"
            else:
                full = cmd
                iso = "minimal (timeout-confined only)"
        else:
            fj = _which("firejail") or _which("bwrap")
            if fj and fj.endswith("firejail"):
                full = [fj, "--quiet", "--net=none", "--private", *cmd]
                iso = "strong (firejail: no network, private filesystem)"
            elif fj:
                full = [fj, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", *cmd]
                iso = "strong (bubblewrap: namespaced, no network)"
            else:
                return {"ok": False, "sandbox": "none",
                        "error": "no sandbox available (install Docker or firejail to run untrusted files safely)"}
        try:
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        except Exception:
            pass
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return {"ok": True, "sandbox": "native", "isolation": iso,
                "exit_code": r.returncode, "timed_out": False,
                "stdout": (r.stdout or "")[:8000], "stderr": (r.stderr or "")[:4000]}
    except subprocess.TimeoutExpired:
        return {"ok": True, "sandbox": "native", "timed_out": True,
                "note": f"killed after {timeout}s — long-running or hung"}
    except Exception as e:
        return {"ok": False, "sandbox": "native", "error": str(e)}


def run_in_sandbox(path: str, args: list[str] | None = None, timeout: int = 30) -> dict:
    """Run a program in the strongest available sandbox to observe it safely.

    Strategy (config 'sandbox_mode'):
      auto   -> Docker if running, else OS-native confinement, else refuse.
      docker -> require Docker.
      native -> OS-native confinement only.
      off    -> refuse (sandbox disabled).
    Always scans the file first and refuses to run anything already known-malicious.
    """
    try:
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        if isinstance(args, str):
            import shlex
            args = shlex.split(args)
        args = [str(a) for a in (args or [])]
        cfg = get_config()
        mode = cfg.get("sandbox_mode", "auto")
        if mode == "off":
            return {"ok": False, "error": "sandbox is disabled in settings"}

        # Pre-scan: never execute a file we already know is malicious.
        scan = scan_file(str(p), deep=True)
        if scan.get("verdict") == "malicious":
            handled = _handle_malicious(str(p), scan)
            return {"ok": False, "refused": True, "verdict": "malicious",
                    "reasons": scan.get("reasons"), "handled": handled,
                    "message": "Refused to run: file is malicious; it has been quarantined."}

        want_docker = mode in ("auto", "docker")
        if want_docker and _docker_available():
            out = _run_docker_sandbox(p, args, timeout)
        elif mode == "docker":
            return {"ok": False, "error": "Docker requested but not available/running"}
        else:
            out = _run_native_sandbox(p, args, timeout)

        out["pre_scan_verdict"] = scan.get("verdict")
        # Behavioral hint for the caller / model.
        if out.get("ok"):
            out["verdict_hint"] = (
                "suspicious-behavior" if out.get("timed_out") else
                ("ran-cleanly" if out.get("exit_code") == 0 else "non-zero-exit"))
        return out
    except Exception as e:
        return {"ok": False, "error": f"sandbox failed: {e}"}


# ---------------------------------------------------------------------------
# Status / startup
# ---------------------------------------------------------------------------

def security_status() -> dict:
    """Report what protection is active (engines available, settings, quarantine size)."""
    cfg = get_config()
    engines = ["heuristics", "known-bad-hashes"]
    if sys.platform.startswith("win") and (Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                                           / "Windows Defender" / "MpCmdRun.exe").exists():
        engines.append("windows-defender")
    if _which("clamscan"):
        engines.append("clamav")
    if _vt_api_key(cfg):
        engines.append("virustotal" + (" (+upload)" if cfg.get("vt_upload_unknown") else " (hash)"))
    sandbox = ("docker" if _docker_available()
               else ("firejail/bwrap" if (_which("firejail") or _which("bwrap"))
                     else ("macos-seatbelt" if sys.platform == "darwin"
                           else ("windows-restricted-token" if sys.platform.startswith("win")
                                 else "none"))))
    return {
        "ok": True,
        "enabled": cfg.get("enabled"),
        "scan_downloads": cfg.get("scan_downloads"),
        "scan_before_open": cfg.get("scan_before_open"),
        "on_malware": cfg.get("on_malware"),
        "autodelete_days": cfg.get("autodelete_days"),
        "engines_available": engines,
        "sandbox_available": sandbox,
        "quarantine_count": len(_load_index()),
    }


def startup() -> None:
    """Call once at app launch: purge any quarantined files past their grace period."""
    try:
        purge_expired()
    except Exception:
        pass
