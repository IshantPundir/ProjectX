"""ATS credential encryption.

Wraps the `cryptography.fernet.MultiFernet` API so application code is
provider-agnostic. `settings.ats_credentials_encryption_keys` is a list of
Fernet keys; the FIRST key encrypts, all keys are tried for decrypt.

Rotation runbook: `docs/security/ats-credentials-rotation.md`.
"""
from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings

_fernet: MultiFernet | None = None


def _get_fernet() -> MultiFernet:
    """Lazy-init the MultiFernet from settings.ats_credentials_encryption_keys.

    Cached in module scope. Tests reset by setting `_fernet = None`.
    """
    global _fernet
    if _fernet is None:
        keys = settings.ats_credentials_encryption_keys
        if not keys:
            raise RuntimeError(
                "ats_credentials_encryption_keys is empty; encryption unavailable. "
                "Set ATS_CREDENTIALS_ENCRYPTION_KEYS in env."
            )
        _fernet = MultiFernet([Fernet(k.encode()) for k in keys])
    return _fernet


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a single string secret (access_token, refresh_token, …)."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a single string secret. Raises cryptography.fernet.InvalidToken
    if no key in the ring can decrypt."""
    return _get_fernet().decrypt(ciphertext).decode()


def encrypt_credentials_blob(plaintext: dict[str, Any]) -> bytes:
    """Encrypt a credentials dict (vendor-specific shape) for storage in
    ats_connections.credentials_ciphertext."""
    return _get_fernet().encrypt(json.dumps(plaintext, sort_keys=True).encode())


def decrypt_credentials_blob(ciphertext: bytes) -> dict[str, Any]:
    """Decrypt a credentials dict. Caller validates shape per vendor."""
    return json.loads(_get_fernet().decrypt(ciphertext).decode())
