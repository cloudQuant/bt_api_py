"""Small subset of the `pyotp` API used by the test-suite."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote


def random_base32(length: int = 20) -> str:
    """Return a base32 secret without padding."""
    return base64.b32encode(os.urandom(length)).decode("ascii").rstrip("=")


def _normalize_secret(secret: str) -> bytes:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode((secret + padding).encode("ascii"), casefold=True)


def _generate_otp(secret: str, counter: int, digits: int = 6) -> str:
    key = _normalize_secret(secret)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


class TOTP:
    """Minimal TOTP implementation compatible with the tests."""

    def __init__(
        self,
        secret: str,
        digits: int = 6,
        interval: int = 30,
        issuer: str | None = None,
        name: str | None = None,
    ):
        self.secret = secret
        self.digits = digits
        self.interval = interval
        self.issuer = issuer
        self.name = name

    def at(self, timestamp: float) -> str:
        counter = int(timestamp // self.interval)
        return _generate_otp(self.secret, counter, self.digits)

    def now(self) -> str:
        return self.at(time.time())

    def verify(self, token: str, valid_window: int = 0) -> bool:
        current_counter = int(time.time() // self.interval)
        for offset in range(-valid_window, valid_window + 1):
            if _generate_otp(self.secret, current_counter + offset, self.digits) == str(token):
                return True
        return False

    def provisioning_uri(self) -> str:
        label = quote(self.name or "")
        params = [f"secret={self.secret}"]
        if self.issuer:
            params.append(f"issuer={quote(self.issuer)}")
        return f"otpauth://totp/{label}?{'&'.join(params)}"


class HOTP:
    """Minimal HOTP implementation compatible with the tests."""

    def __init__(self, secret: str, digits: int = 6):
        self.secret = secret
        self.digits = digits

    def at(self, counter: int) -> str:
        return _generate_otp(self.secret, counter, self.digits)

    def verify(self, token: str, counter: int) -> bool:
        return self.at(counter) == str(token)
