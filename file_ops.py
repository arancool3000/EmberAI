"""File organization tools: sort, dedup, rename, audit. Cross-platform (Windows + macOS)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import zipfile
import tarfile
from collections import defaultdict
from pathlib import Path


TYPE_GROUPS = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg",
                ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".ico"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".m4v", ".mpeg", ".mpg", ".flv", ".3gp"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus", ".aiff"},
    "Documents": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".tex",
                   ".pages", ".epub", ".mobi", ".azw", ".azw3"},
    "Spreadsheets": {".xls", ".xlsx", ".ods", ".csv", ".tsv", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tgz", ".tbz", ".iso", ".dmg"},
    "Code": {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".sass",
              ".go", ".rs", ".cpp", ".c", ".h", ".hpp", ".java", ".kt", ".swift",
              ".rb", ".php", ".sh", ".bash", ".ps1", ".sql", ".json", ".yaml", ".yml",
              ".xml", ".toml", ".ini", ".cfg", ".lua", ".r", ".dart"},
    "Installers": {".exe", ".msi", ".pkg", ".dmg", ".deb", ".rpm", ".appx", ".msix"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2", ".eot"},
    "Design": {".psd", ".ai", ".fig", ".sketch", ".xd", ".indd"},
    "3D": {".obj", ".fbx", ".blend", ".stl", ".gltf", ".glb", ".dae"},
    "Disk": {".iso", ".img", ".vhd", ".vmdk", ".bin", ".cue"},
}


def create_cluttered_demo_folder(path: str | None = None, overwrite: bool = False) -> dict:
    """Create a deliberately messy demo folder for testing Ember file organization."""
    base = Path(path).expanduser() if path else Path.home() / "Desktop" / "Ember_Clutter_Demo"
    if base.exists() and not overwrite:
        return {"ok": True, "path": str(base), "already_exists": True,
                "hint": "Pass overwrite=true to refresh the cluttered demo."}
    if base.exists() and overwrite:
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    files = {
        "IMG_0001 copy FINAL final.png": "fake image placeholder alpha\n",
        "IMG_0001 copy FINAL final (1).png": "fake image placeholder alpha\n",
        "IMG_0001 copy FINAL final USE THIS ONE maybe.png": "fake image placeholder alpha edited\n",
        "random screenshot 2024-01-03.png": "screenshot placeholder\n",
        "Screen Shot 2023-11-18 at 9.41.02 PM.png": "screenshot placeholder old\n",
        "tax stuff OLD.pdf": "Pretend PDF text for receipts and tax notes.\n",
        "tax stuff OLD copy.pdf": "Pretend PDF text for receipts and tax notes.\n",
        "2022 receipts maybe important.pdf": "Pretend receipt bundle.\n",
        "meeting-notes-v1.txt": "notes: move this into documents maybe\n",
        "meeting-notes-v1 copy.txt": "notes: move this into documents maybe\n",
        "meeting notes FINAL no really.txt": "notes: newer but badly named\n",
        "untitled document final FINAL.docx": "docx placeholder\n",
        "Proposal FINAL v7 final final.docx": "proposal placeholder\n",
        "Proposal FINAL v7 final final COPY.docx": "proposal placeholder\n",
        "budget - actual final final.csv": "item,cost\ncoffee,4\nhosting,12\n",
        "budget - actual final final USE THIS.csv": "item,cost\ncoffee,4\nhosting,12\nmisc,19\n",
        "contacts export (old).csv": "name,email\nAlex,alex@example.com\n",
        "song export 01.mp3": "audio placeholder\n",
        "song export 01 copy.mp3": "audio placeholder\n",
        "voice memo unknown 4.m4a": "audio memo placeholder\n",
        "video clip copy MOV.mov": "video placeholder\n",
        "video clip copy MOV (converted).mp4": "video placeholder converted\n",
        "archive of old junk.zip": "zip placeholder\n",
        "archive of old junk copy.zip": "zip placeholder\n",
        "installer copy copy.dmg": "installer placeholder\n",
        "Ember Test Installer 2 2 2.pkg": "installer placeholder\n",
        "script_test_old.py": "print('old messy demo')\n",
        "script_test_old copy.py": "print('old messy demo')\n",
        "quick hack DO NOT USE.js": "console.log('temporary');\n",
        "style backup backup.css": "body { color: red; }\n",
        "README maybe delete.md": "# Maybe delete\nThis is intentionally cluttered.\n",
        "TODO - clean all this up.md": "- dedupe\n- organize\n- rename\n",
        ".DS_Store copy": "not really ds store\n",
        "~$temporary word lock.docx": "temp lock placeholder\n",
        "untitled": "no extension, no context\n",
        "Untitled 2": "another no-extension mystery\n",
    }
    nested = {
        "New Folder/New Folder 2/where-did-this-go.txt": "deeply nested misplaced note\n",
        "New Folder/New Folder 2/New Folder 3/final final final.key": "presentation placeholder\n",
        "New Folder/New Folder 2/New Folder 3/final final final copy.key": "presentation placeholder\n",
        "Desktop dump/photo duplicate.png": "fake image placeholder beta\n",
        "Desktop dump/photo duplicate copy.png": "fake image placeholder beta\n",
        "Desktop dump/old profile pic.jpg": "jpeg placeholder\n",
        "Desktop dump/old profile pic copy.jpg": "jpeg placeholder\n",
        "Downloads dump/invoice_scan_001.pdf": "invoice placeholder\n",
        "Downloads dump/invoice_scan_001 copy.pdf": "invoice placeholder\n",
        "Downloads dump/notes notes notes.txt": "lots of notes\n",
        "Downloads dump/Installers/Chrome (1).dmg": "installer placeholder\n",
        "Downloads dump/Installers/Chrome (2).dmg": "installer placeholder\n",
        "Project Maybe/assets/logo old old old.svg": "<svg><!-- old logo --></svg>\n",
        "Project Maybe/assets/logo old old old copy.svg": "<svg><!-- old logo --></svg>\n",
        "Project Maybe/src/main copy copy.py": "print('project maybe')\n",
        "Project Maybe/src/main newer maybe.py": "print('project maybe newer')\n",
        "Finance/Q1/q1 real numbers.xlsx": "xlsx placeholder\n",
        "Finance/Q1/q1 real numbers copy.xlsx": "xlsx placeholder\n",
        "Finance/Q1/receipts/uber receipt 001.pdf": "receipt\n",
        "Finance/Q1/receipts/uber receipt 001 duplicate.pdf": "receipt\n",
        "Audio exports/bounce/bounce 1.wav": "wav placeholder\n",
        "Audio exports/bounce/bounce 1 final.wav": "wav placeholder\n",
        "Video exports/render/render.mov": "render placeholder\n",
        "Video exports/render/render final final.mov": "render placeholder\n",
        "Screenshots/Screen Shot copy copy copy.png": "screenshot duplicate\n",
        "Screenshots/Screen Shot copy copy copy (1).png": "screenshot duplicate\n",
        "Old Desktop/loose thing.rtf": "rtf placeholder\n",
        "Old Desktop/loose thing copy.rtf": "rtf placeholder\n",
    }
    for name, content in {**files, **nested}.items():
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    # Add numbered clutter so large-folder workflows have enough material to inspect.
    for i in range(1, 19):
        month = ((i - 1) % 12) + 1
        p = base / "Camera Roll dump" / f"IMG_{1000 + i} duplicate maybe {i % 4}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"camera image placeholder group {i % 4}\n", encoding="utf-8")
        q = base / "Loose PDFs" / f"scan {month:02d} - unknown - copy {i}.pdf"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(f"scan placeholder group {i % 3}\n", encoding="utf-8")
    for i in range(1, 10):
        p = base / "Temp exports" / f"export final final v{i}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    now = time.time()
    for i, p in enumerate(base.rglob("*")):
        if p.is_file():
            try:
                os.utime(p, (now - i * 86400, now - i * 86400))
            except OSError:
                pass
    return {
        "ok": True,
        "path": str(base),
        "file_count": sum(1 for p in base.rglob("*") if p.is_file()),
        "description": "Very messy demo folder with duplicates, near-duplicates, vague names, nested clutter, temp files, exports, and mixed media/document/code/archive types.",
    }


def _classify(ext: str) -> str:
    ext = ext.lower()
    for group, exts in TYPE_GROUPS.items():
        if ext in exts:
            return group
    return "Other"


def _safe_move(src: Path, dest_dir: Path, dry_run: bool) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    if target.exists() and target.resolve() != src.resolve():
        stem = src.stem
        suffix = src.suffix
        i = 1
        while True:
            target = dest_dir / f"{stem} ({i}){suffix}"
            if not target.exists():
                break
            i += 1
    if dry_run:
        return {"src": str(src), "dst": str(target), "dry": True}
    shutil.move(str(src), str(target))
    return {"src": str(src), "dst": str(target)}


def organize_folder(path: str, mode: str = "type", dry_run: bool = False,
                    include_subfolders: bool = False) -> dict:
    """Sort files in a folder into subfolders.
    mode = 'type' (grouped: Images/Videos/Documents/...) | 'extension' (by .ext) |
           'date' (YYYY-MM) | 'size' (Tiny/Small/Medium/Large/Huge)
    """
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "folder not found"}
    iterator = p.rglob("*") if include_subfolders else p.iterdir()
    moves = []
    skipped = 0
    by_group: dict[str, int] = defaultdict(int)
    for item in iterator:
        try:
            if not item.is_file():
                continue
            if item.parent != p and not include_subfolders:
                continue
            if mode == "type":
                group = _classify(item.suffix)
            elif mode == "extension":
                group = item.suffix.lower().lstrip(".") or "no_ext"
            elif mode == "date":
                ts = item.stat().st_mtime
                group = time.strftime("%Y-%m", time.localtime(ts))
            elif mode == "size":
                kb = item.stat().st_size / 1024
                if kb < 100: group = "Tiny (<100 KB)"
                elif kb < 1024: group = "Small (<1 MB)"
                elif kb < 10 * 1024: group = "Medium (<10 MB)"
                elif kb < 100 * 1024: group = "Large (<100 MB)"
                else: group = "Huge (>=100 MB)"
            else:
                return {"ok": False, "error": f"unknown mode '{mode}'"}
            dest_dir = p / group
            if dest_dir == item.parent:
                skipped += 1
                continue
            moves.append(_safe_move(item, dest_dir, dry_run))
            by_group[group] += 1
        except Exception as e:
            moves.append({"src": str(item), "error": str(e)})
    return {
        "ok": True,
        "mode": mode,
        "dry_run": dry_run,
        "moved_count": sum(1 for m in moves if "dst" in m and "error" not in m),
        "by_group": dict(by_group),
        "skipped": skipped,
        "sample": moves[:30],
    }


def find_duplicate_files(path: str, min_size_kb: int = 100, recursive: bool = True,
                          hash_algo: str = "blake2b") -> dict:
    """Find files with identical contents by hash. Groups files >= min_size_kb."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "folder not found"}
    by_size: dict[int, list[Path]] = defaultdict(list)
    walker = p.rglob("*") if recursive else p.iterdir()
    for item in walker:
        try:
            if item.is_file():
                sz = item.stat().st_size
                if sz >= min_size_kb * 1024:
                    by_size[sz].append(item)
        except Exception:
            continue

    dup_groups = []
    bytes_wasted = 0
    for size, files in by_size.items():
        if len(files) < 2:
            continue
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for f in files:
            try:
                h = hashlib.new(hash_algo)
                with f.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                by_hash[h.hexdigest()].append(f)
            except Exception:
                continue
        for hex_hash, group in by_hash.items():
            if len(group) >= 2:
                dup_groups.append({
                    "size_bytes": size,
                    "size_mb": round(size / (1024 * 1024), 2),
                    "count": len(group),
                    "hash": hex_hash[:16],
                    "files": [str(f) for f in group],
                })
                bytes_wasted += size * (len(group) - 1)

    dup_groups.sort(key=lambda g: -g["size_bytes"] * g["count"])
    return {
        "ok": True,
        "duplicate_group_count": len(dup_groups),
        "wasted_mb": round(bytes_wasted / (1024 * 1024), 1),
        "groups": dup_groups[:50],
    }


def find_large_files(path: str, min_mb: int = 100, max_results: int = 50,
                      recursive: bool = True) -> dict:
    """List the biggest files in a folder, sorted descending."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "folder not found"}
    walker = p.rglob("*") if recursive else p.iterdir()
    items = []
    threshold = min_mb * 1024 * 1024
    for item in walker:
        try:
            if item.is_file():
                size = item.stat().st_size
                if size >= threshold:
                    items.append((size, item))
        except Exception:
            continue
    items.sort(reverse=True)
    return {
        "ok": True,
        "match_count": len(items),
        "files": [
            {"path": str(it[1]), "size_mb": round(it[0] / (1024 * 1024), 2),
             "modified": time.strftime("%Y-%m-%d", time.localtime(it[1].stat().st_mtime))}
            for it in items[:max_results]
        ],
    }


def bulk_rename(folder: str, pattern: str, replacement: str,
                regex: bool = False, dry_run: bool = False) -> dict:
    """Rename files in folder by string or regex replacement."""
    p = Path(folder).expanduser()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "folder not found"}
    renames = []
    rx = re.compile(pattern) if regex else None
    for item in p.iterdir():
        try:
            if not item.is_file():
                continue
            name = item.name
            if regex:
                new_name = rx.sub(replacement, name)
            else:
                if pattern not in name:
                    continue
                new_name = name.replace(pattern, replacement)
            if new_name == name or not new_name:
                continue
            target = item.parent / new_name
            if target.exists():
                renames.append({"old": name, "new": new_name, "error": "target exists"})
                continue
            if not dry_run:
                item.rename(target)
            renames.append({"old": name, "new": new_name, "dry": dry_run})
        except Exception as e:
            renames.append({"old": item.name, "error": str(e)})
    return {
        "ok": True,
        "dry_run": dry_run,
        "rename_count": sum(1 for r in renames if "new" in r and "error" not in r),
        "renames": renames[:80],
    }


def move_matching_files(source: str, destination: str, pattern: str = "*",
                         dry_run: bool = False) -> dict:
    """Move files matching a glob from source to destination."""
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists() or not src.is_dir():
        return {"ok": False, "error": "source folder not found"}
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    moves = []
    for item in src.glob(pattern):
        try:
            if item.is_file():
                moves.append(_safe_move(item, dst, dry_run))
        except Exception as e:
            moves.append({"src": str(item), "error": str(e)})
    return {"ok": True, "moved_count": len(moves), "dry_run": dry_run, "moves": moves[:80]}


def get_folder_size(path: str, recursive: bool = True) -> dict:
    """Total size + file count of a folder."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "error": "not found"}
    if p.is_file():
        return {"ok": True, "size_mb": round(p.stat().st_size / (1024 * 1024), 2), "file_count": 1}
    total = 0
    file_count = 0
    by_type = defaultdict(lambda: [0, 0])  # [count, bytes]
    walker = p.rglob("*") if recursive else p.iterdir()
    for item in walker:
        try:
            if item.is_file():
                sz = item.stat().st_size
                total += sz
                file_count += 1
                group = _classify(item.suffix)
                by_type[group][0] += 1
                by_type[group][1] += sz
        except Exception:
            continue
    return {
        "ok": True,
        "path": str(p),
        "file_count": file_count,
        "total_size_mb": round(total / (1024 * 1024), 2),
        "total_size_gb": round(total / (1024 * 1024 * 1024), 3),
        "by_type": {k: {"count": v[0], "size_mb": round(v[1] / (1024 * 1024), 1)}
                    for k, v in sorted(by_type.items(), key=lambda x: -x[1][1])},
    }


def unzip_archive(archive_path: str, destination: str | None = None) -> dict:
    """Extract a .zip, .tar, .tar.gz, .tgz, .tar.bz2 archive."""
    p = Path(archive_path).expanduser()
    if not p.exists():
        return {"ok": False, "error": "archive not found"}
    dest = Path(destination).expanduser() if destination else p.parent / p.stem
    dest.mkdir(parents=True, exist_ok=True)
    try:
        suffix = "".join(p.suffixes).lower()
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                z.extractall(dest)
                names = z.namelist()
        elif any(suffix.endswith(s) for s in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz")):
            with tarfile.open(p) as t:
                t.extractall(dest)
                names = t.getnames()
        else:
            return {"ok": False, "error": f"unsupported archive: {suffix}"}
        return {"ok": True, "extracted_to": str(dest), "entry_count": len(names)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def zip_files(files: list, destination: str) -> dict:
    """Compress a list of file paths into a .zip."""
    try:
        dest = Path(destination).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        added = 0
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                p = Path(f).expanduser()
                if p.exists() and p.is_file():
                    z.write(p, arcname=p.name)
                    added += 1
        return {"ok": True, "archive": str(dest), "file_count": added}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def folder_tree(path: str, max_depth: int = 2, max_per_level: int = 30) -> dict:
    """Return a compact tree summary of a folder."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "not found"}
    lines = []
    def walk(d: Path, depth: int):
        if depth > max_depth:
            return
        items = []
        try:
            items = sorted(d.iterdir())[:max_per_level]
        except Exception:
            return
        for it in items:
            indent = "  " * depth
            mark = "/" if it.is_dir() else ""
            lines.append(f"{indent}{it.name}{mark}")
            if it.is_dir():
                walk(it, depth + 1)
    walk(p, 0)
    return {"ok": True, "path": str(p), "tree": "\n".join(lines[:300])}


def trash_file(path: str) -> dict:
    """Soft-delete: move to Recycle Bin (Win) / Trash (Mac) if possible, else error.
    Falls back to direct delete only with allow_hard_delete=True via the agent.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": False, "error": "not found"}
    try:
        try:
            import send2trash
            send2trash.send2trash(str(p))
            return {"ok": True, "moved_to_trash": str(p)}
        except ImportError:
            pass
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            SHFILEOPSTRUCT = type("SHFILEOPSTRUCT", (ctypes.Structure,), {
                "_fields_": [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ],
            })
            FO_DELETE = 3
            FOF_ALLOWUNDO = 0x40
            FOF_NOCONFIRMATION = 0x10
            FOF_SILENT = 0x4
            op = SHFILEOPSTRUCT()
            op.wFunc = FO_DELETE
            op.pFrom = str(p) + "\0\0"
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            if res == 0:
                return {"ok": True, "moved_to_recycle_bin": str(p)}
            return {"ok": False, "error": f"shell op failed: {res}"}
        return {"ok": False, "error": "install send2trash for cross-platform recycle support"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
