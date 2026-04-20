"""OTP code generation + hashing.

- `generate_code` uses `secrets.randbelow(10**6)` for CSPRNG-quality randomness.
- `hash_code` uses SHA-256 HMAC keyed on `settings.candidate_jwt_secret` — the
  JWT signing secret doubles as the OTP hashing pepper. Rationale: we already
  treat that secret as DB-credential-tier, and a 6-digit OTP lives only briefly
  (hash wiped on verify, 10-minute expiry). HMAC-SHA256 is microseconds;
  no asyncio.to_thread needed.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.config import settings


def generate_code() -> str:
    """Return a 6-digit numeric OTP as a zero-padded string."""
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(code: str) -> str:
    """HMAC-SHA256 of the code using settings.candidate_jwt_secret as the key.

    Returns a hex string.
    """
    return hmac.new(
        key=settings.candidate_jwt_secret.encode("utf-8"),
        msg=code.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_code(code: str, stored_hash: str) -> bool:
    """Constant-time comparison of code's hash against stored value."""
    return hmac.compare_digest(hash_code(code), stored_hash)
