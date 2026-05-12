from __future__ import annotations

import json
import pytest
from cryptography.fernet import Fernet, InvalidToken


def _set_keys(monkeypatch, *keys: str) -> None:
    """Re-bind settings.ats_credentials_encryption_keys for the test."""
    from app.config import settings
    monkeypatch.setattr(settings, "ats_credentials_encryption_keys", list(keys))
    # Reset module-level _fernet cache so it picks up new keys
    from app.modules.ats import crypto
    crypto._fernet = None


def test_encrypt_decrypt_secret_round_trip(monkeypatch):
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    key = Fernet.generate_key().decode()
    _set_keys(monkeypatch, key)

    plaintext = "ceipal-bearer-token-abc123"
    ct = encrypt_secret(plaintext)
    assert isinstance(ct, bytes)
    assert plaintext not in ct.decode(errors="ignore")  # not stored in plain
    assert decrypt_secret(ct) == plaintext


def test_encrypt_decrypt_credentials_blob_round_trip(monkeypatch):
    from app.modules.ats.crypto import (
        encrypt_credentials_blob, decrypt_credentials_blob,
    )
    key = Fernet.generate_key().decode()
    _set_keys(monkeypatch, key)

    blob = {"email": "x@y.com", "password": "p@ss!", "api_key": "k"}
    ct = encrypt_credentials_blob(blob)
    assert decrypt_credentials_blob(ct) == blob


def test_multifernet_rotation_decrypts_old_then_new(monkeypatch):
    """After adding a new key to the front, old ciphertexts still decrypt."""
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypt under old key only
    _set_keys(monkeypatch, old_key)
    old_ct = encrypt_secret("legacy")

    # Rotate: new_key first, old_key still present
    _set_keys(monkeypatch, new_key, old_key)
    assert decrypt_secret(old_ct) == "legacy"   # old ciphertext still readable

    new_ct = encrypt_secret("rotated")
    # Drop old key; new ciphertext still readable
    _set_keys(monkeypatch, new_key)
    assert decrypt_secret(new_ct) == "rotated"


def test_decrypt_with_only_unknown_key_raises(monkeypatch):
    from app.modules.ats.crypto import encrypt_secret, decrypt_secret
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()

    _set_keys(monkeypatch, key_a)
    ct = encrypt_secret("x")

    _set_keys(monkeypatch, key_b)  # totally different keyring
    with pytest.raises(InvalidToken):
        decrypt_secret(ct)


def test_get_fernet_raises_when_keys_empty(monkeypatch):
    _set_keys(monkeypatch)  # no keys
    from app.modules.ats import crypto
    with pytest.raises(RuntimeError, match="ats_credentials_encryption_keys is empty"):
        crypto._get_fernet()
