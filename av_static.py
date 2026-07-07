"""Static malware-analysis engines for Ember's antivirus — the file-parsing layer.

antivirus.py already has SHA-256 + entropy + a ~30-rule IOC regex engine + quarantine +
VirusTotal + sandbox. This module adds the deeper *static* analysis it was missing, and is
called from antivirus._static_scan (see `contribute`). Everything here is stdlib-first so it
works — and unit-tests — with no extra dependencies; heavier libraries (yara, oletools/olevba,
lief) are imported LAZILY and used only when installed, so the core app gains no hard deps and
CI stays hermetic. Install the optional engines with: pip install -r requirements-security.txt

Engines:
  * multi_hash / fuzzy_hash        — MD5 + SHA-1 + SHA-256, and a CTPH-style fuzzy hash
  * parse_binary (PE/ELF/Mach-O)   — structure + packer / W^X / no-imports heuristics (stdlib)
  * analyze_pdf                    — /JS /OpenAction /Launch /EmbeddedFile etc. (stdlib + zlib)
  * analyze_office_macros          — VBA presence + olevba when available (stdlib fallback)
  * analyze_script                 — de-obfuscation (base64/hex/char-codes) + Python AST
  * yara_scan                      — yara-python when installed (+ optional bundled rules)
  * unpack_archive                 — tar/gz (stdlib); rar/7z when the libs are installed
  * KnownGoodCache                 — sqlite cache of scanned hashes to skip rescans
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import struct
import zlib
from pathlib import Path

# Standard PE section names — anything else in an executable is mildly suspicious, and a few
# names are packer fingerprints.
_STD_PE_SECTIONS = {
    ".text", ".data", ".rdata", ".bss", ".idata", ".edata", ".pdata", ".rsrc",
    ".reloc", ".tls", ".debug", ".xdata", ".didat", "CODE", "DATA", "BSS",
}
_PACKER_SECTIONS = {
    "upx0": "UPX", "upx1": "UPX", "upx2": "UPX", ".upx": "UPX",
    ".aspack": "ASPack", ".adata": "ASPack", ".themida": "Themida", ".winlice": "WinLicense",
    ".vmp0": "VMProtect", ".vmp1": "VMProtect", ".vmp2": "VMProtect", ".enigma1": "Enigma",
    ".petite": "Petite", ".mpress1": "MPRESS", ".mpress2": "MPRESS", ".nsp0": "NsPack",
    "pec1": "PECompact", ".pklstb": "PKLite", ".yp": "Y0da", ".fsg": "FSG", ".mew": "MEW",
}
_MEM_EXECUTE = 0x20000000
_MEM_WRITE = 0x80000000


def _entropy(data: bytes) -> float:
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


# --- hashing ---------------------------------------------------------------------------

def multi_hash(path: str) -> dict:
    """MD5 + SHA-1 + SHA-256 (+ size) in a single pass. All stdlib."""
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                size += len(chunk)
                md5.update(chunk); sha1.update(chunk); sha256.update(chunk)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "md5": md5.hexdigest(), "sha1": sha1.hexdigest(),
            "sha256": sha256.hexdigest(), "size": size}


def fuzzy_hash(data: bytes, block: int = 64) -> str:
    """A CTPH-style fuzzy fingerprint: content-triggered chunk boundaries, each chunk hashed to
    one byte. Similar files share long runs of these bytes. Not ssdeep-compatible, but gives a
    fast near-duplicate signal with zero dependencies (real ssdeep is used by fuzzy_hash_file
    when the library is installed)."""
    if not data:
        return ""
    # Rolling sum over a small window; a chunk ends when (roll % block) == block-1.
    out = bytearray()
    window = bytearray()
    roll = 0
    chunk = hashlib.md5()
    any_in_chunk = False
    for byte in data:
        window.append(byte)
        roll += byte
        if len(window) > 7:
            roll -= window.pop(0)
        chunk.update(bytes((byte,)))
        any_in_chunk = True
        if roll % block == block - 1:
            out.append(chunk.digest()[0])
            chunk = hashlib.md5()
            any_in_chunk = False
    if any_in_chunk:
        out.append(chunk.digest()[0])
    import base64
    return base64.b64encode(bytes(out[:128])).decode("ascii")


def fuzzy_similar(a: str, b: str) -> float:
    """Similarity (0..1) between two fuzzy_hash signatures via sequence-match ratio — robust for
    the short, variable-length digests our CTPH produces (a real ssdeep compare when both hashes
    are ssdeep-format is left to the ssdeep lib)."""
    if not a or not b:
        return 0.0
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def fuzzy_hash_file(path: str, max_bytes: int = 8 << 20):
    """Real ssdeep hash when the library is available, else the stdlib fuzzy_hash. Returns
    {engine, hash}."""
    try:
        import ssdeep  # type: ignore
        return {"engine": "ssdeep", "hash": ssdeep.hash_from_file(path)}
    except Exception:
        try:
            import ppdeep  # type: ignore
            return {"engine": "ssdeep", "hash": ppdeep.hash_from_file(path)}
        except Exception:
            pass
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError as e:
        return {"engine": "none", "hash": "", "error": str(e)}
    return {"engine": "ctph", "hash": fuzzy_hash(data)}


# --- executable parsing (PE / ELF / Mach-O), stdlib ------------------------------------

def parse_binary(data: bytes) -> dict:
    """Dispatch on magic bytes. Returns {format, ...structure, suspicious:[...]} or
    {format:'unknown'}. Prefers `lief` for rich parsing when installed."""
    if len(data) < 4:
        return {"format": "unknown"}
    lief_res = _parse_with_lief(data)
    if lief_res is not None:
        return lief_res
    if data[:2] == b"MZ":
        return _parse_pe(data)
    if data[:4] == b"\x7fELF":
        return _parse_elf(data)
    if data[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        return _parse_macho(data)
    return {"format": "unknown"}


def _parse_with_lief(data: bytes):
    try:
        import lief  # type: ignore
    except Exception:
        return None
    try:
        parsed = lief.parse(list(data)) if hasattr(lief, "parse") else None
        if parsed is None:
            return None
        fmt = type(parsed).__module__.split(".")[-1]
        sections = []
        suspicious = []
        for s in getattr(parsed, "sections", []) or []:
            try:
                ent = float(getattr(s, "entropy", 0.0) or 0.0)
                sections.append({"name": str(s.name), "entropy": round(ent, 2),
                                 "size": int(getattr(s, "size", 0) or 0)})
                if ent >= 7.2 and int(getattr(s, "size", 0) or 0) > 1024:
                    suspicious.append(f"high-entropy section {s.name} ({ent:.1f}) — likely packed")
                pk = _PACKER_SECTIONS.get(str(s.name).lower().rstrip("\x00"))
                if pk:
                    suspicious.append(f"packer section {s.name} — {pk}")
            except Exception:
                continue
        return {"format": fmt, "engine": "lief", "sections": sections, "suspicious": suspicious}
    except Exception:
        return None


def _parse_pe(data: bytes) -> dict:
    out = {"format": "pe", "engine": "stdlib", "sections": [], "suspicious": []}
    try:
        if len(data) < 0x40:
            return out
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
            out["suspicious"].append("MZ header without a valid PE signature")
            return out
        coff = e_lfanew + 4
        (machine, num_sections, _ts, _psym, _nsym, size_opt, chars) = struct.unpack_from(
            "<HHIIIHH", data, coff)
        out["machine"] = machine
        out["is_dll"] = bool(chars & 0x2000)
        opt = coff + 20
        magic = struct.unpack_from("<H", data, opt)[0] if size_opt else 0
        out["pe_plus"] = (magic == 0x20B)
        # Import directory (data directory index 1): RVA/size. Zero size => no imports.
        dd_off = opt + (112 if magic == 0x20B else 96)
        try:
            imp_rva, imp_size = struct.unpack_from("<II", data, dd_off)
            out["has_imports"] = imp_size > 0
        except struct.error:
            out["has_imports"] = None
        sec_off = opt + size_opt
        for i in range(min(num_sections, 96)):
            base = sec_off + i * 40
            if base + 40 > len(data):
                break
            raw = struct.unpack_from("<8sIIIIIIHHI", data, base)
            name = raw[0].rstrip(b"\x00").decode("latin-1", "replace")
            vsize, _vaddr, rawsize, praw = raw[1], raw[2], raw[3], raw[4]
            scn_chars = raw[9]
            body = data[praw:praw + min(rawsize, 1 << 20)] if praw and rawsize else b""
            ent = round(_entropy(body), 2) if body else 0.0
            out["sections"].append({"name": name, "vsize": vsize, "rawsize": rawsize,
                                    "entropy": ent, "wx": bool(scn_chars & _MEM_EXECUTE and scn_chars & _MEM_WRITE)})
            pk = _PACKER_SECTIONS.get(name.lower())
            if pk:
                out["suspicious"].append(f"packer section '{name}' — {pk}")
            elif name and name not in _STD_PE_SECTIONS and not name.startswith("/"):
                out["suspicious"].append(f"non-standard section name '{name}'")
            if scn_chars & _MEM_EXECUTE and scn_chars & _MEM_WRITE:
                out["suspicious"].append(f"section '{name}' is writable AND executable (W^X violation)")
            if ent >= 7.2 and rawsize > 1024:
                out["suspicious"].append(f"high-entropy section '{name}' ({ent}) — likely packed/encrypted")
        if out.get("has_imports") is False:
            out["suspicious"].append("no import table — common in packed/self-loading malware")
    except Exception as e:
        out["parse_error"] = str(e)
    return out


def _parse_elf(data: bytes) -> dict:
    out = {"format": "elf", "engine": "stdlib", "suspicious": []}
    try:
        ei_class = data[4]          # 1=32-bit, 2=64-bit
        ei_data = data[5]           # 1=LE, 2=BE
        endian = "<" if ei_data == 1 else ">"
        out["bits"] = 64 if ei_class == 2 else 32
        e_type = struct.unpack_from(endian + "H", data, 16)[0]
        e_machine = struct.unpack_from(endian + "H", data, 18)[0]
        out["e_type"] = {1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}.get(e_type, str(e_type))
        out["machine"] = e_machine
        # Cheap heuristics on the raw image.
        if b"/bin/sh" in data or b"/bin/bash" in data:
            out["suspicious"].append("embedded shell path (/bin/sh)")
        if data.count(b"\x00") < len(data) * 0.02 and _entropy(data[:65536]) >= 7.2:
            out["suspicious"].append("very high entropy — likely packed")
    except Exception as e:
        out["parse_error"] = str(e)
    return out


def _parse_macho(data: bytes) -> dict:
    out = {"format": "macho", "engine": "stdlib", "suspicious": []}
    magic = data[:4]
    out["fat"] = magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
    out["bits"] = 64 if magic in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe") else 32
    if b"/bin/sh" in data or b"/bin/bash" in data:
        out["suspicious"].append("embedded shell path (/bin/sh)")
    return out


# --- PDF analysis (stdlib + zlib) ------------------------------------------------------

_PDF_DANGER = {
    b"/JavaScript": "JavaScript action",
    b"/JS": "JavaScript",
    b"/OpenAction": "auto-run on open",
    b"/AA": "additional (auto) actions",
    b"/Launch": "launch external program",
    b"/EmbeddedFile": "embedded file",
    b"/RichMedia": "embedded Flash/rich media",
    b"/URI": "external URI",
    b"/SubmitForm": "form data exfiltration",
    b"/GoToR": "remote go-to action",
}


def analyze_pdf(data: bytes) -> dict:
    """Scan a PDF (raw + inflated streams) for the keywords used by malicious PDFs."""
    found = {}
    haystack = data
    # Inflate FlateDecode streams so obfuscated keywords inside compressed objects are seen too.
    try:
        for m in re.finditer(rb"stream\r?\n", data):
            start = m.end()
            end = data.find(b"endstream", start)
            if end == -1:
                continue
            blob = data[start:end]
            try:
                haystack += b"\n" + zlib.decompress(blob)
            except Exception:
                continue
    except Exception:
        pass
    for needle, label in _PDF_DANGER.items():
        if needle in haystack:
            found[needle.decode("ascii")] = label
    score = 0
    if "/JavaScript" in found or "/JS" in found:
        score += 40
    if "/OpenAction" in found or "/AA" in found:
        score += 30
    if "/Launch" in found:
        score += 60
    if "/EmbeddedFile" in found or "/RichMedia" in found:
        score += 25
    verdict = "malicious" if score >= 70 else ("suspicious" if score >= 30 else "clean")
    return {"format": "pdf", "indicators": found, "score": score, "verdict": verdict}


# --- Office VBA macros -----------------------------------------------------------------

_MACRO_AUTOEXEC = ("AutoOpen", "Auto_Open", "AutoExec", "AutoClose", "Document_Open",
                   "Workbook_Open", "Document_Close", "Auto_Close")
_MACRO_SUSPECT = ("Shell", "WScript.Shell", "CreateObject", "powershell", "cmd.exe",
                  "URLDownloadToFile", "MSXML2.XMLHTTP", "WinHttp", "GetObject",
                  "Environ", "VirtualAlloc", "CallByName", "ExecuteExcel4Macro")


def analyze_office_macros(path: str) -> dict:
    """Extract + inspect VBA. Uses olevba when installed (real p-code/source), otherwise a
    stdlib fallback that finds the VBA project and scans decompressible streams for auto-exec
    and suspicious API strings."""
    try:
        from oletools.olevba import VBA_Parser  # type: ignore
        vp = VBA_Parser(path)
        if not vp.detect_vba_macros():
            return {"has_macros": False, "engine": "olevba"}
        text = "\n".join(code for (_a, _b, _c, code) in vp.extract_macros())
        auto = [k for k in _MACRO_AUTOEXEC if k.lower() in text.lower()]
        susp = [k for k in _MACRO_SUSPECT if k.lower() in text.lower()]
        return _macro_verdict(auto, susp, "olevba")
    except ImportError:
        pass
    except Exception as e:
        return {"has_macros": None, "engine": "olevba", "error": str(e)}
    return _office_macros_stdlib(path)


def _office_macros_stdlib(path: str) -> dict:
    import zipfile
    try:
        # OOXML (.docm/.xlsm) is a zip; legacy OLE (.doc) is not — best-effort on both.
        blobs = []
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                has = any(n.lower().endswith("vbaproject.bin") for n in names)
                if not has:
                    return {"has_macros": False, "engine": "stdlib"}
                for n in names:
                    if n.lower().endswith("vbaproject.bin"):
                        blobs.append(z.read(n))
        else:
            with open(path, "rb") as f:
                raw = f.read(8 << 20)
            if b"VBA" not in raw and b"_VBA_PROJECT" not in raw:
                return {"has_macros": False, "engine": "stdlib"}
            blobs.append(raw)
        hay = b" ".join(blobs)
        # VBA streams are RLE-compressed; the plain API/identifier strings usually survive as
        # readable ASCII fragments, so a raw substring scan catches the important indicators.
        text = hay.decode("latin-1", "replace")
        auto = [k for k in _MACRO_AUTOEXEC if k.lower() in text.lower()]
        susp = [k for k in _MACRO_SUSPECT if k.lower() in text.lower()]
        return _macro_verdict(auto, susp, "stdlib")
    except Exception as e:
        return {"has_macros": None, "engine": "stdlib", "error": str(e)}


def _macro_verdict(auto: list, susp: list, engine: str) -> dict:
    score = 20 + 25 * len(auto) + 15 * len(susp)
    verdict = "clean"
    if auto and susp:
        verdict = "malicious"      # auto-runs AND does something dangerous = classic maldoc
    elif auto or susp:
        verdict = "suspicious"
    return {"has_macros": True, "engine": engine, "autoexec": auto, "suspicious_apis": susp,
            "score": score, "verdict": verdict}


# --- script de-obfuscation + AST -------------------------------------------------------

_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_HEX_RE = re.compile(r"(?:\\x[0-9A-Fa-f]{2}){8,}|(?:0x[0-9A-Fa-f]{2}[, ]){8,}")
_CHARCODE_RE = re.compile(r"(?:fromCharCode|Chr[BW]?)\s*\(", re.I)
_POST_DECODE_IOC = ("iex", "invoke-expression", "downloadstring", "webclient", "frombase64string",
                    "cmd.exe", "powershell", "/bin/sh", "eval(", "wscript.shell", "createobject",
                    "reg add", "schtasks", "vssadmin", "bitsadmin", "certutil")


def _decode_layers(text: str) -> str:
    """Best-effort one-pass de-obfuscation: decode long base64 blobs and \\xNN / 0xNN runs so the
    downstream IOC scan sees the real payload instead of the obfuscation."""
    import base64
    decoded = []
    for m in _B64_RE.findall(text):
        try:
            raw = base64.b64decode(m + "=" * (-len(m) % 4), validate=False)
            # UTF-16LE is what PowerShell -EncodedCommand uses; try both.
            decoded.append(raw.decode("utf-16-le", "ignore"))
            decoded.append(raw.decode("latin-1", "ignore"))
        except Exception:
            continue
    for m in _HEX_RE.findall(text):
        hexpairs = re.findall(r"[0-9A-Fa-f]{2}", m)
        try:
            decoded.append(bytes(int(h, 16) for h in hexpairs).decode("latin-1", "ignore"))
        except Exception:
            continue
    return text + "\n" + "\n".join(decoded)


def analyze_script(text: str, kind: str = "") -> dict:
    """De-obfuscate + score a script. `kind` in {'py','js','ps','vbs',''}. For Python, also run
    an AST pass for dangerous calls (survives variable renaming)."""
    obfuscation = []
    if _CHARCODE_RE.search(text):
        obfuscation.append("char-code string building")
    if _B64_RE.search(text):
        obfuscation.append("long base64 blob")
    if _HEX_RE.search(text):
        obfuscation.append("hex-escaped byte run")
    lines = text.splitlines() or [text]
    longest = max((len(l) for l in lines), default=0)
    if longest > 1000:
        obfuscation.append("very long single line")
    ent = _entropy(text.encode("utf-8", "replace")[:65536])

    expanded = _decode_layers(text).lower()
    iocs = sorted({tok for tok in _POST_DECODE_IOC if tok in expanded})

    ast_hits = []
    if kind == "py" or (not kind and "import " in text and "def " in text):
        ast_hits = _python_ast_findings(text)

    score = 20 * len(iocs) + 10 * len(obfuscation) + 25 * len(ast_hits)
    if ent >= 5.2 and obfuscation:
        score += 15
    verdict = "malicious" if (iocs and (obfuscation or ast_hits)) or score >= 70 else (
        "suspicious" if score >= 25 else "clean")
    return {"verdict": verdict, "score": score, "iocs": iocs, "obfuscation": obfuscation,
            "ast_findings": ast_hits, "entropy": round(ent, 2)}


def _python_ast_findings(text: str) -> list:
    import ast
    dangerous = {"eval", "exec", "compile", "__import__"}
    dangerous_attr = {("os", "system"), ("os", "popen"), ("subprocess", "Popen"),
                      ("subprocess", "call"), ("subprocess", "run"), ("pty", "spawn")}
    hits = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in dangerous:
                hits.append(fn.id)
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if (fn.value.id, fn.attr) in dangerous_attr:
                    hits.append(f"{fn.value.id}.{fn.attr}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("socket", "ctypes") and a.name not in hits:
                    hits.append(f"import {a.name}")
    return sorted(set(hits))


# --- YARA ------------------------------------------------------------------------------

def yara_scan(path: str, rules_path: str | None = None) -> dict:
    """Scan a file with yara-python when installed. Returns {available, matches:[...]}.

    Degrades to {available:False} when the yara package or a rules file isn't present — the
    caller treats that as "engine not installed", not "clean"."""
    try:
        import yara  # type: ignore
    except Exception:
        return {"available": False, "reason": "yara-python not installed"}
    rp = rules_path or os.environ.get("EMBER_YARA_RULES")
    if not rp or not os.path.exists(rp):
        return {"available": False, "reason": "no rules file (set EMBER_YARA_RULES or pass rules_path)"}
    try:
        rules = yara.compile(filepath=rp)
        matches = rules.match(path, timeout=30)
        names = [m.rule for m in matches]
        return {"available": True, "matches": names,
                "verdict": "malicious" if names else "clean"}
    except Exception as e:
        return {"available": True, "matches": [], "error": str(e)}


# --- archive unpacking -----------------------------------------------------------------

def unpack_archive(path: str, max_members: int = 200, member_max: int = 8 << 20) -> dict:
    """Yield archive members' (name, bytes-prefix) for scanning. tar/gz/tgz via stdlib; rar/7z
    when rarfile/py7zr are installed. Returns {ok, format, members:[(name, bytes)]}. ZIP is
    already handled by antivirus._scan_archive, so it's not duplicated here."""
    lower = path.lower()
    members = []
    try:
        if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".gz")):
            import tarfile
            if lower.endswith(".gz") and not lower.endswith(".tar.gz"):
                import gzip
                with gzip.open(path, "rb") as f:
                    members.append((Path(path).stem, f.read(member_max)))
                return {"ok": True, "format": "gzip", "members": members}
            with tarfile.open(path) as tf:
                for m in tf.getmembers()[:max_members]:
                    if not m.isfile():
                        continue
                    f = tf.extractfile(m)
                    if f:
                        members.append((m.name, f.read(member_max)))
            return {"ok": True, "format": "tar", "members": members}
        if lower.endswith(".rar"):
            import rarfile  # type: ignore
            with rarfile.RarFile(path) as rf:
                for info in rf.infolist()[:max_members]:
                    if info.isdir():
                        continue
                    members.append((info.filename, rf.read(info)[:member_max]))
            return {"ok": True, "format": "rar", "members": members}
        if lower.endswith((".7z",)):
            import py7zr  # type: ignore
            with py7zr.SevenZipFile(path, "r") as z:
                for name, bio in (z.readall() or {}).items():
                    members.append((name, bio.read()[:member_max]))
                    if len(members) >= max_members:
                        break
            return {"ok": True, "format": "7z", "members": members}
    except ImportError as e:
        return {"ok": False, "error": f"optional unpacker not installed: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "unsupported archive type"}


# --- known-good cache ------------------------------------------------------------------

class KnownGoodCache:
    """SQLite cache of scan verdicts keyed by sha256, so unchanged files are not re-scanned.
    Best-effort: any sqlite error degrades to a no-op cache (scanning still works)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ok = False
        try:
            import sqlite3
            self._sqlite3 = sqlite3
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE IF NOT EXISTS scanned "
                        "(sha256 TEXT PRIMARY KEY, verdict TEXT, size INTEGER, ts REAL)")
            con.commit(); con.close()
            self._ok = True
        except Exception:
            self._ok = False

    def get(self, sha256: str):
        if not self._ok:
            return None
        try:
            con = self._sqlite3.connect(self.db_path)
            row = con.execute("SELECT verdict FROM scanned WHERE sha256=?", (sha256,)).fetchone()
            con.close()
            return row[0] if row else None
        except Exception:
            return None

    def put(self, sha256: str, verdict: str, size: int = 0, ts: float = 0.0):
        if not self._ok:
            return
        try:
            con = self._sqlite3.connect(self.db_path)
            con.execute("INSERT OR REPLACE INTO scanned VALUES (?,?,?,?)",
                        (sha256, verdict, int(size), float(ts)))
            con.commit(); con.close()
        except Exception:
            pass


# --- integration entry point (called from antivirus._static_scan) ----------------------

def contribute(path: str, data: bytes, ext: str) -> dict:
    """Run the applicable deep engines for a file and return a merged
    {verdict, score, reasons:[...]} that antivirus.py folds into its own verdict. Never raises
    — a failure in any engine degrades to 'no extra signal'."""
    reasons = []
    score = 0
    verdict = "clean"
    ext = (ext or "").lower()

    def _bump(v):
        nonlocal verdict
        order = {"clean": 0, "suspicious": 1, "malicious": 2}
        if order.get(v, 0) > order.get(verdict, 0):
            verdict = v

    try:
        if ext == ".pdf" or data[:5] == b"%PDF-":
            r = analyze_pdf(data)
            if r["indicators"]:
                reasons += [f"PDF: {v}" for v in r["indicators"].values()]
                score += r["score"]; _bump(r["verdict"])
        elif ext in (".docm", ".xlsm", ".pptm", ".doc", ".xls", ".dotm", ".xlam"):
            r = analyze_office_macros(path)
            if r.get("has_macros"):
                if r.get("suspicious_apis"):
                    reasons.append("VBA macro uses: " + ", ".join(r["suspicious_apis"][:6]))
                if r.get("autoexec"):
                    reasons.append("VBA auto-runs on open: " + ", ".join(r["autoexec"]))
                score += r.get("score", 0); _bump(r.get("verdict", "clean"))
        elif ext in (".js", ".ps1", ".psm1", ".vbs", ".vbe", ".wsf", ".hta", ".bat", ".cmd", ".sh", ".py"):
            kind = {".py": "py", ".js": "js", ".ps1": "ps", ".psm1": "ps",
                    ".vbs": "vbs", ".vbe": "vbs"}.get(ext, "")
            r = analyze_script(data.decode("utf-8", "replace"), kind)
            if r["iocs"] or r["ast_findings"] or r["obfuscation"]:
                if r["iocs"]:
                    reasons.append("script IOCs after de-obfuscation: " + ", ".join(r["iocs"][:6]))
                if r["ast_findings"]:
                    reasons.append("dangerous calls (AST): " + ", ".join(r["ast_findings"][:6]))
                if r["obfuscation"]:
                    reasons.append("obfuscation: " + ", ".join(r["obfuscation"]))
                score += r["score"]; _bump(r["verdict"])
        elif data[:2] == b"MZ" or data[:4] == b"\x7fELF" or data[:4] in (
                b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
            r = parse_binary(data)
            for s in r.get("suspicious", []):
                reasons.append(f"{r.get('format', 'bin')}: {s}")
            if r.get("suspicious"):
                score += 20 * len(r["suspicious"])
                _bump("suspicious")   # structural flags are suspicious, not conclusively malicious
    except Exception:
        pass
    return {"verdict": verdict, "score": min(score, 100), "reasons": reasons}
