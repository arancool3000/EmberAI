"""Hermetic tests for gmail_tools.py — the pure parsing/criteria helpers, plus the tool layer
driven by a fake IMAP connection (no network, no PyQt, no real Gmail). Run: python test_gmail_tools.py"""
import gmail_tools as gt


# ---- pure helpers ------------------------------------------------------------
def test_decode_mime_header():
    assert gt._decode_mime_header("=?UTF-8?B?SGVsbG8=?=") == "Hello"
    assert gt._decode_mime_header("Plain Subject") == "Plain Subject"
    assert gt._decode_mime_header("") == ""


def test_label_token():
    assert gt._label_token("Work") == '("Work")'
    assert gt._label_token("Money/Bills") == '("Money/Bills")'
    assert gt._label_token("inbox") == "(\\Inbox)"
    assert gt._label_token("\\Inbox") == "(\\Inbox)"
    assert gt._label_token("trash") == "(\\Trash)"


def test_mailbox_mapping():
    assert gt._mailbox("INBOX") == "INBOX"
    assert gt._mailbox("inbox") == "INBOX"
    assert gt._mailbox("All") == "[Gmail]/All Mail"
    assert gt._mailbox("Spam") == "[Gmail]/Spam"
    assert gt._mailbox("Receipts") == "Receipts"   # user label selects by its own name


def test_build_search():
    assert gt._build_search("from:boss is:unread", False) == ("X-GM-RAW", '"from:boss is:unread"')
    assert gt._build_search("", True) == ("UNSEEN",)
    assert gt._build_search("", False) == ("ALL",)


def test_parse_labels():
    assert gt._parse_labels('\\Inbox "Work" \\Important') == ["\\Inbox", "Work", "\\Important"]
    assert gt._parse_labels('"Money/Bills"') == ["Money/Bills"]


def test_parse_meta():
    meta = '1 (UID 101 FLAGS (\\Seen) X-GM-LABELS (\\Inbox "Work"))'
    info = gt._parse_meta(meta)
    assert info["uid"] == "101"
    assert "\\Seen" in info["flags"]
    assert "\\Inbox" in info["labels"] and "Work" in info["labels"]


def test_uids_from_search():
    assert gt._uids_from_search([b"1 2 3"]) == ["1", "2", "3"]
    assert gt._uids_from_search([b""]) == []
    assert gt._uids_from_search([None]) == []


def test_extract_body_plain():
    raw = (b"From: a@b.com\r\nSubject: x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
           b"Hello there\r\nsecond line\r\n")
    body = gt._extract_body(raw)
    assert "Hello there" in body and "second line" in body


# ---- fake-IMAP driven tool tests ---------------------------------------------
class _FakeIMAP:
    def __init__(self):
        self.calls = []
        self.selected = None
        self.logged_out = False

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        self.calls.append(("select", mailbox, readonly))
        return ("OK", [b"1"])

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "SEARCH":
            return ("OK", [b"101 102 103"])
        if command == "FETCH":
            return ("OK", [
                (b'1 (UID 103 FLAGS () X-GM-LABELS (\\Inbox "Work") '
                 b'BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {70}',
                 b"From: Alice <alice@example.com>\r\nSubject: =?UTF-8?B?SGVsbG8=?=\r\n"
                 b"Date: Mon, 1 Jan 2026 10:00:00 +0000\r\n\r\n"),
                b")",
            ])
        if command == "STORE":
            return ("OK", [b"103 (UID 103 ...)"])
        return ("OK", [b""])

    def list(self):
        return ("OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Work"'])

    def create(self, name):
        self.calls.append(("create", name))
        return ("OK", [b"created"])

    def logout(self):
        self.logged_out = True


def _with_fake(monkey_fn):
    fake = _FakeIMAP()
    orig = gt._connect
    gt._connect = lambda: fake
    try:
        return monkey_fn(fake)
    finally:
        gt._connect = orig


def test_gmail_search_parses_messages():
    def run(fake):
        r = gt.gmail_search("is:unread", max_results=10, label="INBOX")
        assert r["ok"] and r["count"] == 1, r
        m = r["messages"][0]
        assert m["uid"] == "103"
        assert "alice@example.com" in m["from"]
        assert m["subject"] == "Hello"          # decoded from MIME
        assert m["unread"] is True              # no \\Seen flag
        assert "Work" in m["labels"]
        # used Gmail raw search + selected INBOX read-only
        assert ("uid", "SEARCH", (None, "X-GM-RAW", '"is:unread"')) in fake.calls
        assert fake.logged_out is True
    _with_fake(run)


def test_gmail_apply_label_stores_label():
    def run(fake):
        r = gt.gmail_apply_label("103", "Work")
        assert r["ok"] and r["action"] == "applied", r
        assert ("uid", "STORE", ("103", "+X-GM-LABELS", '("Work")')) in fake.calls
    _with_fake(run)


def test_gmail_archive_removes_inbox_label():
    def run(fake):
        r = gt.gmail_archive("103")
        assert r["ok"] and r["action"] == "archived", r
        assert ("uid", "STORE", ("103", "-X-GM-LABELS", "(\\Inbox)")) in fake.calls
    _with_fake(run)


def test_gmail_mark_unread():
    def run(fake):
        r = gt.gmail_mark_read("103", read=False)
        assert r["ok"] and r["action"] == "marked_unread", r
        assert ("uid", "STORE", ("103", "-FLAGS", "(\\Seen)")) in fake.calls
    _with_fake(run)


def test_not_configured_is_graceful():
    orig = gt._creds
    gt._creds = lambda: ("imap.gmail.com", "", "")
    try:
        assert gt.is_configured() is False
        s = gt.gmail_status()
        assert s["ok"] is True and s["configured"] is False
    finally:
        gt._creds = orig


def test_creds_strips_spaces_from_pasted_app_password():
    # Google shows App Passwords as "abcd efgh ijkl mnop"; users paste them verbatim but login
    # needs the 16 chars with no spaces. _creds must strip them ("I did everything right" bug).
    import sys
    import types as _t
    fake_ui = _t.SimpleNamespace(load_settings=lambda: {
        "gmail_address": "me@gmail.com", "gmail_app_password": "abcd efgh ijkl mnop"})
    prev = sys.modules.get("ui")
    sys.modules["ui"] = fake_ui
    try:
        host, user, pw = gt._creds()
    finally:
        if prev is None:
            sys.modules.pop("ui", None)
        else:
            sys.modules["ui"] = prev
    assert user == "me@gmail.com"
    assert pw == "abcdefghijklmnop", pw   # spaces stripped


def _fake_imap(behavior):
    import imaplib

    class F:
        def __init__(self, host, timeout=None):
            pass

        def login(self, u, p):
            if behavior == "auth":
                raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
            if behavior == "imap_off":
                raise imaplib.IMAP4.error("Please log in via your web browser")
            return ("OK", [])

        def select(self, mbox, readonly=False):
            return ("OK", [b"1"]) if behavior != "select_fail" else ("NO", [b""])

        def logout(self):
            return ("BYE", [])
    return F


def _run_diag(creds, behavior):
    import imaplib
    orig_creds, orig_ssl = gt._creds, imaplib.IMAP4_SSL
    gt._creds = lambda: creds
    imaplib.IMAP4_SSL = _fake_imap(behavior)
    try:
        return gt.gmail_diagnose()
    finally:
        gt._creds = orig_creds
        imaplib.IMAP4_SSL = orig_ssl


def test_gmail_diagnose_not_configured():
    orig = gt._creds
    gt._creds = lambda: ("imap.gmail.com", "", "")
    try:
        r = gt.gmail_diagnose()
        assert r["ok"] is False and r["configured"] is False and "address" in r["problem"].lower()
    finally:
        gt._creds = orig


def test_gmail_diagnose_missing_password():
    orig = gt._creds
    gt._creds = lambda: ("imap.gmail.com", "me@gmail.com", "")
    try:
        r = gt.gmail_diagnose()
        assert r["ok"] is False and "app password" in r["problem"].lower()
    finally:
        gt._creds = orig


def test_gmail_diagnose_auth_failure_flags_a_non_app_password():
    r = _run_diag(("imap.gmail.com", "me@gmail.com", "mypassword"), "auth")  # 10 chars, not an app pw
    assert r["ok"] is False and "rejected" in r["problem"].lower()
    assert r["notes"] and "App Password" in r["notes"][0]


def test_gmail_diagnose_detects_imap_disabled():
    r = _run_diag(("imap.gmail.com", "me@gmail.com", "abcdefghijklmnop"), "imap_off")
    assert r["ok"] is False and "imap" in r["problem"].lower()
    assert "Workspace" in r["fix"] and "automatically" in r["fix"]


def test_gmail_diagnose_success_when_login_and_inbox_work():
    r = _run_diag(("imap.gmail.com", "me@gmail.com", "abcdefghijklmnop"), "ok")
    assert r["ok"] is True and r["configured"] is True and r["address"] == "me@gmail.com"


def test_gmail_diagnose_select_failure_points_at_imap():
    r = _run_diag(("imap.gmail.com", "me@gmail.com", "abcdefghijklmnop"), "select_fail")
    assert r["ok"] is False and "IMAP" in r["fix"]


def test_gmail_diagnose_can_test_unsaved_fields_directly():
    import imaplib
    original = imaplib.IMAP4_SSL
    imaplib.IMAP4_SSL = _fake_imap("ok")
    try:
        r = gt.gmail_diagnose(address="new@gmail.com",
                              app_password="abcd efgh ijkl mnop")
    finally:
        imaplib.IMAP4_SSL = original
    assert r["ok"] is True and r["address"] == "new@gmail.com"


def test_exports_consistent():
    assert set(gt.TOOL_DISPATCH) == {d["name"] for d in gt.TOOL_DECLARATIONS}
    assert gt.READONLY_TOOLS <= set(gt.TOOL_DISPATCH)
    assert gt.INTERACTION_TOOLS <= set(gt.TOOL_DISPATCH)
    assert gt.READONLY_TOOLS.isdisjoint(gt.INTERACTION_TOOLS)
    assert gt.READONLY_TOOLS | gt.INTERACTION_TOOLS == set(gt.TOOL_DISPATCH)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} gmail_tools tests passed")
