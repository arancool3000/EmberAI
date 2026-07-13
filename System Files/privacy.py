"""Privacy & security tools: OS-keychain secrets, file encryption, and a
privacy-preserving breached-password check."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

_SERVICE = "EmberVault"


def keychain_store(name: str, secret: str) -> dict:
    """Store a secret in the OS keychain (macOS Keychain / Windows Credential Manager /
    libsecret on Linux). Use this instead of leaving keys in plaintext files."""
    if not name or not secret:
        return {"ok": False, "error": "name and secret are required"}
    try:
        if sys.platform == "darwin":
            subprocess.run(["security", "add-generic-password", "-U", "-a", name,
                            "-s", f"{_SERVICE}:{name}", "-w", secret],
                           capture_output=True, text=True, check=True, timeout=15)
            return {"ok": True, "stored": name}
        if sys.platform.startswith("win"):
            r = subprocess.run(["cmdkey", f"/generic:{_SERVICE}:{name}",
                                f"/user:{name}", f"/pass:{secret}"],
                               capture_output=True, text=True, timeout=15)
            return ({"ok": True, "stored": name} if r.returncode == 0
                    else {"ok": False, "error": (r.stdout or "cmdkey failed").strip()})
        if not shutil.which("secret-tool"):
            return {"ok": False, "error": "secret-tool not available (install libsecret-tools)"}
        subprocess.run(["secret-tool", "store", "--label", f"{_SERVICE}:{name}",
                        "service", _SERVICE, "account", name],
                       input=secret, capture_output=True, text=True, check=True, timeout=15)
        return {"ok": True, "stored": name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def keychain_get(name: str) -> dict:
    """Retrieve a secret previously stored with keychain_store."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["security", "find-generic-password", "-a", name,
                                "-s", f"{_SERVICE}:{name}", "-w"],
                               capture_output=True, text=True, timeout=15)
            return ({"ok": True, "name": name, "secret": r.stdout.strip()}
                    if r.returncode == 0 else {"ok": False, "error": "not found"})
        if sys.platform.startswith("win"):
            return {"ok": False, "error": "retrieval isn't supported via cmdkey; "
                    "use Windows Credential Manager to view it"}
        if not shutil.which("secret-tool"):
            return {"ok": False, "error": "secret-tool not available"}
        r = subprocess.run(["secret-tool", "lookup", "service", _SERVICE, "account", name],
                           capture_output=True, text=True, timeout=15)
        return ({"ok": True, "name": name, "secret": r.stdout.strip()}
                if r.returncode == 0 and r.stdout else {"ok": False, "error": "not found"})
    except Exception as e:
        return {"ok": False, "error": str(e)}


def encrypt_file(path: str, passphrase: str, output: str = "") -> dict:
    """Encrypt a file with AES-256 (via openssl). Output defaults to <path>.enc."""
    if not shutil.which("openssl"):
        return {"ok": False, "error": "openssl not available"}
    if not passphrase:
        return {"ok": False, "error": "passphrase required"}
    s = Path(path).expanduser()
    if not s.exists():
        return {"ok": False, "error": f"not found: {path}"}
    out = Path(output).expanduser() if output else s.with_suffix(s.suffix + ".enc")
    try:
        r = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
                            "-in", str(s), "-out", str(out), "-pass", f"pass:{passphrase}"],
                           capture_output=True, text=True, timeout=120)
        return ({"ok": True, "output": str(out)} if r.returncode == 0
                else {"ok": False, "error": (r.stderr or "openssl failed")[-300:]})
    except Exception as e:
        return {"ok": False, "error": str(e)}


def decrypt_file(path: str, passphrase: str, output: str = "") -> dict:
    """Decrypt a file produced by encrypt_file."""
    if not shutil.which("openssl"):
        return {"ok": False, "error": "openssl not available"}
    s = Path(path).expanduser()
    if not s.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if output:
        out = Path(output).expanduser()
    elif s.suffix == ".enc":
        out = s.with_suffix("")
    else:
        out = s.with_suffix(s.suffix + ".dec")
    try:
        r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                            "-in", str(s), "-out", str(out), "-pass", f"pass:{passphrase}"],
                           capture_output=True, text=True, timeout=120)
        return ({"ok": True, "output": str(out)} if r.returncode == 0
                else {"ok": False, "error": (r.stderr or "wrong passphrase or not an "
                                             "Ember-encrypted file")[-300:]})
    except Exception as e:
        return {"ok": False, "error": str(e)}


def password_pwned_check(password: str) -> dict:
    """Check if a password appears in known breaches via Have I Been Pwned's range API.
    Privacy-preserving: only the first 5 chars of the SHA-1 hash are sent (k-anonymity);
    the password itself never leaves your machine."""
    pw = password or ""
    if not pw:
        return {"ok": False, "error": "empty password"}
    try:
        import requests
        sha1 = hashlib.sha1(pw.encode("utf-8")).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        r = requests.get(f"https://api.pwnedpasswords.com/range/{prefix}",
                         headers={"User-Agent": "Ember"}, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"breach service returned {r.status_code}"}
        count = 0
        for line in r.text.splitlines():
            h, _, c = line.partition(":")
            if h.strip().upper() == suffix:
                count = int(c.strip() or 0)
                break
        return {"ok": True, "pwned": count > 0, "times_seen": count,
                "advice": ("This password is in known breaches — change it everywhere."
                           if count else "Not found in known breaches.")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
