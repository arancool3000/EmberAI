"""Helper for RELEASE.command — the fiddly version/manifest/site-sync bits, in Python
(easier to get right than bash). Not imported by the app; only run by the release script.

Subcommands:
  bump <version|auto>          -> set version.py __version__ (auto = bump patch); prints new version
  manifest <version> <zip> <notes_file> <pub_date>
                               -> write dist/latest.json + docs/latest.json (with sha256 + url)
  sync-site                    -> sync docs/index.html CFG owner/repo from version.py
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import version  # noqa: E402


def _bump_patch(cur: str) -> str:
    parts = [int(x) for x in re.findall(r"\d+", cur)[:3]] or [1, 0, 0]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return ".".join(map(str, parts))


def set_version(newv: str) -> None:
    p = ROOT / "version.py"
    txt = p.read_text(encoding="utf-8")
    txt, n = re.subn(r'__version__ = "[^"]*"', f'__version__ = "{newv}"', txt, count=1)
    if n != 1:
        raise SystemExit("could not find __version__ in version.py")
    p.write_text(txt, encoding="utf-8")


def sync_site() -> None:
    """Write the real GitHub owner/repo into the website's app.js (single source of truth
    is version.py)."""
    importlib.reload(version)
    js = ROOT / "docs" / "app.js"
    if not js.exists():
        return
    txt = js.read_text(encoding="utf-8")
    repl = (f'const EMBER = {{ owner: "{version.GITHUB_OWNER}", '
            f'repo: "{version.GITHUB_REPO}" }};')
    txt = re.sub(r'const EMBER = \{ owner: "[^"]*", repo: "[^"]*" \};', repl, txt, count=1)
    js.write_text(txt, encoding="utf-8")


def _dl_url(plat: str) -> str:
    return (f"https://github.com/{version.GITHUB_OWNER}/{version.GITHUB_REPO}"
            f"/releases/latest/download/{version.asset_name(plat)}")


def write_manifest(plat: str, newv: str, zip_path: str, notes_file: str, pub_date: str) -> None:
    """Update latest.json for one freshly-built platform, PRESERVING the other platform's
    entry (so a mac release doesn't wipe the Windows download and vice-versa)."""
    importlib.reload(version)
    if plat not in ("macos", "windows"):
        raise SystemExit("platform must be 'macos' or 'windows'")
    zp = Path(zip_path)
    sha = hashlib.sha256(zp.read_bytes()).hexdigest()
    notes = Path(notes_file).read_text(encoding="utf-8").strip() if Path(notes_file).exists() else ""

    docs_manifest = ROOT / "docs" / version.MANIFEST_NAME
    base = {}
    if docs_manifest.exists():
        try:
            base = json.loads(docs_manifest.read_text(encoding="utf-8"))
        except Exception:
            base = {}
    downloads = base.get("downloads") or {}
    for p in ("macos", "windows"):
        d = downloads.get(p) or {}
        d.setdefault("url", _dl_url(p))
        d.setdefault("sha256", "")
        downloads[p] = d
    downloads[plat] = {"url": _dl_url(plat), "sha256": sha}  # the one we just built

    manifest = {
        "version": newv,
        "pub_date": pub_date,
        "min_macos": base.get("min_macos", "12.0"),
        "min_windows": base.get("min_windows", "10"),
        "downloads": downloads,
        "notes": notes,
    }
    blob = json.dumps(manifest, indent=2) + "\n"
    (ROOT / "dist" / version.MANIFEST_NAME).write_text(blob, encoding="utf-8")
    docs_manifest.write_text(blob, encoding="utf-8")
    print(sha)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "bump":
        arg = sys.argv[2] if len(sys.argv) > 2 else "auto"
        newv = _bump_patch(version.__version__) if arg == "auto" else arg.lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", newv):
            raise SystemExit(f"invalid version '{newv}' — use MAJOR.MINOR.PATCH")
        set_version(newv)
        print(newv)
    elif cmd == "manifest":
        # manifest <platform> <version> <zip> <notes_file> <pub_date>
        write_manifest(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
    elif cmd == "sync-site":
        sync_site()
    else:
        raise SystemExit(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    main()
