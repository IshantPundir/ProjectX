"""Opaque capability tokens for the public recordings share link.

The plaintext token lives ONLY in the rendered PDF link. We store its keyed
HMAC-SHA256 hash on the report_shares row, so a DB-only leak yields no working
links. The 256-bit token is unguessable and unenumerable.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config import settings


def generate_share_token() -> str:
    """A 256-bit URL-safe random token (~43 chars)."""
    return secrets.token_urlsafe(32)


def hash_share_token(token: str) -> str:
    """Keyed HMAC-SHA256 hex digest of the token (constant-time-comparable via
    indexed equality lookup)."""
    return hmac.new(
        settings.recording_share_hmac_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
