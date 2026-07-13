"""Ember Browser password manager — per-site login storage backed by the encrypted vault.

Credentials are stored through ``key_vault`` (OS keychain when available, else a Fernet
encrypted file), so passwords are never written in plaintext. These helpers are deliberately
NOT exposed as agent tools — a stored password must never be handed to the LLM. They are used
only by the browser UI for save / autofill / manage.
"""
from __future__ import annotations

import json

_PREFIX = "webpw:"   # vault key namespace for browser logins


def _domain(host_or_url: str) -> str:
    """Normalise to a bare registrable host (drop scheme, path, port, leading www.)."""
    s = (host_or_url or "").strip().lower()
    if "://" in s:
        try:
            from urllib.parse import urlparse
            s = urlparse(s).netloc or s
        except Exception:
            s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0]
    s = s.split("@")[-1]          # strip any userinfo
    s = s.split(":", 1)[0]         # strip port
    if s.startswith("www."):
        s = s[4:]
    return s


def save_login(host_or_url: str, username: str, password: str) -> bool:
    dom = _domain(host_or_url)
    if not dom:
        return False
    try:
        import key_vault
        blob = json.dumps({"u": username or "", "p": password or ""})
        return bool(key_vault.set_key(_PREFIX + dom, blob))
    except Exception:
        return False


def get_login(host_or_url: str) -> dict | None:
    dom = _domain(host_or_url)
    if not dom:
        return None
    try:
        import key_vault
        raw = key_vault.get_key(_PREFIX + dom)
        if not raw:
            return None
        d = json.loads(raw)
        return {"domain": dom, "username": d.get("u", ""), "password": d.get("p", "")}
    except Exception:
        return None


def list_logins() -> list[str]:
    """Domains that have a saved login (no passwords returned)."""
    try:
        import key_vault
        return sorted(k[len(_PREFIX):] for k in key_vault.list_keys() if k.startswith(_PREFIX))
    except Exception:
        return []


def delete_login(host_or_url: str) -> bool:
    dom = _domain(host_or_url)
    if not dom:
        return False
    try:
        import key_vault
        return bool(key_vault.delete_key(_PREFIX + dom))
    except Exception:
        return False


def autofill_js(login: dict) -> str:
    """Return JS that fills the first visible username + password field on the page.
    Values are JSON-encoded so quotes/backslashes can't break out of the string."""
    u = json.dumps(login.get("username", ""))
    p = json.dumps(login.get("password", ""))
    return """
(function(){
  try{
    var u=%s, p=%s;
    var pw=document.querySelector('input[type=password]');
    if(!pw) return 'no-password-field';
    var form=pw.form||document;
    var user=form.querySelector('input[type=email],input[type=text],input[name*=user i],input[name*=email i],input[id*=user i],input[id*=email i]');
    function set(el,val){ if(!el) return; el.focus();
      var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');
      d&&d.set?d.set.call(el,val):el.value=val;
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true})); }
    set(user,u); set(pw,p);
    return 'filled';
  }catch(e){ return 'error:'+e; }
})();
""" % (u, p)
