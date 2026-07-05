from __future__ import annotations

import hashlib
from html import unescape


def clean_text(value: str) -> str:
    return " ".join(unescape(str(value)).split())


def join_text(existing: str, extra: str) -> str:
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing} {extra}"


def first_meaningful_text(text: str) -> str:
    for part in text.split("  "):
        clean = clean_text(part)
        if len(clean) > 2:
            return clean
    return text[:120]


def stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
