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
import math
import os
import re
import secrets
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
    try:
        os.chmod(base, 0o700)
    except Exception:
        pass
    return base


def _quarantine_dir() -> Path:
    d = _support_dir() / "Quarantine"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    return d


_CONFIG_LOCK = threading.RLock()
# Serializes every quarantine-index read-modify-write so concurrent quarantines can't
# clobber each other (which orphaned malware on disk with no index entry).
_INDEX_LOCK = threading.RLock()
_AUDIT_LOCK = threading.RLock()

DEFAULT_CONFIG: dict = {
    "response_policy_version": 2,
    "enabled": True,
    "scan_downloads": True,        # scan files as they finish downloading
    "scan_before_open": True,      # scan files before opening them
    "block_suspicious_open": True, # also block "suspicious" (not just "malicious") on open
    "block_on_scan_error": True,   # if a file can't be scanned, refuse to open it (fail CLOSED)
    "ai_scan_on_open": True,       # AI second-opinion on UNCONFIRMED risky files before opening
    "require_confirm_unconfirmed": True,  # hold unconfirmed risky files until the user confirms
    "vt_api_key": "",              # falls back to env VIRUSTOTAL_API_KEY / VT_API_KEY
    "vt_hash_lookup": True,        # query VirusTotal by SHA-256 (only the hash leaves)
    # Privacy boundary: hashes may be checked when configured, but private files are NEVER
    # uploaded to a third party unless the user explicitly opts in.
    "vt_upload_unknown": False,
    "on_malware": "quarantine",    # containment first; deletion requires explicit review
    "autodelete_days": 30,         # used only if legacy quarantine_autodelete is selected
    "sandbox_mode": "auto",        # auto | docker | native | off
    "max_scan_bytes": 64 * 1024 * 1024,      # files larger than this: hash + heuristics only
    "vt_upload_max_bytes": 32 * 1024 * 1024, # VirusTotal's free upload ceiling
    "vt_malicious_threshold": 3,   # >= this many AV engines flag it -> malicious
    "agent_mode": "full",          # full | restricted | read_only (agent capability cap)
    # --- stronger static analysis ---
    "entropy_scan": True,          # flag packed/encrypted executables via Shannon entropy
    "entropy_threshold": 7.2,      # >= this (bits/byte) on code content -> likely packed/encrypted
    "ioc_scan": True,              # scan script/command content for malware IOCs (fileless, LOLBins)
    "deep_static": True,           # av_static engines: PE/ELF/Mach-O + PDF actions + VBA macros + script de-obfuscation
    "scan_archives": True,         # look INSIDE zip archives for malicious members
    "archive_max_members": 200,    # max members inspected per archive
    "archive_member_max_bytes": 4 * 1024 * 1024,  # max bytes read per member
    # --- always-on fileless/behavioral protection (see fileless_guard.py) ---
    "fileless_protection": True,   # real-time monitoring of running processes / command lines
    "fileless_poll_seconds": 4,    # how often the behavioral monitor samples processes
    "fileless_auto_terminate": True,   # kill confirmed-malicious processes (maximum protection)
    # --- unified always-on active scanning (see security_center.py) ---
    "realtime_security_center": True,  # master switch for continuous multi-surface scanning
    "sc_network_scan": True,       # continuously inspect network connections / listening ports
    "sc_persistence_scan": True,   # continuously inspect autostart / persistence locations
    "sc_file_sweep": True,         # periodically sweep sensitive folders for malware
    "sc_network_interval": 20,     # seconds between network scans
    "sc_persistence_interval": 45, # seconds between persistence scans
    "sc_file_sweep_interval": 600, # seconds between sensitive-folder sweeps
    "sc_sweep_max_files": 1500,    # cap files scanned per folder per sweep (keeps it light)
    "sc_watch_roots": [],          # extra folders to sweep ([] -> sensible per-OS defaults)
    "sc_notify": False,            # push security threats to connected channels (integrations.py)
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
                loaded = json.loads(p.read_text("utf-8"))
                if isinstance(loaded, dict):
                    cfg.update(loaded)
                    # Version 1 shipped auto-delete as a default. Treat an unversioned stored
                    # value as that old default and migrate it to evidence-preserving containment.
                    if "response_policy_version" not in loaded and loaded.get("on_malware") in (
                            "delete", "quarantine_autodelete"):
                        cfg["on_malware"] = "quarantine"
                        cfg["response_policy_version"] = 2
                    if "response_policy_version" not in loaded:
                        # Version 1 also enabled third-party sample upload by default. Requiring
                        # a fresh, explicit opt-in is safer than guessing whether that old value
                        # represented informed consent.
                        cfg["vt_upload_unknown"] = False
        except Exception:
            pass
        return cfg


def set_config(**changes) -> dict:
    """Update and persist config values. Returns the new config."""
    with _CONFIG_LOCK:
        cfg = get_config()
        before = dict(cfg)
        for k, v in changes.items():
            if k in DEFAULT_CONFIG:
                cfg[k] = v
        try:
            _config_path().write_text(json.dumps(cfg, indent=2), "utf-8")
        except Exception:
            pass
        changed = sorted(k for k in changes if k in DEFAULT_CONFIG and before.get(k) != cfg.get(k))
        if changed:
            # Record names, not values: API keys and other sensitive configuration never enter
            # the audit trail.
            audit_event("configuration_changed", {"settings": changed})
        return cfg


def _vt_api_key(cfg: dict) -> str:
    return (cfg.get("vt_api_key") or os.environ.get("VIRUSTOTAL_API_KEY")
            or os.environ.get("VT_API_KEY") or "").strip()


# ---------------------------------------------------------------------------
# Security audit trail
# ---------------------------------------------------------------------------

def _audit_path() -> Path:
    return _support_dir() / "security_audit.jsonl"


def _audit_digest(entry: dict) -> str:
    unsigned = {k: v for k, v in entry.items() if k != "record_hash"}
    raw = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def audit_event(action: str, details: dict | None = None) -> dict:
    """Append a structured, hash-chained local security event.

    The chain detects accidental edits and unsophisticated tampering; it is deliberately not
    marketed as tamper-proof because an administrator with disk access can replace local state.
    """
    try:
        with _AUDIT_LOCK:
            previous = ""
            try:
                lines = _audit_path().read_text("utf-8").splitlines()
                if lines:
                    previous = str(json.loads(lines[-1]).get("record_hash") or "")
            except Exception:
                pass
            now = time.time()
            entry = {
                "event_id": hashlib.sha256(
                    f"{time.time_ns()}:{os.getpid()}:{threading.get_ident()}".encode()).hexdigest()[:20],
                "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "epoch": now,
                "action": str(action),
                "details": details or {},
                "previous_hash": previous,
            }
            entry["record_hash"] = _audit_digest(entry)
            path = _audit_path()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            return {"ok": True, "event": entry}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def security_audit(limit: int = 200) -> dict:
    """Read recent security operations and verify the retained hash chain."""
    try:
        try:
            wanted = max(1, min(5000, int(limit)))
        except (TypeError, ValueError):
            wanted = 200
        events, malformed = [], 0
        with _AUDIT_LOCK:
            for line in (_audit_path().read_text("utf-8").splitlines()
                         if _audit_path().exists() else []):
                try:
                    events.append(json.loads(line))
                except Exception:
                    malformed += 1
        integrity = malformed == 0
        previous = None
        for event in events:
            if event.get("record_hash") != _audit_digest(event):
                integrity = False
            if previous is not None and event.get("previous_hash") != previous:
                integrity = False
            previous = event.get("record_hash")
        return {"ok": True, "events": events[-wanted:], "total": len(events),
                "integrity_ok": integrity, "malformed_records": malformed,
                "integrity_note": ("Hash chain valid for the local audit file."
                                   if integrity else "The local audit file has been altered or damaged.")}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": [], "integrity_ok": False}


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

# Inherently high-entropy CONTAINERS / compressed media. Their entropy is ALWAYS ~8 bits/byte,
# so "high entropy" means nothing here — scanning it just produced false positives on every
# .dmg / .zip installer. Excluded from the entropy heuristic.
_COMPRESSED_EXTS = {
    ".dmg", ".pkg", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar",
    ".jar", ".apk", ".iso", ".cab", ".lz", ".lzma", ".br", ".whl", ".egg",
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".pdf", ".woff", ".woff2", ".ttf",
}

# Only these file types are scanned for script/IOC malware indicators. Scanning prose/data
# (.md/.txt/.pdf) or binaries for indicator STRINGS just flagged security docs and the
# scanner's own signature database — a huge false-positive source.
_SCRIPT_EXTS = {
    ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".hta", ".sh", ".bash", ".zsh", ".command", ".py", ".pyw", ".pl", ".rb", ".php",
    ".scpt", ".reg", ".lnk",
}


# AI second-opinion hook: callable(items) -> list[bool] (True = could cause REAL harm).
# Set by the UI; lets a heuristic "suspicious" flag be cleared by the model so we don't
# falsely flag source code / installers / docs.
_AI_JUDGE = None


def set_ai_judge(fn) -> None:
    global _AI_JUDGE
    _AI_JUDGE = fn


def ai_review_flagged(flagged: list[dict]) -> tuple[list[dict], list[dict]]:
    """Add an AI triage opinion to heuristic findings without changing the security verdict.

    Language models are not malware engines and must never silently clear evidence. The second
    return value remains for API compatibility but is always empty; callers should display the
    opinion as analyst context and keep the finding until a human dismisses or quarantines it.
    """
    judge = _AI_JUDGE
    review = [f for f in flagged if f.get("verdict") == "suspicious"]
    definite = [f for f in flagged if f.get("verdict") != "suspicious"]
    if not judge or not review:
        return flagged, []
    items = []
    for f in review:
        excerpt = ""
        try:
            excerpt = Path(f["path"]).read_bytes()[:4000].decode("utf-8", "replace")
        except Exception:
            pass
        items.append({"name": Path(f["path"]).name, "reasons": f.get("reasons", []),
                      "excerpt": excerpt})
    try:
        verdicts = judge(items)
    except Exception:
        return flagged, []
    kept = []
    for f, harmful in zip(review, verdicts):
        enriched = dict(f)
        enriched["ai_assessment"] = "potentially-harmful" if harmful else "likely-benign"
        enriched["ai_assessment_advisory"] = True
        kept.append(enriched)
    # Anything unjudged stays flagged.
    kept += review[len(verdicts):]
    return definite + kept, []


# ---------------------------------------------------------------------------
# Confirmed-safe allowlist + AI second opinion at OPEN time
# ---------------------------------------------------------------------------
# Files the user has explicitly confirmed are remembered by content hash, so Ember opens them
# without re-blocking. Anything NOT confirmed that looks risky is AI-scanned and held until the
# user confirms it (see gate_open).
_CLEARED_LOCK = threading.RLock()

# Risky file types we hold for confirmation even if the static scan says "clean" — these are the
# things that can actually run code on your machine.
_EXECUTABLE_EXTS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".hta", ".jar", ".app", ".dmg", ".pkg", ".command", ".sh", ".bash", ".zsh",
    ".py", ".pyw", ".rb", ".pl", ".php", ".apk", ".deb", ".rpm", ".bin", ".run", ".AppImage",
}


def _cleared_path() -> Path:
    return _support_dir() / "cleared_files.json"


def _load_cleared() -> dict:
    try:
        return json.loads(_cleared_path().read_text("utf-8")) or {}
    except Exception:
        return {}


def is_cleared(sha: str) -> bool:
    if not sha:
        return False
    with _CLEARED_LOCK:
        return sha in _load_cleared()


def confirm_file_safe(path: str) -> dict:
    """Mark a file as user-confirmed-safe (by content hash) so Ember opens it without blocking.
    Use this AFTER the user has confirmed they trust a file Ember held for review."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        sha = sha256_file(p)
    except Exception as e:
        return {"ok": False, "error": f"could not hash file: {e}"}
    with _CLEARED_LOCK:
        data = _load_cleared()
        data[sha] = {"name": p.name, "path": str(p), "ts": int(time.time())}
        try:
            _cleared_path().write_text(json.dumps(data, indent=2), "utf-8")
        except Exception as e:
            return {"ok": False, "error": f"could not save confirmation: {e}"}
    return {"ok": True, "sha256": sha, "name": p.name,
            "message": f"'{p.name}' confirmed safe — Ember will open it from now on."}


def unconfirm_file(path: str) -> dict:
    """Revoke a file's confirmed-safe status (re-arms scan + block-on-open for it)."""
    p = Path(path).expanduser()
    try:
        sha = sha256_file(p) if p.exists() and p.is_file() else ""
    except Exception:
        sha = ""
    with _CLEARED_LOCK:
        data = _load_cleared()
        removed = bool(sha and sha in data)
        if removed:
            del data[sha]
            try:
                _cleared_path().write_text(json.dumps(data, indent=2), "utf-8")
            except Exception as e:
                return {"ok": False, "error": str(e)}
    return {"ok": True, "removed": removed, "sha256": sha}


def list_cleared_files() -> dict:
    """List the files the user has confirmed safe."""
    with _CLEARED_LOCK:
        data = _load_cleared()
    files = [{"sha256": k, "name": v.get("name", ""), "path": v.get("path", ""),
              "confirmed_at": v.get("ts")} for k, v in data.items()]
    return {"ok": True, "count": len(files), "files": files}


def _is_executableish(p: Path) -> bool:
    if p.suffix.lower() in _EXECUTABLE_EXTS:
        return True
    try:                                   # Unix executable bit (e.g. a no-extension binary)
        return bool(p.stat().st_mode & 0o111) and p.is_file()
    except Exception:
        return False


def ai_assess_file(path: str, reasons: list | None = None) -> dict:
    """AI second opinion on ONE file. Returns {available, verdict} where verdict is
    'malicious' | 'clean' | 'unknown'. Uses the registered AI judge (set_ai_judge); if no AI is
    available it reports available=False so the caller falls back to heuristics."""
    judge = _AI_JUDGE
    if not judge:
        return {"available": False, "verdict": "unknown"}
    p = Path(path).expanduser()
    excerpt = ""
    try:
        excerpt = p.read_bytes()[:4000].decode("utf-8", "replace")
    except Exception:
        pass
    try:
        verdicts = judge([{"name": p.name, "reasons": list(reasons or []), "excerpt": excerpt}])
        if isinstance(verdicts, list) and verdicts:
            return {"available": True, "verdict": "malicious" if verdicts[0] else "clean"}
    except Exception:
        pass
    return {"available": False, "verdict": "unknown"}


def _ember_own_dirs() -> list:
    """Directories that ARE Ember itself — never flag our own code/signature DB/tests
    (that's why antivirus.py, test_*.py, README, etc. lit up as 'malware')."""
    dirs = []
    try:
        dirs.append(Path(__file__).resolve().parent)
    except Exception:
        pass
    try:
        if getattr(sys, "frozen", False):
            dirs.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    return dirs


# Files whose presence together fingerprints an Ember source tree. The point: Ember's own
# security modules + tests legitimately CONTAIN malware indicator strings (they're the signature
# DB / detectors), so a scan of Ember's code — the running copy OR a checkout/copy the user
# scanned — must skip the whole tree, not just the exact running directory.
_EMBER_TREE_FILES = ("agent.py", "antivirus.py", "version.py")
_EMBER_TREE_CACHE: dict = {}


def _dir_is_ember_source(d: Path) -> bool:
    """True if `d` looks like an Ember source tree: the fingerprint files are present AND
    version.py carries Ember's own marker (so a random folder that happens to hold files with
    these names isn't blindly trusted). Memoized per directory for scan-speed."""
    key = str(d)
    cached = _EMBER_TREE_CACHE.get(key)
    if cached is not None:
        return cached
    result = False
    try:
        if all((d / name).is_file() for name in _EMBER_TREE_FILES):
            vt = (d / "version.py").read_text("utf-8", "ignore")
            result = ("EmberAI" in vt) or ("GITHUB_REPO" in vt)
    except Exception:
        result = False
    _EMBER_TREE_CACHE[key] = result
    return result


def _is_ember_own(p: Path) -> bool:
    try:
        rp = p.resolve()
    except Exception:
        return False
    # 1) Under the running / installed Ember directories.
    for d in _ember_own_dirs():
        try:
            rp.relative_to(d)
            return True
        except ValueError:
            continue
    # 2) Under ANY directory that fingerprints as an Ember source tree (a checkout or copy the
    #    user scanned). Walk a bounded number of ancestors so nested files (e.g. in a subdir) are
    #    still recognized, without an unbounded climb per file on a big scan.
    try:
        ancestors = [rp.parent, *list(rp.parent.parents)[:6]]
    except Exception:
        return False
    for anc in ancestors:
        if _dir_is_ember_source(anc):
            return True
    return False

_MACRO_EXTS = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm"}


def _static_scan(path: Path, cfg: dict | None = None) -> tuple[int, list[str], dict]:
    """Heuristic analysis. Returns (score 0-100, reasons, info)."""
    score = 0
    reasons: list[str] = []
    info: dict = {}
    cfg = cfg or get_config()
    try:
        # One bounded read serves the header checks, entropy and IOC scanning.
        with open(path, "rb") as _fh:
            sample = _fh.read(262144)
    except Exception as e:
        return 0, [f"could not read file: {e}"], info
    head = sample[:8192]

    name = path.name.lower()
    suffixes = [s.lower() for s in path.suffixes]
    ext = path.suffix.lower()

    # EICAR test signature -> hard malicious signal (caller promotes to malicious).
    # Scan the whole sampled window, not just the first 8 KB, so it can't be hidden by padding.
    if EICAR_SIG in sample:
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

    # Optional on-disk byte signatures (extensible signature DB).
    try:
        for label, pat in _signature_db().get("byte_patterns", []):
            if pat and pat in sample:
                info["signature_hit"] = label
                return 100, [f"matched malware signature: {label}"], info
    except Exception:
        pass

    # Entropy: packed / encrypted executable payloads stand out sharply — but NOT for
    # compressed containers/media (.dmg/.zip/.png…), which are always ~8 bits/byte and were
    # the source of "Affinity.dmg is suspicious" false positives.
    if (cfg.get("entropy_scan", True) and (exec_kind or is_script or ext in _DANGEROUS_EXTS)
            and ext not in _COMPRESSED_EXTS):
        body = sample[512:] if len(sample) > 2048 else sample
        ent = shannon_entropy(body)
        info["entropy"] = round(ent, 2)
        if len(sample) >= 2048 and ent >= float(cfg.get("entropy_threshold", 7.2)):
            score += 25
            reasons.append(f"very high entropy ({ent:.2f} bits/byte) — likely packed/encrypted code")

    # IOC / fileless content signatures (download-exec, reverse shells, LOLBins…). Restricted
    # to actual SCRIPT files — scanning prose/source for indicator strings flagged security
    # docs and our own signature DB. And a single low/medium hit no longer flags a file: only
    # a HIGH-severity technique or 2+ distinct categories raises it to 'suspicious'.
    if cfg.get("ioc_scan", True) and (is_script or ext in _SCRIPT_EXTS):
        text = sample.decode("utf-8", "ignore")
        ioc_score, ioc_hits = scan_text_iocs(text)
        if ioc_hits:
            cats = {h["category"] for h in ioc_hits}
            has_high = any(h["severity"] == "high" for h in ioc_hits)
            info["ioc_categories"] = sorted(cats)
            info["ioc_max_severity"] = max((h["severity"] for h in ioc_hits),
                                           key=lambda s: _SEV_RANK.get(s, 0))
            for h in ioc_hits[:6]:
                reasons.append(f"malware indicator: {h['label']} [{h['category']}]")
            if has_high or len(cats) >= 2:
                score += min(ioc_score, 75)      # strong / corroborated -> can flag
            else:
                score += min(ioc_score, 20)      # lone weak hint -> note it, rarely enough alone

    # Deep static engines (av_static): PE/ELF/Mach-O structure + packer/W^X, PDF actions, VBA
    # macros, and de-obfuscated script analysis. Best-effort + lazy so a missing optional lib or
    # a parser hiccup never breaks the core scan.
    if cfg.get("deep_static", True):
        try:
            import av_static
            extra = av_static.contribute(str(path), sample, ext)
            for r in extra.get("reasons", []):
                if r not in reasons:
                    reasons.append(r)
            score += int(extra.get("score", 0))
            if extra.get("reasons"):
                info["deep_static"] = {"verdict": extra.get("verdict"),
                                       "engines": len(extra["reasons"])}
        except Exception:
            pass

    return min(score, 100), reasons, info


def _zip_has_macro(path: Path) -> bool:
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            return any(n.endswith("vbaProject.bin") for n in zf.namelist())
    except Exception:
        return False


def _scan_archive(path: Path, cfg: dict) -> dict | None:
    """Look inside a zip archive for malicious members. Returns
    {verdict, reasons, scanned} or None if it isn't a (readable) archive.

    Definitive signals (EICAR / signature byte match) inside a member -> malicious;
    disguised executables / IOC content -> suspicious. Bounded by member count and
    per-member byte caps so a zip bomb can't blow it up."""
    try:
        if not zipfile.is_zipfile(path):
            return None
    except Exception:
        return None
    max_members = int(cfg.get("archive_max_members", 200))
    member_cap = int(cfg.get("archive_member_max_bytes", 4 * 1024 * 1024))
    verdict = "clean"
    reasons: list[str] = []
    scanned = 0

    def _raise(v):
        nonlocal verdict
        if _VERDICT_ORDER[v] > _VERDICT_ORDER[verdict]:
            verdict = v

    try:
        sigs = _signature_db().get("byte_patterns", [])
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if scanned >= max_members:
                    break
                if info.is_dir():
                    continue
                # Read a BOUNDED prefix via streaming rather than skipping large members:
                # skipping on declared size let an attacker pad a member past the cap to evade
                # the in-archive EICAR/signature/IOC checks (false negative). zf.open + a capped
                # read still bounds decompression so a zip bomb can't blow up memory.
                try:
                    with zf.open(info.filename) as fh:
                        data = fh.read(member_cap + 1)[:member_cap]
                except Exception:
                    continue
                scanned += 1
                mname = info.filename
                if EICAR_SIG in data:
                    _raise("malicious")
                    reasons.append(f"archive member '{mname}' contains the EICAR test signature")
                    continue
                sig_hit = next((label for label, pat in sigs if pat and pat in data), None)
                if sig_hit:
                    _raise("malicious")
                    reasons.append(f"archive member '{mname}' matched signature: {sig_hit}")
                    continue
                ext2 = os.path.splitext(mname.lower())[1]
                exec_kind = next((k for m, k in _EXEC_MAGIC if data.startswith(m)), None)
                if exec_kind and ext2 in _BENIGN_LOOKING_EXTS:
                    _raise("suspicious")
                    reasons.append(f"archive member '{mname}': executable disguised as '{ext2}'")
                if cfg.get("ioc_scan", True):
                    sc, hits = scan_text_iocs(data.decode("utf-8", "ignore"))
                    if any(h["severity"] == "high" for h in hits) or sc >= _SUSPICIOUS_SCORE:
                        _raise("suspicious")
                        reasons.append(f"archive member '{mname}': "
                                       + "; ".join(h["label"] for h in hits[:2]))
    except Exception:
        return None
    if scanned == 0:
        return None
    return {"verdict": verdict, "reasons": reasons[:8], "scanned": scanned}


# ---------------------------------------------------------------------------
# Entropy  (packed / encrypted / obfuscated content is high-entropy)
# ---------------------------------------------------------------------------

def shannon_entropy(data: bytes) -> float:
    """Shannon entropy of `data` in bits/byte (0-8). Plain text ~4-5; native code
    ~5-6.5; packed / encrypted / compressed payloads push toward 7.5-8.0."""
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    ent = 0.0
    for c in counts:
        if c:
            p = c / n
            ent -= p * math.log2(p)
    return ent


# ---------------------------------------------------------------------------
# Behavioral IOC / signature engine
#
# Pattern-matches the *content* of scripts and the *command lines* of running
# processes for the techniques fileless malware and "living-off-the-land"
# attacks rely on (they leave little or nothing on disk): encoded PowerShell,
# download-and-execute, reverse shells, LOLBins, ransomware shadow-copy wipes,
# credential dumping, crypto-miners and heavy obfuscation. Each pattern carries
# a severity:
#   high   -> almost never benign; in process context this is "malicious".
#   medium -> strong indicator; "suspicious".
#   low    -> a weak hint that only matters when it stacks with others.
# ---------------------------------------------------------------------------

# (label, category, severity, score, regex-source)
_IOC_DEFS: list[tuple[str, str, str, int, str]] = [
    ("PowerShell -EncodedCommand payload", "encoded-powershell", "high", 80,
     r"powershell(?:\.exe)?\b[^\n]*?\s-e(?:nc|ncodedcommand|c)?\b[^\n]*?[A-Za-z0-9+/]{40,}={0,2}"),
    ("PowerShell hidden window", "evasion", "medium", 30,
     r"powershell(?:\.exe)?\b[^\n]*?-w(?:indowstyle)?\s+hidden"),
    ("PowerShell no-profile flag", "evasion", "low", 18,
     r"powershell(?:\.exe)?\b[^\n]*?-nop(?:rofile)?\b"),
    ("Download-and-execute (IEX + web client)", "download-exec", "high", 85,
     r"(?:iex|invoke-expression)\b[^\n]*?(?:downloadstring|downloaddata|downloadfile|net\.webclient|invoke-webrequest|\biwr\b|\bcurl\b|\bwget\b)"),
    ("In-memory web download (Net.WebClient)", "download-exec", "medium", 42,
     r"new-object\s+(?:system\.)?net\.webclient"),
    ("certutil download/decode (LOLBin)", "lolbin", "high", 70,
     r"certutil(?:\.exe)?\b[^\n]*?(?:-urlcache|-decode|-encode|-verifyctl|-split)"),
    ("bitsadmin transfer (LOLBin)", "lolbin", "medium", 48,
     r"bitsadmin\b[^\n]*?/transfer"),
    ("mshta remote/script payload (LOLBin)", "lolbin", "high", 62,
     r"mshta(?:\.exe)?\b[^\n]*?(?:https?:|javascript:|vbscript:)"),
    ("regsvr32 scriptlet (LOLBin)", "lolbin", "high", 62,
     r"regsvr32(?:\.exe)?\b[^\n]*?(?:/i:http|scrobj\.dll)"),
    ("rundll32 script/remote (LOLBin)", "lolbin", "medium", 48,
     r"rundll32(?:\.exe)?\b[^\n]*?(?:javascript:|\.dll\s*,|url\.dll|shell32\.dll)"),
    ("WMIC process spawn (LOLBin)", "lolbin", "medium", 48,
     r"wmic\b[^\n]*?process\b[^\n]*?call\b[^\n]*?create"),
    ("WMI/CIM remote process create", "lateral-movement", "medium", 40,
     r"(?:invoke-cimmethod|invoke-wmimethod)\b[^\n]*?create"),
    ("MSBuild inline-task abuse (LOLBin)", "lolbin", "medium", 45,
     r"msbuild(?:\.exe)?\b[^\n]*?\.(?:xml|csproj|targets)\b"),
    ("Reverse shell via /dev/tcp", "reverse-shell", "high", 90,
     r"/dev/(?:tcp|udp)/[^\s/]+/\d+"),
    ("Reverse shell via netcat -e", "reverse-shell", "high", 82,
     r"\bn(?:c|cat)\b[^\n]*?\s-[a-z]*e\b[^\n]*?(?:/bin/|cmd)"),
    ("FIFO + netcat reverse shell", "reverse-shell", "high", 80,
     r"mkfifo\b[^\n]*?(?:nc|ncat|/dev/tcp)"),
    ("curl/wget piped to a shell", "download-exec", "high", 78,
     r"(?:curl|wget)\b[^\n|]*?https?://[^\n|]*?\|\s*(?:sudo\s+)?(?:ba|z|d|c)?sh\b"),
    ("Python inline exec/eval payload", "obfuscation", "medium", 42,
     r"python[0-9.]*\s+-c\b[^\n]*?(?:exec\s*\(|eval\s*\(|__import__\s*\(|base64|socket\.socket)"),
    ("eval() of decoded data", "obfuscation", "medium", 45,
     r"eval\s*\(\s*(?:atob|base64\.b64decode|bytes\.fromhex|String\.fromCharCode)"),
    ("Base64 decode in script", "obfuscation", "low", 22,
     r"(?:frombase64string|::frombase64|\bbase64\s+-{1,2}d(?:ecode)?\b|b64decode|\batob\s*\()"),
    ("JavaScript char-code obfuscation", "obfuscation", "low", 22,
     r"String\.fromCharCode\s*\([^)]{24,}"),
    ("Large base64 blob", "obfuscation", "low", 18,
     r"[A-Za-z0-9+/]{220,}={0,2}"),
    ("Defender / AV tampering", "av-evasion", "high", 78,
     r"(?:set-mppreference\b[^\n]*?-disable|add-mppreference\b[^\n]*?-exclusion|disableantispyware|disablerealtimemonitoring|disablebehaviormonitoring)"),
    ("Shadow-copy / backup wipe (ransomware)", "ransomware", "high", 90,
     r"(?:vssadmin\b[^\n]*?delete\b[^\n]*?shadows|wbadmin\b[^\n]*?delete\b[^\n]*?catalog|bcdedit\b[^\n]*?recoveryenabled\s+no|wmic\b[^\n]*?shadowcopy\b[^\n]*?delete|delete\b[^\n]*?systemstatebackup)"),
    ("Event-log / history clearing", "anti-forensics", "medium", 42,
     r"(?:wevtutil\b[^\n]*?\bcl\b|clear-eventlog\b|\bhistory\s+-c\b|\bset\s+HISTFILE=)"),
    ("Credential dumping (LSASS / mimikatz)", "credential-theft", "high", 90,
     r"(?:sekurlsa::logonpasswords|mimikatz|privilege::debug|procdump\b[^\n]*?lsass|comsvcs\.dll\b[^\n]*?minidump|reg\s+save\s+hk(?:lm|ey_local_machine)\\?\\?sam)"),
    ("Crypto-miner pool/binary", "cryptominer", "high", 80,
     r"(?:stratum\+(?:tcp|ssl)://|\bxmrig\b|--donate-level|cryptonight|minergate|nanopool|supportxmr|\brandomx\b|pool\.minexmr)"),
    ("Persistence: registry Run key", "persistence", "medium", 38,
     r"reg(?:\.exe)?\s+add\b[^\n]*?\\(?:currentversion\\run|currentversion\\runonce|userinit|winlogon)"),
    ("Persistence: scheduled task", "persistence", "medium", 32,
     r"schtasks(?:\.exe)?\b[^\n]*?/create"),
    ("Persistence: cron / launchd / autostart", "persistence", "low", 22,
     r"(?:crontab\s+-|/etc/cron|launchctl\s+load|library/launchagents|library/launchdaemons|\.config/autostart)"),
    ("In-memory execution (memfd / fileless ELF)", "fileless", "high", 72,
     r"(?:memfd_create|/proc/self/fd/\d|\bld_preload\s*=)"),
]

_COMPILED_IOCS: list[tuple[str, str, str, int, "re.Pattern[str]"]] = []
for _label, _cat, _sev, _score, _src in _IOC_DEFS:
    try:
        _COMPILED_IOCS.append((_label, _cat, _sev, _score,
                               re.compile(_src, re.IGNORECASE | re.MULTILINE)))
    except re.error:
        pass

_SEV_RANK = {"low": 0, "medium": 1, "high": 2}


def scan_text_iocs(text: str) -> tuple[int, list[dict]]:
    """Scan a blob of text (script body or command line) for malware indicators.

    Returns (combined_score 0-100, hits) where each hit is
    {label, category, severity, score}. Each indicator counts at most once."""
    if not text:
        return 0, []
    hits: list[dict] = []
    seen: set[str] = set()
    total = 0
    for label, cat, sev, score, rx in _COMPILED_IOCS:
        if label in seen:
            continue
        try:
            if rx.search(text):
                seen.add(label)
                hits.append({"label": label, "category": cat, "severity": sev, "score": score})
                total += score
        except Exception:
            continue
    hits.sort(key=lambda h: (-_SEV_RANK.get(h["severity"], 0), -h["score"]))
    return min(total, 100), hits


def scan_command_line(cmdline: str, name: str = "") -> dict:
    """Classify a single command line / process invocation for fileless-malware
    behavior. Unlike file scanning (which never auto-deletes and so caps at
    'suspicious'), a process is live and observable, so an unambiguous (high
    severity) technique is reported as 'malicious'.

    Returns {verdict, score, reasons, categories, hits}."""
    text = (f"{name} {cmdline}" if name else (cmdline or "")).strip()
    score, hits = scan_text_iocs(text)
    has_high = any(h["severity"] == "high" for h in hits)
    has_med = any(h["severity"] == "medium" for h in hits)
    if has_high:
        verdict = "malicious"
    elif has_med or score >= _SUSPICIOUS_SCORE:
        verdict = "suspicious"
    else:
        verdict = "clean"
    return {
        "verdict": verdict,
        "score": score,
        "reasons": [f"{h['label']} [{h['category']}]" for h in hits],
        "categories": sorted({h["category"] for h in hits}),
        "hits": hits,
    }


# ---------------------------------------------------------------------------
# Extensible signature database  (optional, user/admin-updatable)
# ---------------------------------------------------------------------------

_SIG_CACHE: dict | None = None
_SIG_CACHE_MTIME: float = 0.0


def _signature_db() -> dict:
    """Load the optional on-disk signature DB (support_dir/signatures.json), cached
    by mtime. Lets the protection be strengthened without a code change:
        {"sha256": ["<hash>", ...], "patterns": [{"label","hex"|"text"}, ...],
         "bad_ips": ["1.2.3.4", ...]}
    Returns {"hashes": set[str], "byte_patterns": [(label, bytes)], "bad_ips": set[str]}."""
    global _SIG_CACHE, _SIG_CACHE_MTIME
    p = _support_dir() / "signatures.json"
    try:
        mtime = p.stat().st_mtime if p.exists() else 0.0
    except Exception:
        mtime = 0.0
    if _SIG_CACHE is not None and mtime == _SIG_CACHE_MTIME:
        return _SIG_CACHE
    hashes: set[str] = set()
    byte_patterns: list[tuple[str, bytes]] = []
    bad_ips: set[str] = set()
    try:
        if p.exists():
            raw = json.loads(p.read_text("utf-8"))
            for h in raw.get("sha256", []) or []:
                if isinstance(h, str) and len(h.strip()) == 64:
                    hashes.add(h.strip().lower())
            for sig in raw.get("patterns", []) or []:
                try:
                    label = str(sig.get("label", "signature"))
                    if sig.get("hex"):
                        byte_patterns.append((label, bytes.fromhex(sig["hex"])))
                    elif sig.get("text"):
                        byte_patterns.append((label, str(sig["text"]).encode("utf-8")))
                except Exception:
                    continue
            for ip in raw.get("bad_ips", []) or []:
                if isinstance(ip, str) and ip.strip():
                    bad_ips.add(ip.strip())
    except Exception:
        pass
    _SIG_CACHE = {"hashes": hashes, "byte_patterns": byte_patterns, "bad_ips": bad_ips}
    _SIG_CACHE_MTIME = mtime
    return _SIG_CACHE


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
        # rc == 2 (or anything else) = clamscan ERRORED (unreadable / encrypted / password-
        # protected archive, libclamav failure). That is NOT "clean" — surface it as an error
        # so the file isn't reported clean on the strength of a failed scan.
        detail = ((res.stderr or res.stdout or "").strip() or "clamscan error")[:300]
        return {"engine": "clamav", "malicious": False, "error": detail, "scan_error": True}
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


def _explain_result(path: Path, sha: str, verdict: str, score: int, reasons: list[str],
                    engines: list[str], scan_error: bool = False) -> dict:
    """Translate raw scanner output into an honest incident record for UI/export/API callers."""
    definitive = verdict == "malicious"
    review = verdict == "suspicious"
    platform_or_reputation = any(
        e in engines for e in ("windows-defender", "clamav", "virustotal"))
    if scan_error:
        assurance, confidence = "incomplete", "unknown"
    elif definitive:
        assurance, confidence = "confirmed-threat", "high"
    elif review:
        assurance, confidence = "review-required", "medium"
    else:
        assurance = "no-known-threats"
        confidence = "moderate" if platform_or_reputation else "limited"
    classification = {
        "malicious": "confirmed-malware",
        "suspicious": "suspicious-behavior",
        "clean": "no-known-threats",
    }.get(verdict, "unknown")
    detection_id = f"EMB-{sha[:16].upper()}" if sha else ""
    coverage = {
        "local_static": any(e in engines for e in
                            ("heuristics", "entropy", "ioc-signatures", "deep-static")),
        "known_bad_hashes": bool(sha),
        "platform_antivirus": any(e in engines for e in ("windows-defender", "clamav")),
        "cloud_reputation": "virustotal" in engines,
        "archive_inspection": "archive" in engines,
    }
    return {
        "detection_id": detection_id,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "classification": classification,
        "assurance": assurance,
        "confidence": confidence,
        "requires_review": review or scan_error,
        "evidence": [{"kind": "indicator", "detail": r} for r in reasons],
        "coverage": coverage,
        "privacy": {
            "file_uploaded": False,
            "note": "File contents are uploaded only when sample sharing is explicitly enabled.",
        },
    }


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
        # Never flag Ember's own code / signature DB / tests (they legitimately CONTAIN the
        # very indicator strings the scanner looks for).
        if _is_ember_own(p):
            reasons = ["Ember's own component (trusted, excluded from scanning)"]
            return {"ok": True, "path": str(p), "sha256": "", "verdict": "clean", "score": 0,
                    "reasons": reasons, "engines": ["self-exclude"], "size": size,
                    "scan_error": False, "classification": "trusted-component",
                    "assurance": "excluded", "confidence": "policy",
                    "requires_review": False, "detection_id": "", "evidence": [],
                    "coverage": {}, "privacy": {"file_uploaded": False}}
        reasons: list[str] = []
        engines: list[str] = []
        verdict = "clean"

        def _raise(v: str):
            nonlocal verdict
            if _VERDICT_ORDER[v] > _VERDICT_ORDER[verdict]:
                verdict = v

        # 1) Static heuristics (suspicious ceiling, except EICAR / signature hit).
        score, sreasons, info = _static_scan(p, cfg)
        reasons += sreasons
        engines.append("heuristics")
        if info.get("entropy") is not None:
            engines.append("entropy")
        if info.get("ioc_categories"):
            engines.append("ioc-signatures")
        if info.get("deep_static"):
            engines.append("deep-static")
        if info.get("eicar") or info.get("signature_hit"):
            _raise("malicious")
        elif score >= _SUSPICIOUS_SCORE:
            _raise("suspicious")

        # 2) Hash + known-bad list (built-in + extensible signature DB). Always the FULL-file
        # digest — a truncated hash of a >max_scan_bytes file can never match a known-bad/VT
        # hash, silently defeating hash detection on large payloads.
        sha = sha256_file(p)
        if sha in KNOWN_BAD_SHA256 or sha in _signature_db().get("hashes", set()):
            _raise("malicious")
            reasons.append("matches a known-malicious file hash")

        # 3) Platform antivirus (definitive).
        scan_errored = False
        for backend in (_scan_windows_defender, _scan_clamav):
            res = backend(p)
            if res is None:
                continue
            if res.get("scan_error"):
                # A backend that ERRORED (encrypted/corrupt archive, engine failure) has NOT
                # cleared the file. Don't list it as a passed engine and remember the failure, so
                # a "clean" verdict is never reported on the strength of a scan that didn't run.
                scan_errored = True
                engines.append(f"{res['engine']} (scan error)")
                reasons.append(f"{res['engine']} could not scan this file: "
                               f"{res.get('error', 'scan error')}")
                continue
            engines.append(res["engine"])
            if res.get("malicious"):
                _raise("malicious")
                reasons.append(f"{res['engine']} detection" +
                               (f": {res['detail']}" if res.get("detail") else ""))

        # 4) VirusTotal (hash lookup, then upload of unknowns).
        vt = None
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

        # 5) Look inside zip archives for malicious members.
        if cfg.get("scan_archives", True):
            arch = _scan_archive(p, cfg)
            if arch is not None:
                engines.append("archive")
                _raise(arch["verdict"])
                if arch["verdict"] != "clean":
                    reasons += arch["reasons"]

        reasons = reasons or ["no indicators found"]
        explained = _explain_result(p, sha, verdict, score, reasons, engines, scan_errored)
        explained["privacy"]["file_uploaded"] = bool(vt and vt.get("source") == "upload")
        return {"ok": True, "path": str(p), "sha256": sha, "verdict": verdict,
                "score": score, "reasons": reasons, "engines": engines, "size": size,
                "scan_error": scan_errored, **explained}
    except Exception as e:
        return {"ok": False, "error": f"scan failed: {e}", "verdict": "unknown"}


def scan_directory(path: str, deep: bool = False, max_files: int = 2000, progress=None,
                   cancel=None) -> dict:
    """Recursively scan a folder. Confirmed-malicious files are
    quarantined; suspicious files are reported. deep=True also consults VirusTotal.
    `progress(scanned, flagged, current_path)` is called periodically for a live UI bar.
    `cancel()` may stop a long scan without killing the worker thread."""
    try:
        root = Path(path).expanduser()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        scanned = 0
        flagged = []
        cancelled = False
        for p in root.rglob("*"):
            if cancel is not None:
                try:
                    if cancel():
                        cancelled = True
                        break
                except Exception:
                    pass
            if scanned >= max_files:
                break
            try:
                if p.is_symlink() or not p.is_file():
                    continue
            except OSError:
                continue
            if _is_ember_own(p):
                continue   # don't scan / count Ember's own files
            scanned += 1
            r = scan_file(str(p), deep=deep)
            if r.get("verdict") in ("suspicious", "malicious"):
                item = {k: r.get(k) for k in (
                    "path", "sha256", "verdict", "score", "reasons", "engines",
                    "scan_error", "detection_id", "scanned_at", "classification",
                    "assurance", "confidence", "requires_review", "evidence", "coverage",
                    "privacy")}
                if r["verdict"] == "malicious":
                    item["handled"] = _handle_malicious(str(p), r)
                flagged.append(item)
            if progress is not None and scanned % 25 == 0:
                try:
                    progress(scanned, len(flagged), str(p))
                except Exception:
                    pass
        result = {"ok": True, "root": str(root), "scanned": scanned,
                  "flagged_count": len(flagged), "flagged": flagged[:200],
                  "reached_limit": scanned >= max_files, "cancelled": cancelled,
                  "scan_mode": "cloud-reputation" if deep else "local-only"}
        audit_event("directory_scan_completed", {
            "root": str(root), "scanned": scanned, "findings": len(flagged),
            "confirmed_malware": sum(f.get("verdict") == "malicious" for f in flagged),
            "cancelled": cancelled, "mode": result["scan_mode"],
        })
        return result
    except Exception as e:
        return {"ok": False, "error": f"directory scan failed: {e}"}


# ---------------------------------------------------------------------------
# Quarantine vault
# ---------------------------------------------------------------------------

def _index_path() -> Path:
    return _quarantine_dir() / "quarantine.json"


def _load_index() -> list[dict]:
    """Always return a list of dict entries; a corrupt/old-format file degrades to [] rather
    than crashing callers (list_quarantine/purge/restore/delete)."""
    try:
        p = _index_path()
        if p.exists():
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
    except Exception:
        pass
    return []


def _save_index(entries: list[dict]) -> bool:
    """Atomically persist the index (temp file + os.replace) so an interrupted write can't
    corrupt or truncate the quarantine vault. Returns True on success."""
    try:
        p = _index_path()
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(entries, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        os.replace(tmp, p)   # atomic on POSIX & Windows (same directory)
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def quarantine_file(path: str, reasons: list[str] | None = None, sha256: str = "") -> dict:
    """Move a file into the locked quarantine vault and record it. The stored copy
    is renamed (neutralized) and made non-executable / non-readable where possible."""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return {"ok": False, "error": f"file not found: {path}"}
        cfg = get_config()
        sha = sha256 or sha256_file(p)   # full-file digest (identity / known-bad / VT)
        # Nanosecond time + random suffix prevents two identical files quarantined in the same
        # second from colliding and overwriting evidence.
        qid = f"{time.time_ns()}_{sha[:12]}_{secrets.token_hex(3)}"
        stored = _quarantine_dir() / f"{qid}_{p.name}.quarantine"
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
        with _INDEX_LOCK:
            shutil.move(str(p), str(stored))
            try:
                os.chmod(stored, stat.S_IRUSR)  # owner read-only; strip all execute bits
            except Exception:
                pass
            entries = _load_index()
            entries.append(entry)
            if not _save_index(entries):
                # Index write failed — don't leave the file orphaned in the vault with no
                # record. Roll it back to where it came from.
                try:
                    os.chmod(stored, stat.S_IRUSR | stat.S_IWUSR)
                except Exception:
                    pass
                try:
                    shutil.move(str(stored), str(p))
                except Exception:
                    pass
                return {"ok": False, "error": "could not write the quarantine index (file left in place)"}
        return {"ok": True, "quarantined": True, "id": qid, "stored_path": str(stored),
                "original_path": str(p), "delete_after": entry["delete_after"],
                "audit": audit_event("file_quarantined", {
                    "quarantine_id": qid, "original_path": str(p), "sha256": sha,
                    "reasons": (reasons or [])[:12],
                }).get("event", {}).get("event_id")}
    except Exception as e:
        return {"ok": False, "error": f"quarantine failed: {e}"}


def list_quarantine() -> dict:
    """List everything currently in quarantine (purges anything past its grace period first).
    A single malformed/old-format entry can never crash the whole listing."""
    purge_expired()
    items = []
    for e in _load_index():
        try:
            if not e.get("id"):
                continue
            qa = e.get("quarantined_at")
            items.append({
                "id": e["id"],
                "original_path": e.get("original_path", "?"),
                "sha256": e.get("sha256", ""),
                "reasons": e.get("reasons", []),
                "quarantined_at": (time.strftime("%Y-%m-%d %H:%M", time.localtime(qa)) if qa else "?"),
                "deletes_on": (time.strftime("%Y-%m-%d %H:%M", time.localtime(e["delete_after"]))
                               if e.get("delete_after") else "never"),
            })
        except Exception:
            continue
    return {"ok": True, "count": len(items), "items": items}


def verify_quarantined(id: str) -> dict:
    """Verify that a contained payload still matches the SHA-256 recorded at intake."""
    try:
        with _INDEX_LOCK:
            entry = next((e for e in _load_index() if e.get("id") == id), None)
            if not entry:
                return {"ok": False, "error": f"no quarantine entry with id {id}"}
            stored = Path(entry.get("stored_path", ""))
            if not stored.is_file():
                return {"ok": False, "integrity_ok": False,
                        "error": "quarantined payload is missing", "id": id}
            expected = str(entry.get("sha256") or "").lower()
            if not expected:
                return {"ok": False, "integrity_ok": False,
                        "error": "legacy quarantine entry has no recorded SHA-256", "id": id}
            actual = sha256_file(stored).lower()
            valid = secrets.compare_digest(actual, expected)
        if not valid:
            audit_event("quarantine_integrity_failed", {
                "quarantine_id": id, "expected_sha256": expected, "actual_sha256": actual})
        return {"ok": True, "id": id, "integrity_ok": valid,
                "sha256": actual, "expected_sha256": expected,
                "message": ("Contained payload matches its intake hash."
                            if valid else "Contained payload does not match its intake hash.")}
    except Exception as exc:
        return {"ok": False, "integrity_ok": False,
                "error": f"quarantine verification failed: {exc}", "id": id}


def restore_quarantined(id: str, destination: str = "") -> dict:
    """Move a quarantined file back to its original location (or `destination`).
    This re-arms a file you previously deemed dangerous, so callers should confirm."""
    try:
        with _INDEX_LOCK:
            entries = _load_index()
            entry = next((e for e in entries if e.get("id") == id), None)
            if not entry:
                return {"ok": False, "error": f"no quarantine entry with id {id}"}
            stored = Path(entry["stored_path"])
            if not stored.exists():
                return {"ok": False, "error": "quarantined payload is missing"}
            verification = verify_quarantined(id)
            if not verification.get("ok") or not verification.get("integrity_ok"):
                return {"ok": False, "error": (
                    "refusing to restore: contained payload integrity could not be verified"),
                    "verification": verification}
            target = Path(destination).expanduser() if destination else Path(entry["original_path"])
            if target.is_dir():
                target = target / Path(entry["original_path"]).name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Never silently clobber a file that now lives at the restore path.
            if target.exists():
                stem, suf = target.stem, target.suffix
                i = 1
                while target.exists():
                    target = target.with_name(f"{stem} (restored {i}){suf}")
                    i += 1
            try:
                os.chmod(stored, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            shutil.move(str(stored), str(target))
            if not _save_index([e for e in entries if e.get("id") != id]):
                try:
                    shutil.move(str(target), str(stored))
                    os.chmod(stored, stat.S_IRUSR)
                    return {"ok": False, "error": (
                        "could not update the quarantine index; restore was rolled back")}
                except Exception:
                    return {"ok": True, "restored_to": str(target), "id": id,
                            "warning": ("file restored, but the quarantine index could not be "
                                        "updated; the stale entry will show as missing")}
        event = audit_event("quarantine_restored", {
            "quarantine_id": id, "restored_to": str(target),
            "sha256": entry.get("sha256", ""),
        })
        return {"ok": True, "restored_to": str(target), "id": id,
                "audit": event.get("event", {}).get("event_id")}
    except Exception as e:
        return {"ok": False, "error": f"restore failed: {e}"}


def delete_quarantined(id: str) -> dict:
    """Permanently delete a single quarantined file."""
    try:
        with _INDEX_LOCK:
            entries = _load_index()
            entry = next((e for e in entries if e.get("id") == id), None)
            if not entry:
                return {"ok": False, "error": f"no quarantine entry with id {id}"}
            try:
                Path(entry["stored_path"]).unlink(missing_ok=True)
            except Exception as exc:
                # Never drop the index when payload deletion failed; that would orphan malware.
                return {"ok": False, "error": f"could not delete quarantined payload: {exc}"}
            if not _save_index([e for e in entries if e.get("id") != id]):
                return {"ok": False, "error": (
                    "payload was deleted but the quarantine index could not be updated; retry "
                    "Refresh/Delete to repair the stale record")}
        event = audit_event("quarantine_deleted", {
            "quarantine_id": id, "original_path": entry.get("original_path", "?"),
            "sha256": entry.get("sha256", ""),
        })
        return {"ok": True, "deleted": id,
                "audit": event.get("event", {}).get("event_id")}
    except Exception as e:
        return {"ok": False, "error": f"delete failed: {e}"}


def purge_expired() -> dict:
    """Delete quarantined files only when an explicit retention policy enables auto-delete.
    An entry is dropped ONLY once its file is confirmed gone — otherwise it's kept so a
    later run retries (never leave orphaned malware on disk with no index record)."""
    now = time.time()
    with _INDEX_LOCK:
        entries = _load_index()
        if get_config().get("on_malware") != "quarantine_autodelete":
            # Cancel deadlines inherited from the old default. Containment evidence is retained
            # until a human deletes it from Quarantine.
            changed = False
            for entry in entries:
                if entry.get("delete_after") is not None:
                    entry["delete_after"] = None
                    changed = True
            if changed:
                _save_index(entries)
                audit_event("legacy_auto_delete_cancelled", {"items": len(entries)})
            return {"ok": True, "purged": [], "remaining": len(entries), "failed": [],
                    "retention": "manual-review"}
        kept, removed, failed = [], [], []
        for e in entries:
            if e.get("delete_after") and now >= e["delete_after"]:
                stored = Path(e.get("stored_path", ""))
                try:
                    try:
                        os.chmod(stored, stat.S_IWUSR | stat.S_IRUSR)
                    except Exception:
                        pass
                    stored.unlink()
                    removed.append(e.get("id"))
                except FileNotFoundError:
                    removed.append(e.get("id"))     # already gone == success
                except Exception as ex:
                    kept.append(e)                  # keep so the next purge retries
                    failed.append({"id": e.get("id"), "error": str(ex)})
            else:
                kept.append(e)
        if removed:
            _save_index(kept)
    return {"ok": True, "purged": removed, "remaining": len(kept), "failed": failed}


# ---------------------------------------------------------------------------
# Gates: scan-on-download and scan-before-open
# ---------------------------------------------------------------------------

def _handle_malicious(path: str, scan: dict) -> dict:
    """Apply the configured response to a confirmed-malicious file."""
    cfg = get_config()
    action = cfg.get("on_malware", "quarantine_autodelete")
    if action == "delete":
        # Legacy configurations could delete immediately. Modern incident handling always
        # contains first so evidence is recoverable and a false positive cannot destroy data.
        audit_event("unsafe_response_policy_overridden", {
            "requested": "delete", "applied": "quarantine", "path": path})
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
    out = {k: scan.get(k) for k in (
        "verdict", "reasons", "engines", "sha256", "detection_id", "scanned_at",
        "classification", "assurance", "confidence", "requires_review", "evidence",
        "coverage", "privacy", "scan_error")}
    out.update({"ok": True, "scanned": True})
    if scan["verdict"] == "malicious":
        out["handled"] = _handle_malicious(path, scan)
        out["blocked"] = True
    return out


def gate_open(path: str) -> dict:
    """Scan a file BEFORE it is opened and decide whether opening is allowed.

    Malicious files are quarantined and hard-blocked. Anything UNCONFIRMED that looks risky
    (heuristically suspicious, an executable/script, or unscannable) gets an AI second opinion
    and is held — allowed=False, needs_confirmation=True — until the user confirms it safe
    (confirm_file_safe), which remembers it by hash so it opens freely thereafter."""
    cfg = get_config()
    if not cfg.get("enabled") or not cfg.get("scan_before_open"):
        return {"ok": True, "allowed": True, "scanned": False}
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        # Not a scannable file (e.g. a directory or app to launch by name) — let it through.
        return {"ok": True, "allowed": True, "scanned": False, "verdict": "n/a"}

    require_confirm = cfg.get("require_confirm_unconfirmed", True)
    use_ai = cfg.get("ai_scan_on_open", True)

    scan = scan_file(str(p), deep=True)
    if not scan.get("ok"):
        # A file we COULDN'T scan must not be silently allowed through (fail-open bypass).
        if cfg.get("block_on_scan_error", True):
            ai = ai_assess_file(str(p)) if use_ai else {"available": False, "verdict": "unknown"}
            return {"ok": True, "scanned": False, "verdict": "unknown", "ai_verdict": ai["verdict"],
                    "allowed": False, "blocked": True, "needs_confirmation": True,
                    "error": scan.get("error"),
                    "message": "Held for review: this file couldn't be scanned. Confirm it's safe "
                               "to open it."}
        return {"ok": True, "scanned": False, "verdict": "unknown", "allowed": True}

    sha = scan.get("sha256") or ""
    verdict = scan["verdict"]
    result = {"ok": True, "scanned": True, "verdict": verdict,
              "reasons": scan["reasons"], "engines": scan["engines"], "sha256": sha}

    # Already confirmed by the user (or AI-cleared earlier) -> open freely.
    if sha and is_cleared(sha):
        result["allowed"] = True
        result["cleared"] = True
        return result

    # Definitive malware: quarantine + hard block (a plain confirm can't override this).
    if verdict == "malicious":
        result["allowed"] = False
        result["handled"] = _handle_malicious(str(p), scan)
        result["message"] = "Blocked: this file is malicious and has been quarantined."
        return result

    # A backend couldn't finish scanning (encrypted/corrupt archive, engine error): the "clean"
    # verdict below was never actually earned by any engine. Fail CLOSED - hold for review - just
    # like a file we couldn't scan at all, instead of trusting an incomplete scan.
    if scan.get("scan_error") and cfg.get("block_on_scan_error", True):
        ai = ai_assess_file(str(p)) if use_ai else {"available": False, "verdict": "unknown"}
        result["ai_verdict"] = ai.get("verdict")
        if ai.get("verdict") == "malicious":
            result["allowed"] = False
            result["handled"] = _handle_malicious(str(p), scan)
            result["message"] = "Blocked: an AI review flagged this partly-unscannable file as malicious."
            return result
        result["allowed"] = False
        result["blocked"] = True
        result["needs_confirmation"] = True
        result["message"] = ("Held for review: part of this file couldn't be scanned "
                             "(e.g. an encrypted or corrupt archive). Confirm it's safe to open it.")
        return result

    # Decide whether this UNCONFIRMED file needs holding: heuristically suspicious, or an
    # executable/script (which can run code), or we're told to confirm everything unconfirmed.
    risky = (verdict == "suspicious") or _is_executableish(p)
    if not risky:
        result["allowed"] = True       # ordinary docs/images/text: open without nagging
        return result

    # AI second opinion on the unconfirmed risky file.
    ai = ai_assess_file(str(p), scan.get("reasons")) if use_ai else {"available": False,
                                                                     "verdict": "unknown"}
    result["ai_verdict"] = ai["verdict"]
    result["ai_available"] = ai["available"]

    if ai["verdict"] == "malicious":
        result["allowed"] = False
        result["blocked"] = True
        result["needs_confirmation"] = True
        result["message"] = ("Held: AI flagged this file as likely malicious. Only open it if "
                             "you're certain you trust it — then confirm to proceed.")
        return result

    # Not judged malicious. Hold for the user's confirmation unless that's turned off.
    if require_confirm or (verdict == "suspicious" and cfg.get("block_suspicious_open", True)):
        result["allowed"] = False
        result["blocked"] = True
        result["needs_confirmation"] = True
        result["message"] = ("Held for review: Ember hasn't confirmed this file is safe"
                             + (" (AI didn't find anything malicious). " if ai["available"]
                                else ". ")
                             + "Confirm it's safe to open it.")
        return result

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
    # Stage a COPY in a throwaway temp dir and run THAT — never chmod (+x) or execute
    # the user's original file in place. Mutating the host file's permissions, or running
    # it from its real location, are both side effects a sandbox must not have.
    import tempfile
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="ember_sbx_")
        run_path = Path(tmpdir) / (p.name or "sample")  # keep the suffix: _native_command keys on it
        shutil.copy2(p, run_path)
    except Exception as e:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return {"ok": False, "sandbox": "native", "error": f"could not stage file for sandbox: {e}"}
    cmd = _native_command(run_path, args)
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
                # NEVER run an untrusted file unconfined — that defeats the entire purpose of
                # a sandbox. Refuse (mirrors the Linux 'no sandbox' branch) instead of executing
                # it with the user's full privileges/filesystem/network.
                return {"ok": False, "sandbox": "none",
                        "error": "no sandbox available on this Windows host (install Docker "
                                 "Desktop to run untrusted files safely)"}
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
            os.chmod(run_path, os.stat(run_path).st_mode | stat.S_IXUSR)  # the copy, not the original
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
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


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
    if cfg.get("entropy_scan", True):
        engines.append("entropy")
    if cfg.get("ioc_scan", True):
        engines.append("ioc-signatures")
        engines.append("fileless-behavioral")
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
    # Real-time behavioral (fileless) monitor state, if the module is present.
    fileless_running = False
    try:
        import fileless_guard
        fileless_running = fileless_guard.is_running()
    except Exception:
        pass
    center_running = False
    try:
        import security_center
        center_running = security_center.is_running()
    except Exception:
        pass
    platform_engine = any(e in engines for e in ("windows-defender", "clamav"))
    reputation_engine = any(e.startswith("virustotal") for e in engines)
    gaps = []
    if not platform_engine:
        gaps.append("No independent platform signature engine is available.")
    if cfg.get("vt_hash_lookup", True) and not _vt_api_key(cfg):
        gaps.append("Cloud reputation is configured but no VirusTotal API key is available.")
    if cfg.get("fileless_protection", True) and not fileless_running:
        gaps.append("Behavioral process monitoring is configured but not running.")
    if cfg.get("realtime_security_center", True) and not center_running:
        gaps.append("The continuous Security Center is configured but not running.")
    if not cfg.get("scan_before_open", True):
        gaps.append("Pre-open scanning is disabled.")
    if not cfg.get("enabled", True):
        protection_state = "off"
    elif (cfg.get("fileless_protection", True) and not fileless_running) or (
            cfg.get("realtime_security_center", True) and not center_running):
        protection_state = "attention"
    elif not platform_engine and not reputation_engine:
        protection_state = "limited"
    else:
        protection_state = "protected"
    audit = security_audit(limit=1)
    return {
        "ok": True,
        "enabled": cfg.get("enabled"),
        "scan_downloads": cfg.get("scan_downloads"),
        "scan_before_open": cfg.get("scan_before_open"),
        "on_malware": cfg.get("on_malware"),
        "autodelete_days": cfg.get("autodelete_days"),
        "entropy_scan": cfg.get("entropy_scan", True),
        "ioc_scan": cfg.get("ioc_scan", True),
        "fileless_protection": cfg.get("fileless_protection", True),
        "fileless_monitor_running": fileless_running,
        "engines_available": engines,
        "sandbox_available": sandbox,
        "quarantine_count": len(_load_index()),
        "protection_state": protection_state,
        "coverage_gaps": gaps,
        "providers": {
            "local_static": True,
            "known_bad_hashes": True,
            "platform_antivirus": platform_engine,
            "cloud_reputation": reputation_engine,
            "cloud_sample_upload": bool(reputation_engine and cfg.get("vt_upload_unknown", False)),
            "behavior_monitor": fileless_running,
            "security_center": center_running,
        },
        "vt_api_key_configured": bool(_vt_api_key(cfg)),
        "audit_integrity_ok": audit.get("integrity_ok", False),
    }


def security_report(audit_limit: int = 500) -> dict:
    """Build a portable incident/protection report without exposing credentials."""
    cfg = get_config()
    safe_cfg = {k: v for k, v in cfg.items() if k != "vt_api_key"}
    safe_cfg["vt_api_key_configured"] = bool(_vt_api_key(cfg))
    quarantine = list_quarantine()
    audit = security_audit(audit_limit)
    return {
        "schema": "ember.endpoint-security-report.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {"platform": sys.platform, "hostname": os.environ.get("COMPUTERNAME") or
                 os.environ.get("HOSTNAME") or "local-host"},
        "status": security_status(),
        "policy": safe_cfg,
        "quarantine": quarantine,
        "audit": audit,
        "limitations": [
            "This report describes Ember's observable controls; it is not a compliance certification.",
            "A clean result means no known indicators were found, not that a file is provably safe.",
        ],
    }


def startup() -> None:
    """Call once at app launch: purge any quarantined files past their grace period."""
    try:
        purge_expired()
    except Exception:
        pass
