"""Sort protected photos into albums without ever exposing them.

The awkward part of encrypting your photos is that they stop being browsable. Filenames are all
you have left, and `IMG_0042.JPG.ember` tells you nothing. This module lets Ember look inside —
in memory, never on disk — group the photos, and file them away still encrypted.

Two rules it will not bend
--------------------------
1. **The plaintext never touches the disk.** Each photo is decrypted into memory, shown to a
   model, and discarded. What gets moved is the encrypted file.
2. **The classifier is local by default.** Sorting photos by content means showing them to a
   model, and shipping the decrypted contents of a privacy product's photos to a cloud API is a
   contradiction. Ollama on this machine is the default; a cloud provider requires
   ``allow_cloud=True`` and says exactly what it is about to do.

The metadata problem, stated plainly
------------------------------------
Folder and file names are NOT encrypted — iCloud can see them. Sorting into folders called
"Passport", "Medical" or "Receipts" hands Apple a searchable index of exactly the things you
encrypted the photos to hide. That is worse than leaving them unsorted.

So ``private_names=True`` (the default) writes opaque folders — ``group-01``, ``group-02`` — and
keeps the real album names in an index that is itself encrypted with the same key as the photos.
``private_names=False`` uses readable folder names and the tool result says what that costs.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import data_protect

#: Kept deliberately short and concrete. A free-form label per photo produces 200 albums of one
#: photo each; a fixed vocabulary produces albums a person can actually use.
CATEGORIES = [
    "people", "documents", "receipts", "screenshots", "places",
    "food", "animals", "events", "objects", "other",
]

_PROMPT = ("Put this photo in exactly one category from this list, answering with the single "
           "word only and nothing else: " + ", ".join(CATEGORIES) + ".")

INDEX_NAME = "albums.json.ember"


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------
def _normalise(answer: str) -> str:
    a = (answer or "").strip().lower()
    for c in CATEGORIES:
        if c in a:
            return c
    return "other"


NO_LOCAL_MODEL = ("no local vision model. Install Ollama from ollama.com, then: "
                  "ollama pull llava")


def local_vision_models() -> list[str]:
    """Vision-capable models available in the local Ollama, newest-listed first."""
    try:
        import local_ai
        st = local_ai.local_ai_status()
        return [m for m in (st.get("models") or [])
                if any(v in m for v in ("llava", "vision", "moondream", "gemma3"))]
    except Exception:
        return []


def classify_local(image: bytes, model: str = "") -> dict:
    """Classify with a local Ollama vision model. Nothing leaves this machine."""
    try:
        import requests
        if not model:
            models = local_vision_models()
            if not models:
                return {"ok": False, "error": NO_LOCAL_MODEL}
            model = models[0]
        r = requests.post("http://localhost:11434/api/generate", timeout=180, json={
            "model": model, "prompt": _PROMPT, "stream": False,
            "images": [base64.b64encode(image).decode()]})
        if r.status_code != 200:
            return {"ok": False, "error": f"ollama returned {r.status_code}"}
        return {"ok": True, "category": _normalise(r.json().get("response", "")), "model": model}
    except Exception as e:
        return {"ok": False, "error": f"local AI unavailable: {e}"}


def classify_cloud(image: bytes, suffix: str = ".jpg") -> dict:
    """Classify with the user's configured cloud model. Sends the DECRYPTED photo off-machine —
    only ever reached when the caller passed allow_cloud=True."""
    import tempfile
    import creative
    # describe_image takes a path, so this is the one place a plaintext byte-string touches the
    # filesystem. Keep it in the OS temp dir, and shred it immediately whatever happens.
    tmp = Path(tempfile.mkdtemp()) / f"ember-classify{suffix}"
    try:
        tmp.write_bytes(image)
        r = creative.describe_image(str(tmp), _PROMPT)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "vision failed")}
        return {"ok": True, "category": _normalise(r.get("answer", "")), "model": "cloud"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        data_protect._shred(tmp)
        try:
            tmp.parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Album index (encrypted, so the mapping itself leaks nothing)
# ---------------------------------------------------------------------------
def _read_index(folder: Path, pw: str) -> dict:
    p = folder / INDEX_NAME
    if not p.exists():
        return {}
    try:
        blob = p.read_bytes()
        data, _cek, _slots = data_protect._open(blob, pw)
        out = json.loads(data.decode("utf-8"))
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _write_index(folder: Path, pw: str, index: dict) -> None:
    data_protect.encrypt_bytes(json.dumps(index, indent=2).encode("utf-8"),
                               folder / INDEX_NAME, pw, data_protect.load_recipients())


# ---------------------------------------------------------------------------
# The work
# ---------------------------------------------------------------------------
def organise(folder: str, allow_cloud: bool = False, private_names: bool = True,
             recursive: bool = False, limit: int = 0) -> dict:
    """Sort protected photos into albums, leaving them encrypted throughout."""
    pw = data_protect._passphrase()
    if pw is None:
        return {"ok": False, "error": "data protection is not set up — run adp_setup first"}
    root = Path(str(folder)).expanduser()
    if not root.is_dir():
        return {"ok": False, "error": f"no such folder: {root}"}

    files = [f for f in sorted(root.rglob("*") if recursive else root.glob("*"))
             if f.is_file() and f.suffix == data_protect.EXT and f.name != INDEX_NAME]
    if limit and limit > 0:
        files = files[:limit]
    if not files:
        return {"ok": True, "sorted_count": 0, "albums": {}, "note": "no protected files here"}

    # Check availability by asking what models exist, not by running the classifier on a dummy
    # image — a probe that pretends to classify is a lie in the call log and in any usage meter.
    if not allow_cloud and not local_vision_models():
        return {"ok": False, "error": NO_LOCAL_MODEL,
                "hint": "Or pass allow_cloud=true to use your configured cloud model — but "
                        "that sends the DECRYPTED photos to that provider."}

    index = _read_index(root, pw)
    albums: dict[str, list[str]] = {}
    moved, failed = [], []
    for f in files:
        try:
            blob = f.read_bytes()
            image, _cek, _slots = data_protect._open(blob, pw)
        except Exception as e:
            failed.append({"path": str(f), "error": f"cannot decrypt: {e}"})
            continue
        # The original name is `something.jpg.ember`; recover the real suffix for the classifier.
        suffix = Path(f.stem).suffix or ".jpg"
        r = classify_local(image) if not allow_cloud else classify_cloud(image, suffix)
        del image  # drop the plaintext as soon as the classifier is done with it
        if not r.get("ok"):
            failed.append({"path": str(f), "error": r.get("error", "classification failed")})
            continue
        cat = r["category"]
        albums.setdefault(cat, [])
        idx = sorted(CATEGORIES).index(cat) + 1 if cat in CATEGORIES else 99
        dest_dir = root / (f"group-{idx:02d}" if private_names else cat)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f.name
        if dest.exists():
            failed.append({"path": str(f), "error": "a file of that name is already in the album"})
            continue
        try:
            f.rename(dest)
        except OSError as e:
            failed.append({"path": str(f), "error": str(e)})
            continue
        albums[cat].append(dest.name)
        moved.append(str(dest))
        if private_names:
            index.setdefault(f"group-{idx:02d}", cat)

    if private_names and moved:
        _write_index(root, pw, index)

    out = {"ok": True, "sorted_count": len(moved), "albums": {k: len(v) for k, v in albums.items()},
           "failed": failed, "private_names": bool(private_names),
           "classifier": "cloud" if allow_cloud else "local"}
    if private_names:
        out["note"] = ("Albums are named group-NN so iCloud cannot see what they contain. The "
                       f"real names are in {INDEX_NAME}, encrypted with the same key as your "
                       "photos. adp_albums lists them.")
    else:
        out["warning"] = ("Folder names are NOT encrypted. iCloud can now see album names like "
                          "'documents' and 'receipts', which is a searchable index of what you "
                          "encrypted these photos to hide. Re-run with private_names=true to "
                          "undo that.")
    if allow_cloud:
        out["warning_cloud"] = ("Each photo was decrypted and sent to your configured cloud "
                                "model to be classified.")
    return out


def albums(folder: str) -> dict:
    """Read back the album names for a folder organised with private names."""
    pw = data_protect._passphrase()
    if pw is None:
        return {"ok": False, "error": "data protection is not set up"}
    root = Path(str(folder)).expanduser()
    if not root.is_dir():
        return {"ok": False, "error": f"no such folder: {root}"}
    index = _read_index(root, pw)
    out = []
    for group, name in sorted(index.items()):
        d = root / group
        out.append({"folder": group, "album": name,
                    "count": len(list(d.glob(f"*{data_protect.EXT}"))) if d.is_dir() else 0})
    return {"ok": True, "albums": out} if out else {
        "ok": True, "albums": [], "note": "no album index here — nothing organised yet, or it "
                                          "was organised with readable folder names"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def adp_organise(folder: str, allow_cloud: bool = False, private_names: bool = True,
                 recursive: bool = False, limit: int = 0) -> dict:
    """Sort protected photos into albums by what is in them, without decrypting them to disk."""
    return organise(folder, allow_cloud, private_names, recursive, limit)


def adp_albums(folder: str) -> dict:
    """List the album names for a folder that was organised with private folder names."""
    return albums(folder)


TOOL_DECLARATIONS = [
    {
        "name": "adp_organise",
        "description": "Sort encrypted photos into albums by looking at what is in them. Photos "
                       "are decrypted in memory only and stay encrypted on disk. Uses a local "
                       "Ollama vision model by default; allow_cloud sends the decrypted photos "
                       "to the configured cloud model instead.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder of .ember files"},
            "allow_cloud": {"type": "BOOLEAN", "description": "send decrypted photos to the cloud model instead of a local one (default false)"},
            "private_names": {"type": "BOOLEAN", "description": "use opaque folder names so iCloud cannot see album topics (default true)"},
            "recursive": {"type": "BOOLEAN", "description": "include subfolders (default false)"},
            "limit": {"type": "INTEGER", "description": "stop after this many photos (0 = all)"}},
            "required": ["folder"]},
    },
    {
        "name": "adp_albums",
        "description": "List the real album names for a folder organised with private folder "
                       "names, by reading the encrypted album index.",
        "parameters": {"type": "OBJECT", "properties": {
            "folder": {"type": "STRING", "description": "folder of .ember files"}},
            "required": ["folder"]},
    },
]

TOOL_DISPATCH = {"adp_organise": adp_organise, "adp_albums": adp_albums}
READONLY_TOOLS = {"adp_albums"}
INTERACTION_TOOLS = {"adp_organise"}
