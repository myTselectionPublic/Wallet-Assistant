from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, urlparse

from html import unescape


def generate_totp(seed: str, timestamp: float | None = None) -> str:
    """Generate a six-digit RFC 6238 token from a base32 seed."""
    key = _decode_totp_seed(seed)
    counter = int(time.time() if timestamp is None else timestamp) // 30
    digest = hmac.new(
        key,
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def is_valid_totp_seed(seed: str) -> bool:
    """Return whether a seed can be used to generate a TOTP token."""
    try:
        _decode_totp_seed(seed)
    except ValueError:
        return False
    return True


def _decode_totp_seed(seed: str) -> bytes:
    value = str(seed or "").strip()
    if value.lower().startswith("otpauth://"):
        value = parse_qs(urlparse(value).query).get("secret", [""])[0]
    normalized = "".join(value.replace("-", "").split()).upper()
    if not normalized:
        raise ValueError("TOTP seed is empty")
    padded = normalized + "=" * (-len(normalized) % 8)
    try:
        decoded = base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("TOTP seed is not valid base32") from err
    if not decoded:
        raise ValueError("TOTP seed is empty")
    return decoded


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
