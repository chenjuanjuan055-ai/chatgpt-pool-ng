"""TOTP (RFC 6238) helpers — no third-party dependency."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def normalize(secret: str) -> str:
    return str(secret or "").replace(" ", "").replace("-", "").strip().upper().rstrip("=")


def is_valid(secret: str) -> bool:
    value = normalize(secret)
    if not value:
        return False
    try:
        base64.b32decode(value + "=" * ((8 - len(value) % 8) % 8), casefold=True)
        return True
    except Exception:
        return False


def new_secret(byte_length: int = 20) -> str:
    return base64.b32encode(secrets.token_bytes(byte_length)).decode().rstrip("=")


def code(secret: str, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    value = normalize(secret)
    key = base64.b32decode(value + "=" * ((8 - len(value) % 8) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** digits)).zfill(digits)


def provisioning_uri(secret: str, account: str = "", issuer: str = "ChatGPT") -> str:
    label = quote(f"{issuer}:{account}" if account else issuer)
    return (
        f"otpauth://totp/{label}?secret={normalize(secret)}"
        f"&issuer={quote(issuer)}&period=30&digits=6"
    )
