"""Gmail organisation for Ember — search, label, archive, star, trash, mark read/unread.

Ember organises your inbox over IMAP using the SAME credentials as send_email:
  • email_smtp_user      -> your Gmail address
  • email_smtp_password  -> a Google App Password (NOT your normal password)
So if you've set up sending email, organising already works. No OAuth / Cloud project needed:
turn on 2-Step Verification, create an App Password at myaccount.google.com/apppasswords, and
paste it into Settings.

Gmail's IMAP extensions do the heavy lifting: X-GM-RAW lets Ember use Gmail's own search syntax
("from:boss is:unread older_than:7d"), and X-GM-LABELS adds/removes real Gmail labels (including
\\Inbox, so "archive" = remove the Inbox label, and \\Starred for stars).

Design: the network goes through _connect() (a single injection point tests monkeypatch with a
fake IMAP); the parsing/criteria helpers are pure and unit-tested. Every tool returns
{"ok": True, ...} or {"ok": False, "error": "..."} and never raises.
"""
from __future__ import annotations

import email
import re
from email.header import decode_header

DEFAULT_IMAP_HOST = "imap.gmail.com"

# Gmail system labels (used with X-GM-LABELS); everything else is a user label.
_SYSTEM_LABELS = {
    "inbox": "\\Inbox", "starred": "\\Starred", "important": "\\Important",
    "sent": "\\Sent", "draft": "\\Draft", "drafts": "\\Draft", "spam": "\\Spam",
    "trash": "\\Trash", "all": "\\All",
}


# ---------------------------------------------------------------------------
# Credentials + connection
# ---------------------------------------------------------------------------
def _creds() -> tuple[str, str, str]:
    """(host, user, app_password) from settings — dedicated gmail_* keys, else the SMTP ones."""
    try:
        from ui import load_settings
        st = load_settings()
    except Exception:
        st = {}
    host = (st.get("gmail_imap_host") or DEFAULT_IMAP_HOST).strip()
    user = (st.get("gmail_address") or st.get("email_smtp_user") or "").strip()
    # Google shows App Passwords as "abcd efgh ijkl mnop" (four space-separated groups). Users
    # paste them exactly as shown, but the actual secret is the 16 characters with NO spaces —
    # IMAP/SMTP login fails on the spaced form. Strip ALL whitespace so a correctly-copied app
    # password authenticates ("I did everything right" but it still failed = the spaces).
    pw = "".join((st.get("gmail_app_password") or st.get("email_smtp_password") or "").split())
    return host, user, pw


def is_configured() -> bool:
    _, user, pw = _creds()
    return bool(user and pw)


def _connect():
    """Open an authenticated IMAP4_SSL connection to Gmail. Raises with a clear message if the
    account isn't configured. Tests monkeypatch this to return a fake connection."""
    import imaplib
    host, user, pw = _creds()
    if not (user and pw):
        raise RuntimeError("Gmail isn't set up. Add your Gmail address + a Google App Password "
                           "in Settings (the same fields used to send email).")
    conn = imaplib.IMAP4_SSL(host, timeout=30)
    try:
        conn.login(user, pw)
    except imaplib.IMAP4.error as e:
        raise RuntimeError(
            "Gmail rejected the login. Use a Google App Password "
            "(myaccount.google.com/apppasswords) — not your normal password — with 2-Step "
            "Verification on. Create a new App Password if your Google password changed. "
            "Personal Gmail has IMAP enabled automatically; managed Workspace accounts may "
            f"need their administrator to allow it. ({e})")
    return conn


def gmail_diagnose(address: str = "", app_password: str = "", host: str = "") -> dict:
    """Pinpoint WHY Gmail isn't working and how to fix it. Turns 'it's not working' into a specific
    cause: missing setup, a normal password instead of an App Password, revoked credentials, or
    a managed-account policy. Optional arguments let Settings test unsaved fields directly."""
    import imaplib
    if address or app_password or host:
        saved_host, saved_user, saved_pw = _creds()
        host = (host or saved_host or DEFAULT_IMAP_HOST).strip()
        user = (address or saved_user).strip()
        pw = "".join((app_password or saved_pw).split())
    else:
        host, user, pw = _creds()
    if not user:
        return {"ok": False, "configured": False, "problem": "No Gmail address is set.",
                "fix": "Settings ▸ Gmail: enter your full Gmail address and a Google App Password."}
    if not pw:
        return {"ok": False, "configured": False, "problem": "No Google App Password is set.",
                "fix": "Create one at myaccount.google.com/apppasswords (needs 2-Step Verification), "
                       "then paste it in Settings ▸ Gmail."}
    notes = []
    # A Google App Password is exactly 16 letters (shown as 4 groups of 4). If the saved value
    # isn't, it's almost certainly a normal password — which Gmail IMAP always rejects.
    if not (len(pw) == 16 and pw.isalpha()):
        notes.append("The saved password isn't 16 letters, so it looks like your normal Google "
                     "password rather than an App Password. Gmail only accepts an App Password.")
    try:
        conn = imaplib.IMAP4_SSL(host, timeout=20)
    except Exception as e:
        return {"ok": False, "configured": True, "problem": f"Couldn't reach {host}: {e}",
                "fix": "Check your internet connection / firewall, then retry.", "notes": notes}
    try:
        conn.login(user, pw)
    except imaplib.IMAP4.error as e:
        low = str(e).lower()
        if any(t in low for t in ("web browser", "imap access", "not enabled", "enable imap")):
            problem = "This Google account or administrator is blocking IMAP access."
            fix = ("Personal Gmail enables IMAP automatically. If this is a work/school Google "
                   "Workspace account, ask the administrator to allow IMAP and App Passwords.")
        else:
            problem = "Gmail rejected the username / App Password."
            fix = ("1) Turn ON 2-Step Verification. 2) Create a NEW App Password at "
                   "myaccount.google.com/apppasswords and paste it (spaces are fine — Ember strips "
                   "them). 3) Make sure the address is the SAME account the App Password belongs "
                   "to. Google revokes App Passwords after the main account password changes. "
                   "Advanced Protection and some work/school policies do not allow App Passwords.")
        try:
            conn.logout()
        except Exception:
            pass
        return {"ok": False, "configured": True, "address": user,
                "problem": problem, "fix": fix, "detail": str(e), "notes": notes}
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        return {"ok": False, "configured": True, "problem": f"Unexpected login error: {e}",
                "fix": "Recreate the App Password and confirm 2-Step Verification is on.",
                "notes": notes}
    # Login worked — confirm IMAP is actually usable by opening the inbox.
    try:
        typ, _ = conn.select("INBOX", readonly=True)
        try:
            conn.logout()
        except Exception:
            pass
        if typ != "OK":
            return {"ok": False, "configured": True, "address": user,
                    "problem": "Signed in, but Gmail would not open the inbox.",
                    "fix": "Retry once; for a managed Workspace account, ask the administrator "
                           "to allow IMAP access.", "notes": notes}
    except Exception as e:
        return {"ok": False, "configured": True, "address": user,
                "problem": f"Signed in, but opening the inbox failed: {e}",
                "fix": "Retry once; managed Workspace accounts may require administrator IMAP "
                       "permission.", "notes": notes}
    return {"ok": True, "configured": True, "address": user,
            "message": "Gmail is working — signed in and opened the inbox successfully."
                       + (" (Note: " + " ".join(notes) + ")" if notes else "")}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without a connection)
# ---------------------------------------------------------------------------
def _decode_mime_header(raw: str) -> str:
    """Decode an RFC 2047 header (e.g. '=?UTF-8?B?...?=') to a plain string."""
    if not raw:
        return ""
    out = []
    try:
        for chunk, enc in decode_header(raw):
            if isinstance(chunk, bytes):
                out.append(chunk.decode(enc or "utf-8", "replace"))
            else:
                out.append(chunk)
    except Exception:
        return str(raw)
    return "".join(out).strip()


def _label_token(label: str) -> str:
    """Build the X-GM-LABELS argument for one label: a parenthesised, quoted token. System labels
    (inbox/starred/...) become their backslash form; user labels are quoted verbatim."""
    name = (label or "").strip()
    sysname = _SYSTEM_LABELS.get(name.lower())
    if name.startswith("\\"):
        token = name
    elif sysname:
        token = sysname
    else:
        token = '"' + name.replace('"', '\\"') + '"'
    return "(" + token + ")"


def _mailbox(label: str) -> str:
    """Map a friendly name to the IMAP mailbox to SELECT. Labels select by their own name;
    a few specials map to Gmail's [Gmail]/ folders."""
    name = (label or "INBOX").strip()
    low = name.lower()
    specials = {"all": "[Gmail]/All Mail", "all mail": "[Gmail]/All Mail",
                "sent": "[Gmail]/Sent Mail", "drafts": "[Gmail]/Drafts",
                "spam": "[Gmail]/Spam", "trash": "[Gmail]/Trash",
                "starred": "[Gmail]/Starred", "important": "[Gmail]/Important"}
    if low == "inbox":
        return "INBOX"
    return specials.get(low, name)


def _build_search(query: str, unread_only: bool) -> tuple:
    """Return the args for conn.uid('SEARCH', *args). Gmail raw syntax when a query is given."""
    if query and query.strip():
        return ("X-GM-RAW", '"' + query.strip().replace('"', '\\"') + '"')
    if unread_only:
        return ("UNSEEN",)
    return ("ALL",)


def _parse_labels(blob: str) -> list:
    """Parse the X-GM-LABELS (...) payload into a list of label names."""
    out, i, n = [], 0, len(blob or "")
    while i < n:
        c = blob[i]
        if c == '"':
            j = i + 1
            buf = []
            while j < n and blob[j] != '"':
                if blob[j] == "\\" and j + 1 < n:
                    buf.append(blob[j + 1]); j += 2; continue
                buf.append(blob[j]); j += 1
            out.append("".join(buf)); i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not blob[j].isspace():
                j += 1
            out.append(blob[i:j]); i = j
    return out


def _parse_meta(meta: str) -> dict:
    """Pull UID, FLAGS and X-GM-LABELS out of a FETCH response's metadata string."""
    uid = ""
    m = re.search(r"UID (\d+)", meta)
    if m:
        uid = m.group(1)
    flags = []
    m = re.search(r"FLAGS \(([^)]*)\)", meta)
    if m:
        flags = m.group(1).split()
    labels = []
    m = re.search(r"X-GM-LABELS \((.*?)\)(?: |$|BODY|FLAGS|UID)", meta)
    if not m:
        m = re.search(r"X-GM-LABELS \((.*)\)", meta)
    if m:
        labels = _parse_labels(m.group(1))
    return {"uid": uid, "flags": flags, "labels": labels}


def _parse_fetch(fetched: list) -> list:
    """Turn imaplib's FETCH result (list of tuples / bytes) into message dicts."""
    out = []
    for item in fetched or []:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        meta = item[0].decode("utf-8", "replace") if isinstance(item[0], (bytes, bytearray)) else str(item[0])
        hdr = item[1] if isinstance(item[1], (bytes, bytearray)) else str(item[1]).encode()
        info = _parse_meta(meta)
        try:
            msg = email.message_from_bytes(hdr)
        except Exception:
            msg = None
        frm = _decode_mime_header(msg.get("From", "")) if msg else ""
        subj = _decode_mime_header(msg.get("Subject", "")) if msg else ""
        date = (msg.get("Date", "") if msg else "")
        out.append({
            "uid": info["uid"],
            "from": frm,
            "subject": subj,
            "date": date,
            "unread": "\\Seen" not in info["flags"],
            "starred": ("\\Flagged" in info["flags"]) or ("\\Starred" in info["labels"]),
            "labels": [l for l in info["labels"] if l not in ("\\Seen",)],
        })
    return out


def _extract_body(raw_bytes: bytes, max_chars: int = 8000) -> str:
    """Plain-text body of an RFC822 message (prefers text/plain; strips tags from HTML)."""
    try:
        msg = email.message_from_bytes(raw_bytes)
    except Exception:
        return ""
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                    break
                except Exception:
                    continue
        if not text:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                        text = re.sub(r"<[^>]+>", " ", html)
                        break
                    except Exception:
                        continue
    else:
        try:
            text = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")
        except Exception:
            text = str(msg.get_payload())
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def _uids_from_search(data) -> list:
    if not data or not data[0]:
        return []
    raw = data[0]
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", "replace")
    return raw.split()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def gmail_status() -> dict:
    """Report whether Gmail is set up and, if so, the inbox total + unread counts."""
    if not is_configured():
        return {"ok": True, "configured": False,
                "message": "Gmail not set up — add your Gmail address + a Google App Password in Settings."}
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "configured": True, "error": str(e)}
    try:
        conn.select("INBOX", readonly=True)
        total = _uids_from_search(conn.uid("SEARCH", None, "ALL")[1])
        unread = _uids_from_search(conn.uid("SEARCH", None, "UNSEEN")[1])
        _, _, user = (*_creds()[:2], _creds()[1])
        return {"ok": True, "configured": True, "account": _creds()[1],
                "inbox_total": len(total), "inbox_unread": len(unread)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def gmail_list_labels() -> dict:
    """List the Gmail labels/folders available to organise into."""
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        typ, data = conn.list()
        labels = []
        for row in (data or []):
            s = row.decode("utf-8", "replace") if isinstance(row, (bytes, bytearray)) else str(row)
            m = re.search(r'"(?:[^"]*)" "?([^"]+)"?$', s) or re.search(r'\s([^\s"]+)$', s)
            name = (m.group(1) if m else s).strip().strip('"')
            if name:
                labels.append(name)
        return {"ok": True, "count": len(labels), "labels": labels}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def gmail_search(query: str = "", max_results: int = 25, label: str = "INBOX",
                 unread_only: bool = False) -> dict:
    """Search a Gmail label/folder. `query` uses Gmail's own syntax (e.g. 'from:amazon
    is:unread older_than:30d'); empty query lists the mailbox. Returns the newest matches first
    with uid, from, subject, date, unread, starred, labels — use the uid with the other tools."""
    try:
        max_results = max(1, min(100, int(max_results)))
    except Exception:
        max_results = 25
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        conn.select(_mailbox(label), readonly=True)
        typ, data = conn.uid("SEARCH", None, *_build_search(query, unread_only))
        if typ != "OK":
            return {"ok": False, "error": f"search failed: {data}"}
        uids = _uids_from_search(data)[-max_results:][::-1]   # newest first
        messages = []
        if uids:
            uid_csv = ",".join(u.decode() if isinstance(u, (bytes, bytearray)) else str(u) for u in uids)
            typ, fetched = conn.uid("FETCH", uid_csv,
                "(FLAGS X-GM-LABELS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            messages = _parse_fetch(fetched)
        return {"ok": True, "count": len(messages), "label": label, "messages": messages}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def gmail_read(uid: str, label: str = "INBOX") -> dict:
    """Read the full plain-text body of one message by uid (from gmail_search)."""
    if not str(uid).strip():
        return {"ok": False, "error": "a message uid is required"}
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        conn.select(_mailbox(label), readonly=True)
        typ, fetched = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not fetched:
            return {"ok": False, "error": "message not found"}
        raw = b""
        for item in fetched:
            if isinstance(item, (tuple, list)) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                raw = item[1]; break
        msg = email.message_from_bytes(raw) if raw else None
        return {"ok": True, "uid": str(uid),
                "from": _decode_mime_header(msg.get("From", "")) if msg else "",
                "to": _decode_mime_header(msg.get("To", "")) if msg else "",
                "subject": _decode_mime_header(msg.get("Subject", "")) if msg else "",
                "date": (msg.get("Date", "") if msg else ""),
                "body": _extract_body(raw)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def _store(uid: str, command: str, arg: str, source: str = "INBOX", readonly: bool = False) -> dict:
    """Run a UID STORE against the source mailbox. Returns {ok} or {ok:False,error}."""
    if not str(uid).strip():
        return {"ok": False, "error": "a message uid is required"}
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        conn.select(_mailbox(source))
        typ, data = conn.uid("STORE", str(uid), command, arg)
        if typ != "OK":
            return {"ok": False, "error": f"{command} failed: {data}"}
        return {"ok": True, "uid": str(uid)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def gmail_apply_label(uid: str, label: str, source: str = "INBOX") -> dict:
    """Add a Gmail label to a message (creating it implicitly). Use to file/categorise email."""
    r = _store(uid, "+X-GM-LABELS", _label_token(label), source)
    if r.get("ok"):
        r.update({"label": label, "action": "applied"})
    return r


def gmail_remove_label(uid: str, label: str, source: str = "INBOX") -> dict:
    """Remove a Gmail label from a message."""
    r = _store(uid, "-X-GM-LABELS", _label_token(label), source)
    if r.get("ok"):
        r.update({"label": label, "action": "removed"})
    return r


def gmail_archive(uid: str, source: str = "INBOX") -> dict:
    """Archive a message — remove it from the Inbox (it stays in All Mail). Reversible."""
    r = _store(uid, "-X-GM-LABELS", _label_token("\\Inbox"), source)
    if r.get("ok"):
        r.update({"action": "archived"})
    return r


def gmail_mark_read(uid: str, read: bool = True, source: str = "INBOX") -> dict:
    """Mark a message read (read=True) or unread (read=False)."""
    r = _store(uid, "+FLAGS" if read else "-FLAGS", "(\\Seen)", source)
    if r.get("ok"):
        r.update({"action": "marked_read" if read else "marked_unread"})
    return r


def gmail_star(uid: str, star: bool = True, source: str = "INBOX") -> dict:
    """Star (star=True) or unstar (star=False) a message."""
    r = _store(uid, "+FLAGS" if star else "-FLAGS", "(\\Flagged)", source)
    if r.get("ok"):
        r.update({"action": "starred" if star else "unstarred"})
    return r


def gmail_trash(uid: str, source: str = "INBOX") -> dict:
    """Move a message to Trash (recoverable for 30 days). Use instead of permanent deletion."""
    r = _store(uid, "+X-GM-LABELS", _label_token("\\Trash"), source)
    if r.get("ok"):
        r.update({"action": "trashed"})
    return r


def gmail_create_label(name: str) -> dict:
    """Create a new Gmail label (folder) to organise mail into."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "a label name is required"}
    try:
        conn = _connect()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        typ, data = conn.create(name)
        # Gmail returns NO with "ALREADYEXISTS" if it's already there — treat as success.
        ok = typ == "OK" or "ALREADYEXISTS" in str(data).upper()
        return {"ok": ok, "label": name} if ok else {"ok": False, "error": str(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _safe_logout(conn)


def _safe_logout(conn) -> None:
    try:
        conn.logout()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Wiring exports
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {"name": "gmail_status",
     "description": "Check if Gmail is connected and report inbox total + unread counts.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "gmail_diagnose",
     "description": "Diagnose WHY Gmail isn't working and how to fix it — it actually tries to "
                    "connect and reports the exact cause (not set up, a normal password instead of "
                    "an App Password, wrong credentials, 2-Step Verification off, or a Workspace "
                    "administrator policy) "
                    "with the specific fix. Use this whenever Gmail fails.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "gmail_list_labels",
     "description": "List the Gmail labels/folders available to organise email into.",
     "parameters": {"type": "OBJECT", "properties": {}, "required": []}},
    {"name": "gmail_search",
     "description": "Search Gmail and list messages (newest first). `query` uses Gmail's own "
                    "search syntax, e.g. 'from:amazon is:unread', 'subject:invoice older_than:30d', "
                    "'category:promotions'. Empty query lists the label. Returns each message's "
                    "uid (use it with the other gmail_ tools), from, subject, date, unread, labels.",
     "parameters": {"type": "OBJECT", "properties": {
        "query": {"type": "STRING", "description": "Gmail search query (optional)"},
        "max_results": {"type": "INTEGER", "description": "1-100, default 25"},
        "label": {"type": "STRING", "description": "label/folder to search (default INBOX; e.g. "
                  "'All', 'Spam', or a label name)"},
        "unread_only": {"type": "BOOLEAN"}}, "required": []}},
    {"name": "gmail_read",
     "description": "Read the full plain-text body of one Gmail message by uid (from gmail_search).",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "label": {"type": "STRING"}}, "required": ["uid"]}},
    {"name": "gmail_apply_label",
     "description": "Add a Gmail label to a message (filing/categorising). Creates the label if "
                    "it doesn't exist.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "label": {"type": "STRING"},
        "source": {"type": "STRING", "description": "mailbox the uid is in (default INBOX)"}},
        "required": ["uid", "label"]}},
    {"name": "gmail_remove_label",
     "description": "Remove a Gmail label from a message.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "label": {"type": "STRING"}, "source": {"type": "STRING"}},
        "required": ["uid", "label"]}},
    {"name": "gmail_archive",
     "description": "Archive a message: remove it from the Inbox (kept in All Mail). Reversible.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "source": {"type": "STRING"}}, "required": ["uid"]}},
    {"name": "gmail_mark_read",
     "description": "Mark a message read or unread.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "read": {"type": "BOOLEAN", "description": "default true"},
        "source": {"type": "STRING"}}, "required": ["uid"]}},
    {"name": "gmail_star",
     "description": "Star or unstar a message.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "star": {"type": "BOOLEAN", "description": "default true"},
        "source": {"type": "STRING"}}, "required": ["uid"]}},
    {"name": "gmail_trash",
     "description": "Move a message to Trash (recoverable for 30 days). Prefer this over permanent "
                    "deletion; confirm with the user before trashing.",
     "parameters": {"type": "OBJECT", "properties": {
        "uid": {"type": "STRING"}, "source": {"type": "STRING"}}, "required": ["uid"]}},
    {"name": "gmail_create_label",
     "description": "Create a new Gmail label to organise mail into.",
     "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING"}},
                    "required": ["name"]}},
]

TOOL_DISPATCH = {
    "gmail_status": gmail_status,
    "gmail_diagnose": gmail_diagnose,
    "gmail_list_labels": gmail_list_labels,
    "gmail_search": gmail_search,
    "gmail_read": gmail_read,
    "gmail_apply_label": gmail_apply_label,
    "gmail_remove_label": gmail_remove_label,
    "gmail_archive": gmail_archive,
    "gmail_mark_read": gmail_mark_read,
    "gmail_star": gmail_star,
    "gmail_trash": gmail_trash,
    "gmail_create_label": gmail_create_label,
}

# Reading the mailbox is non-mutating. Sends nothing over the wire except to Gmail's IMAP with
# the user's own credentials, so the read tools are safe/parallel-friendly.
READONLY_TOOLS = {"gmail_status", "gmail_diagnose", "gmail_list_labels", "gmail_search", "gmail_read"}
# Organising actions are reversible (labels/archive/star/read) or recoverable (trash) — low risk.
INTERACTION_TOOLS = {"gmail_apply_label", "gmail_remove_label", "gmail_archive",
                     "gmail_mark_read", "gmail_star", "gmail_trash", "gmail_create_label"}
