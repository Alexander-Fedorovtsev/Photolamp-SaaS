"""Opaque capability token helpers; plaintext tokens are never persisted."""

import hashlib
import secrets


def issue_capability() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, capability_digest(token)


def capability_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
