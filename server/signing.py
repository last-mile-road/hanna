"""Signed, expiring image URLs.

No static mount, no guessable path: a URL is `?expires=<unix>&sig=<hmac>`, valid for
`SIGNED_URL_TTL_SECONDS`. The key is generated once into `output/.signing_key` (0600) and
is not in git.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from core import settings

_ALG = "sha256"


def _key() -> bytes:
    path = settings.SIGNING_KEY_PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()


def sign(resource: str, ttl: int | None = None) -> str:
    """Return `<expires>.<signature>` for a resource string."""
    expires = int(time.time()) + (ttl if ttl is not None else settings.SIGNED_URL_TTL_SECONDS)
    message = f"{resource}:{expires}".encode()
    signature = hmac.new(_key(), message, _ALG).hexdigest()
    return f"{expires}.{signature}"


def verify(resource: str, token: str) -> bool:
    try:
        expires_raw, signature = token.split(".", 1)
        expires = int(expires_raw)
    except (ValueError, AttributeError):
        return False
    if expires < int(time.time()):
        return False
    expected = hmac.new(_key(), f"{resource}:{expires}".encode(), _ALG).hexdigest()
    return hmac.compare_digest(expected, signature)


def query(resource: str, ttl: int | None = None) -> str:
    """`sig=` query fragment for a resource path."""
    return f"sig={sign(resource, ttl)}"
