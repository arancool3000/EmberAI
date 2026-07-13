"""Create a deliberately messy folder of assorted files so you can watch Ember
organize it. Everything lands flat in one folder with chaotic names, mixed types,
duplicates, and scattered dates.

Run:  python3 make_messy_demo.py
Then ask Ember:  "organize the folder ~/Desktop/Ember Messy Demo"
"""
from __future__ import annotations

import base64
import os
import random
import time
import zipfile
from pathlib import Path

# Real tiny (1x1) image bytes so the files are valid images, not just renamed text.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
_JPG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJ"
    "DRENDg8QEBEQCgwSExIQEw8QEBD/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAA"
    "AAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q==")
_PDF = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 48>>stream\nBT /F1 18 Tf 20 120 Td (Ember demo file) Tj ET\nendstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")

_LOREM = ("Notes from the meeting. Follow up with the team on Tuesday. Budget looks fine.\n"
          "Action items: ship the build, email the client, water the plants.\n")
_CSV = "date,item,amount\n2024-01-04,Coffee,3.50\n2024-02-11,Books,42.00\n2024-03-02,Taxi,18.75\n"
_PY = "import sys\n\n\ndef main():\n    print('hello from a stray script')\n\n\nif __name__ == '__main__':\n    main()\n"
_JS = "export function add(a, b) {\n  return a + b;\n}\n"
_JSON = '{\n  "name": "untitled",\n  "version": "0.0.1",\n  "private": true\n}\n'
_HTML = "<!doctype html>\n<html><body><h1>scratch page</h1></body></html>\n"

# (filename, kind) — kind picks the content written.
FILES = [
    ("Screenshot 2025-03-14 at 10.23.11.png", "png"),
    ("Screen Shot 2024-11-02 at 9.41.55 AM.png", "png"),
    ("Screenshot 2025-01-20.png", "png"),
    ("IMG_4821.jpg", "jpg"), ("IMG_4822.jpg", "jpg"), ("IMG_4823.JPG", "jpg"),
    ("DSC00123.jpg", "jpg"), ("vacation-beach.jpg", "jpg"), ("dog (1).jpg", "jpg"),
    ("dog (1) copy.jpg", "jpg"), ("profile pic FINAL.png", "png"),
    ("resume.pdf", "pdf"), ("resume_final.pdf", "pdf"), ("resume_final_v2.pdf", "pdf"),
    ("Invoice-2024-001.pdf", "pdf"), ("Invoice-2024-002.pdf", "pdf"),
    ("lease agreement.pdf", "pdf"), ("boarding pass.pdf", "pdf"),
    ("notes.txt", "txt"), ("todo.txt", "txt"), ("meeting notes 3.txt", "txt"),
    ("Untitled.txt", "txt"), ("Untitled 2.txt", "txt"), ("random thoughts.txt", "txt"),
    ("copy of copy of doc.txt", "txt"), ("README.md", "txt"), ("ideas.md", "txt"),
    ("budget.csv", "csv"), ("expenses 2024.csv", "csv"), ("data(1).csv", "csv"),
    ("script.py", "py"), ("old_script.py", "py"), ("app.js", "js"),
    ("config.json", "json"), ("package.json", "json"), ("index.html", "html"),
    ("style.css", "txt"),
    ("backup.zip", "zip"), ("old stuff.zip", "zip"),
    ("song.mp3", "bin"), ("recording (2).mp3", "bin"), ("voice memo.m4a", "bin"),
    ("clip.mov", "bin"), ("setup.dmg", "bin"),
    ("asdkjh.dat", "bin"), ("weird_file", "txt"), ("final FINAL.docx", "bin"),
    ("tax stuff 2023.pdf", "pdf"), ("receipt_grocery.pdf", "pdf"),
    ("wallpaper.png", "png"), ("meme.jpg", "jpg"),
]


def _content(kind: str) -> bytes:
    return {
        "png": _PNG, "jpg": _JPG, "pdf": _PDF,
        "csv": _CSV.encode(), "py": _PY.encode(), "js": _JS.encode(),
        "json": _JSON.encode(), "html": _HTML.encode(),
        "txt": _LOREM.encode(), "bin": os.urandom(2048),
    }.get(kind, _LOREM.encode())


def _dest() -> Path:
    desktop = Path.home() / "Desktop"
    base = desktop if desktop.exists() else Path.home()
    return base / "Ember Messy Demo"


def build() -> Path:
    d = _dest()
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for name, kind in FILES:
        p = d / name
        if kind == "zip":
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("readme.txt", "archived junk\n")
                zf.writestr("data.csv", _CSV)
        else:
            p.write_bytes(_content(kind))
        # Scatter modified-times across the last ~2 years so date sorting is interesting.
        old = now - random.randint(0, 730) * 86400 - random.randint(0, 86400)
        try:
            os.utime(p, (old, old))
        except OSError:
            pass
    return d


if __name__ == "__main__":
    folder = build()
    print(f"Created {len(FILES)} assorted files in:\n  {folder}\n")
    print('Now ask Ember:  "organize the folder ' + str(folder) + '"')
