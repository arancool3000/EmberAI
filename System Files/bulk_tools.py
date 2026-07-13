"""Bulk productivity — the "give Ember a messy folder and it sorts/reads it" toolset.

Local and free (no API/keys): triage a folder, read across many documents at once (PDF/Word/
Excel/text), and tidy up by sorting into category subfolders or batch-renaming. The mutating
tools default to a DRY RUN (apply=False) so Ember can show the plan and get your OK before it
moves or renames anything.

Pure-ish + testable: everything is stdlib + pathlib/shutil; document text reuses
power_tools.read_document (imported lazily) with a plain-text fallback. Every tool returns
{"ok": True, ...} or {"ok": False, "error": "..."} and never raises.
"""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# Extension -> category (for folder reports + organize-by-type).
_CATEGORIES = {
    "Documents": {"pdf", "doc", "docx", "txt", "md", "rtf", "odt", "pages", "tex",
                  "ppt", "pptx", "key", "xls", "xlsx", "csv", "numbers", "epub"},
    "Images": {"png", "jpg", "jpeg", "gif", "heic", "heif", "webp", "svg", "bmp", "tiff", "ico"},
    "Video": {"mp4", "mov", "avi", "mkv", "webm", "m4v", "flv", "wmv"},
    "Audio": {"mp3", "wav", "m4a", "flac", "aac", "ogg", "aiff"},
    "Archives": {"zip", "tar", "gz", "tgz", "bz2", "rar", "7z", "dmg", "pkg", "iso"},
    "Code": {"py", "js", "ts", "tsx", "jsx", "html", "css", "json", "yaml", "yml", "sh",
             "c", "h", "cpp", "java", "go", "rs", "rb", "php", "swift", "kt", "sql"},
    "Apps": {"app", "exe", "msi", "deb", "appimage"},
}
_DOC_EXTS = _CATEGORIES["Documents"]


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(path or "")))


def _category_of(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    for cat, exts in _CATEGORIES.items():
        if ext in exts:
            return cat
    return "Other"


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _top_level_files(folder: Path):
    """Visible (non-hidden) regular files directly in `folder`."""
    out = []
    try:
        for p in folder.iterdir():
            if p.name.startswith(".") or not p.is_file():
                continue
            out.append(p)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Read-only: triage + bulk read
# ---------------------------------------------------------------------------
def folder_report(path: str) -> dict:
    """A quick triage of a folder: how many files, a breakdown by category, total size, the
    biggest and newest files, and how many are readable documents. Great first step before
    organising or summarising."""
    folder = _expand(path)
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "error": f"not a folder: {path}"}
    files = _top_level_files(folder)
    cats = Counter(_category_of(p) for p in files)
    total = 0
    sized = []
    for p in files:
        try:
            s = p.stat().st_size
        except Exception:
            s = 0
        total += s
        sized.append((p, s))
    biggest = sorted(sized, key=lambda t: t[1], reverse=True)[:5]
    newest = sorted(files, key=lambda p: _safe_mtime(p), reverse=True)[:5]
    docs = [p for p in files if p.suffix.lower().lstrip(".") in _DOC_EXTS]
    subfolders = sum(1 for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
    return {
        "ok": True, "folder": str(folder), "file_count": len(files), "subfolders": subfolders,
        "total_size": _human_size(total), "total_bytes": total, "documents": len(docs),
        "by_category": dict(cats.most_common()),
        "biggest": [{"name": p.name, "size": _human_size(s)} for p, s in biggest],
        "newest": [{"name": p.name,
                    "modified": datetime.fromtimestamp(_safe_mtime(p)).strftime("%Y-%m-%d %H:%M")}
                   for p in newest],
    }


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


def bulk_read_documents(path: str, pattern: str = "*", max_files: int = 20,
                        max_chars_each: int = 2000) -> dict:
    """Extract text from MANY documents at once (PDF / Word / Excel / text / csv / md) so Ember
    can summarise, compare, or answer questions across a whole folder. Returns each file's name
    + a text excerpt; use a glob `pattern` (e.g. '*.pdf') to narrow it."""
    folder = _expand(path)
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "error": f"not a folder: {path}"}
    try:
        max_files = max(1, min(100, int(max_files)))
        max_chars_each = max(200, min(20000, int(max_chars_each)))
    except Exception:
        max_files, max_chars_each = 20, 2000
    matches = sorted((p for p in folder.glob(pattern or "*")
                      if p.is_file() and not p.name.startswith(".")
                      and p.suffix.lower().lstrip(".") in _DOC_EXTS),
                     key=_safe_mtime, reverse=True)
    truncated = len(matches) > max_files
    matches = matches[:max_files]
    docs = []
    for p in matches:
        text = _read_doc_text(p, max_chars_each)
        docs.append({"name": p.name, "chars": len(text), "text": text})
    return {"ok": True, "folder": str(folder), "count": len(docs),
            "truncated": truncated, "documents": docs}


def _read_doc_text(p: Path, max_chars: int) -> str:
    try:
        import power_tools
        r = power_tools.read_document(str(p), max_chars=max_chars)
        if isinstance(r, dict) and r.get("ok") and r.get("text"):
            return r["text"][:max_chars]
    except Exception:
        pass
    # Fallback for plain-text-ish files.
    if p.suffix.lower().lstrip(".") in {"txt", "md", "csv", "json", "log", "tex", "rtf"}:
        try:
            return p.read_text("utf-8", "replace")[:max_chars]
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------------------
# Mutating: organise + bulk rename (default DRY RUN)
# ---------------------------------------------------------------------------
def _unique_dest(dest: Path) -> Path:
    """Avoid clobbering: append ' (2)', ' (3)'… if dest exists."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def organize_folder(path: str, by: str = "type", apply: bool = False) -> dict:
    """Sort a folder's files into subfolders. by='type' -> Documents/Images/Video/… ; by='date'
    -> YYYY-MM folders. DEFAULT is a dry run (apply=False) that returns the plan; call again with
    apply=True ONLY after the user approves. Files already inside category/date subfolders are
    left alone; name clashes get a ' (2)' suffix instead of overwriting."""
    folder = _expand(path)
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "error": f"not a folder: {path}"}
    by = (by or "type").lower()
    if by not in ("type", "date"):
        return {"ok": False, "error": "by must be 'type' or 'date'"}
    plan = []
    for p in _top_level_files(folder):
        if by == "type":
            bucket = _category_of(p)
        else:
            bucket = datetime.fromtimestamp(_safe_mtime(p)).strftime("%Y-%m")
        dest_dir = folder / bucket
        plan.append((p, dest_dir / p.name, bucket))
    counts = Counter(b for _, _, b in plan)
    if not apply:
        return {"ok": True, "dry_run": True, "folder": str(folder), "by": by,
                "would_move": len(plan), "by_bucket": dict(counts.most_common()),
                "message": f"Plan: sort {len(plan)} files into {len(counts)} folders by {by}. "
                           "Call again with apply=true to do it."}
    moved = 0
    errors = []
    for src, dest, _bucket in plan:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(_unique_dest(dest)))
            moved += 1
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return {"ok": True, "dry_run": False, "folder": str(folder), "by": by,
            "moved": moved, "by_bucket": dict(counts.most_common()),
            "errors": errors[:10],
            "message": f"Sorted {moved} files into {len(counts)} folders by {by}."}


def bulk_rename(path: str, find: str, replace: str = "", pattern: str = "*",
                apply: bool = False) -> dict:
    """Batch-rename files by replacing text in their names (find -> replace) for files matching a
    glob `pattern`. DEFAULT is a dry run (apply=False) returning the planned renames; call again
    with apply=True after the user approves. Skips no-ops and avoids clobbering existing names."""
    folder = _expand(path)
    if not folder.exists() or not folder.is_dir():
        return {"ok": False, "error": f"not a folder: {path}"}
    if not find:
        return {"ok": False, "error": "a 'find' string is required"}
    renames = []
    for p in sorted(folder.glob(pattern or "*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        new_name = p.name.replace(find, replace)
        if new_name == p.name or not new_name.strip():
            continue
        renames.append((p, p.with_name(new_name)))
    if not apply:
        return {"ok": True, "dry_run": True, "folder": str(folder), "would_rename": len(renames),
                "examples": [{"from": s.name, "to": d.name} for s, d in renames[:15]],
                "message": f"Plan: rename {len(renames)} files. Call again with apply=true to do it."}
    done = 0
    errors = []
    for src, dest in renames:
        try:
            src.rename(_unique_dest(dest))
            done += 1
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return {"ok": True, "dry_run": False, "folder": str(folder), "renamed": done,
            "errors": errors[:10], "message": f"Renamed {done} files."}


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {"name": "folder_report",
     "description": "Triage a folder: file count, breakdown by category, total size, biggest + "
                    "newest files, and how many are readable documents. Use before organising or "
                    "summarising a folder.",
     "parameters": {"type": "OBJECT", "properties": {"path": {"type": "STRING"}},
                    "required": ["path"]}},
    {"name": "bulk_read_documents",
     "description": "Extract text from MANY documents at once (PDF/Word/Excel/text/csv/md) so you "
                    "can summarise or answer questions across a whole folder. Optional glob "
                    "`pattern` (e.g. '*.pdf').",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "pattern": {"type": "STRING"},
        "max_files": {"type": "INTEGER"}, "max_chars_each": {"type": "INTEGER"}},
        "required": ["path"]}},
    {"name": "organize_folder",
     "description": "Sort a folder's files into subfolders by 'type' (Documents/Images/…) or "
                    "'date' (YYYY-MM). Defaults to a DRY RUN (apply=false) that returns the plan — "
                    "show it to the user, then call again with apply=true once they approve.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "by": {"type": "STRING", "description": "'type' or 'date'"},
        "apply": {"type": "BOOLEAN", "description": "false = preview (default); true = do it"}},
        "required": ["path"]}},
    {"name": "bulk_rename",
     "description": "Batch-rename files by replacing text in their names (find -> replace) for "
                    "files matching a glob pattern. Defaults to a DRY RUN (apply=false); call "
                    "again with apply=true after the user approves.",
     "parameters": {"type": "OBJECT", "properties": {
        "path": {"type": "STRING"}, "find": {"type": "STRING"}, "replace": {"type": "STRING"},
        "pattern": {"type": "STRING"}, "apply": {"type": "BOOLEAN"}},
        "required": ["path", "find"]}},
]

TOOL_DISPATCH = {
    "folder_report": folder_report,
    "bulk_read_documents": bulk_read_documents,
    "organize_folder": organize_folder,
    "bulk_rename": bulk_rename,
}

# Reading/triage is read-only. Organise/rename mutate files but default to a safe dry run and
# only move/rename (reversible) — low-risk interactions; Ember previews + confirms before apply.
READONLY_TOOLS = {"folder_report", "bulk_read_documents"}
INTERACTION_TOOLS = {"organize_folder", "bulk_rename"}
