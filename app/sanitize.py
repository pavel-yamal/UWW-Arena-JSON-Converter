"""Make every value safe to put on air: plain, trimmed, printable strings.

Rules (applied to every field of the vMix document):
  None / bool / "null" / "undefined" / "NaN" ...  -> ""
  65.0 -> "65", 7 -> "7"
  lists -> items joined by a space, objects -> their readable field
  HTML entities decoded (&amp; -> &), HTML tags removed, UTF-8 mojibake repaired (Ð˜Ð²Ð°Ð½ -> Иван)
  control / zero-width / private-use / replacement characters removed
  any run of whitespace (\n, \t, nbsp, double spaces) -> one space
"""
from __future__ import annotations

import html
import math
import re
import unicodedata
from typing import Any

JUNK_TOKENS = {"null", "none", "nil", "undefined", "nan", "true", "false", "[object object]", "n/a", "-"}
_MOJIBAKE_HINT = re.compile("[ÐÑÃÂâ][\u0080-ÿŒ-˜–-™]")
_WS = re.compile(r"\s+")
_TAG = re.compile(r"</?[a-zA-Z][^<>]{0,200}>")
_COUNTRY = re.compile(r"^[A-Z]{2,3}$")


def _cp1252_bytes(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if ord(ch) > 0xFF:
                raise
            out.append(ord(ch))  # bytes undefined in cp1252 (0x81, 0x8D, 0x9D …)
    return bytes(out)


def fix_mojibake(s: str) -> str:
    """UTF-8 text that was decoded as cp1252/latin-1 (e.g. 'Ð˜Ð²Ð°Ð½')."""
    if not _MOJIBAKE_HINT.search(s):
        return s
    try:
        return _cp1252_bytes(s).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def clean_text(s: str) -> str:
    s = html.unescape(html.unescape(s))
    s = fix_mojibake(s)
    s = _TAG.sub(" ", s)  # vMix shows "<b>" literally
    s = unicodedata.normalize("NFC", s)
    s = "".join(
        " " if ch.isspace() else ch
        for ch in s
        if ch.isspace() or (unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs", "Cn") and ch != "�")
    )
    s = _WS.sub(" ", s).strip()
    return "" if s.lower() in JUNK_TOKENS else s


def clean(v: Any) -> str:
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        return str(int(v)) if v.is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return " ".join(x for x in (clean(i) for i in v) if x)
    if isinstance(v, dict):
        for k in ("alternateName", "shortName", "code", "name", "displayName", "fullName"):
            if v.get(k) not in (None, ""):
                return clean(v[k])
        return ""
    return clean_text(str(v))


def clean_score(v: Any) -> str:
    """Scores are always digits; anything unusable becomes "0"."""
    s = clean(v)
    try:
        f = float(s)
    except ValueError:
        return "0"
    return str(int(f)) if math.isfinite(f) and f >= 0 else "0"


def flag(country: str) -> str:
    """Flag file name only for a real 2–3 letter code (RUS -> RUS.png)."""
    code = country.upper()
    return f"{code}.png" if _COUNTRY.match(code) else ""


def ascii_key(s: str) -> str:
    """Mat name -> safe column prefix (ASCII letters/digits/_), '' if not possible."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", clean(s)).strip("_")
    return s if s.isascii() else ""
