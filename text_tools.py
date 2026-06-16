"""30 small, pure utility tools for Ember — text, data, math, and conversions.
All are deterministic and dependency-free, so they're fast and safe (read-only)."""
from __future__ import annotations

import base64 as _b64
import codecs
import datetime as _dt
import json as _json
import random as _random
import re
import uuid as _uuid
from urllib.parse import quote, unquote


def base64_encode(text: str) -> dict:
    return {"ok": True, "result": _b64.b64encode((text or "").encode()).decode()}


def base64_decode(text: str) -> dict:
    try:
        return {"ok": True, "result": _b64.b64decode((text or "").encode()).decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def url_quote(text: str) -> dict:
    return {"ok": True, "result": quote(text or "", safe="")}


def url_unquote(text: str) -> dict:
    return {"ok": True, "result": unquote(text or "")}


def json_pretty(text: str) -> dict:
    """Validate + pretty-print JSON."""
    try:
        return {"ok": True, "result": _json.dumps(_json.loads(text or ""), indent=2, ensure_ascii=False)}
    except Exception as e:
        return {"ok": False, "error": f"invalid JSON: {e}"}


def case_convert(text: str, mode: str = "lower") -> dict:
    t = text or ""
    mode = (mode or "lower").lower()
    if mode == "upper":
        r = t.upper()
    elif mode == "lower":
        r = t.lower()
    elif mode == "title":
        r = t.title()
    elif mode == "snake":
        r = re.sub(r"[\s\-]+", "_", t.strip().lower())
    elif mode == "kebab":
        r = re.sub(r"[\s_]+", "-", t.strip().lower())
    elif mode == "camel":
        parts = re.split(r"[\s_\-]+", t.strip())
        r = (parts[0].lower() + "".join(p.capitalize() for p in parts[1:])) if parts else ""
    else:
        return {"ok": False, "error": "mode: upper/lower/title/snake/kebab/camel"}
    return {"ok": True, "result": r}


def slugify(text: str) -> dict:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return {"ok": True, "result": s}


def text_stats(text: str) -> dict:
    t = text or ""
    words = re.findall(r"\S+", t)
    sentences = [s for s in re.split(r"[.!?]+", t) if s.strip()]
    return {"ok": True, "characters": len(t), "words": len(words), "lines": len(t.splitlines()),
            "sentences": len(sentences), "reading_minutes": round(len(words) / 200.0, 1)}


def word_frequency(text: str, top: int = 10) -> dict:
    from collections import Counter
    words = re.findall(r"[A-Za-z']+", (text or "").lower())
    return {"ok": True, "top": Counter(words).most_common(max(1, min(50, int(top or 10))))}


def extract_emails(text: str) -> dict:
    found = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}", text or "")))
    return {"ok": True, "count": len(found), "emails": found}


def extract_urls(text: str) -> dict:
    found = sorted(set(re.findall(r"https?://[^\s)>\]]+", text or "")))
    return {"ok": True, "count": len(found), "urls": found}


def regex_find(text: str, pattern: str) -> dict:
    try:
        matches = re.findall(pattern, text or "")
        return {"ok": True, "count": len(matches), "matches": matches[:200]}
    except Exception as e:
        return {"ok": False, "error": f"bad pattern: {e}"}


def find_replace(text: str, find: str, replace: str = "") -> dict:
    if not find:
        return {"ok": False, "error": "find is required"}
    new = (text or "").replace(find, replace or "")
    return {"ok": True, "result": new, "replacements": (text or "").count(find)}


def sort_lines(text: str, reverse: bool = False, numeric: bool = False) -> dict:
    lines = (text or "").splitlines()
    try:
        lines.sort(key=(lambda x: float(re.findall(r"-?\d+\.?\d*", x)[0]) if re.findall(r"-?\d+\.?\d*", x) else 0)
                   if numeric else str.lower, reverse=bool(reverse))
    except Exception:
        lines.sort(reverse=bool(reverse))
    return {"ok": True, "result": "\n".join(lines)}


def dedupe_lines(text: str) -> dict:
    seen, out = set(), []
    for ln in (text or "").splitlines():
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return {"ok": True, "result": "\n".join(out), "removed": len((text or "").splitlines()) - len(out)}


def reverse_text(text: str) -> dict:
    return {"ok": True, "result": (text or "")[::-1]}


def rot13(text: str) -> dict:
    return {"ok": True, "result": codecs.encode(text or "", "rot_13")}


def uuid4() -> dict:
    return {"ok": True, "uuid": str(_uuid.uuid4())}


def random_int(minimum: int = 0, maximum: int = 100) -> dict:
    lo, hi = int(minimum), int(maximum)
    if lo > hi:
        lo, hi = hi, lo
    return {"ok": True, "result": _random.SystemRandom().randint(lo, hi)}


def random_pick(items) -> dict:
    if isinstance(items, str):
        items = [x.strip() for x in items.split(",") if x.strip()]
    if not items:
        return {"ok": False, "error": "provide a list or comma-separated items"}
    return {"ok": True, "pick": _random.SystemRandom().choice(list(items))}


_LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
          "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam quis nostrud").split()


def lorem_ipsum(words: int = 30) -> dict:
    n = max(1, min(500, int(words or 30)))
    out = [_LOREM[i % len(_LOREM)] for i in range(n)]
    s = " ".join(out)
    return {"ok": True, "result": s[0].upper() + s[1:] + "."}


def int_to_roman(number: int) -> dict:
    n = int(number)
    if not (0 < n < 4000):
        return {"ok": False, "error": "number must be 1..3999"}
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, sym in vals:
        while n >= v:
            out += sym
            n -= v
    return {"ok": True, "result": out}


def roman_to_int(roman: str) -> dict:
    m = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    s = (roman or "").upper().strip()
    if not s or any(c not in m for c in s):
        return {"ok": False, "error": "invalid roman numeral"}
    total, prev = 0, 0
    for c in reversed(s):
        v = m[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return {"ok": True, "result": total}


def hex_to_rgb(hex_color: str) -> dict:
    h = (hex_color or "").lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        return {"ok": False, "error": "expected a hex color like #1a2b3c"}
    return {"ok": True, "rgb": [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]}


def rgb_to_hex(r: int, g: int, b: int) -> dict:
    try:
        vals = [max(0, min(255, int(x))) for x in (r, g, b)]
    except Exception:
        return {"ok": False, "error": "r,g,b must be numbers"}
    return {"ok": True, "hex": "#%02x%02x%02x" % tuple(vals)}


def number_to_words(number: int) -> dict:
    n = int(number)
    if n == 0:
        return {"ok": True, "result": "zero"}
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    scales = ["", " thousand", " million", " billion", " trillion"]

    def under1000(x):
        w = ""
        if x >= 100:
            w += ones[x // 100] + " hundred"
            x %= 100
            if x:
                w += " "
        if x >= 20:
            w += tens[x // 10]
            if x % 10:
                w += "-" + ones[x % 10]
        elif x > 0:
            w += ones[x]
        return w

    neg = n < 0
    n = abs(n)
    if n >= 1000 ** len(scales):
        return {"ok": False, "error": "number too large"}
    chunks = []
    i = 0
    while n > 0:
        if n % 1000:
            chunks.append(under1000(n % 1000) + scales[i])
        n //= 1000
        i += 1
    res = " ".join(reversed(chunks)).strip()
    return {"ok": True, "result": ("negative " + res) if neg else res}


def is_prime(number: int) -> dict:
    n = int(number)
    if n < 2:
        return {"ok": True, "number": n, "is_prime": False}
    if n % 2 == 0:
        return {"ok": True, "number": n, "is_prime": n == 2}
    i = 3
    while i * i <= n:
        if n % i == 0:
            return {"ok": True, "number": n, "is_prime": False, "factor": i}
        i += 2
    return {"ok": True, "number": n, "is_prime": True}


def days_between(date1: str, date2: str) -> dict:
    try:
        d1 = _dt.date.fromisoformat((date1 or "").strip())
        d2 = _dt.date.fromisoformat((date2 or "").strip())
    except Exception:
        return {"ok": False, "error": "use dates as YYYY-MM-DD"}
    return {"ok": True, "days": abs((d2 - d1).days)}


def tip_calculator(amount: float, percent: float = 15, people: int = 1) -> dict:
    try:
        amount = float(amount)
        percent = float(percent)
        people = max(1, int(people))
    except Exception:
        return {"ok": False, "error": "amount/percent must be numbers"}
    tip = round(amount * percent / 100, 2)
    total = round(amount + tip, 2)
    return {"ok": True, "tip": tip, "total": total, "per_person": round(total / people, 2), "people": people}


def bmi_calculator(weight_kg: float, height_cm: float) -> dict:
    try:
        w = float(weight_kg)
        h = float(height_cm) / 100.0
    except Exception:
        return {"ok": False, "error": "weight_kg and height_cm must be numbers"}
    if h <= 0:
        return {"ok": False, "error": "height must be positive"}
    bmi = round(w / (h * h), 1)
    cat = ("underweight" if bmi < 18.5 else "normal" if bmi < 25 else "overweight" if bmi < 30 else "obese")
    return {"ok": True, "bmi": bmi, "category": cat}
