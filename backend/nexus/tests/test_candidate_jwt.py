"""Tests for create_candidate_token() and verify_candidate_token() symmetry."""
import uuid
from datetime import datetime, timedelta, UTC

import jwt as pyjwt

from app.config import settings
from app.modules.auth.service import create_candidate_token, verify_candidate_token


def test_create_candidate_token_round_trips():
    tenant_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    session_id = uuid.uuid4()
    jti = uuid.uuid4()

    token, expires_at = create_candidate_token(
        jti=jti,
        candidate_id=candidate_id,
        session_id=session_id,
        tenant_id=tenant_id,
    )
    assert isinstance(token, str)
    assert expires_at > datetime.now(UTC)

    payload = verify_candidate_token(token)
    assert payload.jti == jti
    assert payload.sub == candidate_id
    assert payload.session_id == session_id
    assert payload.tenant_id == tenant_id


def test_create_candidate_token_honors_ttl_env_var(monkeypatch):
    monkeypatch.setattr(settings, "candidate_jwt_ttl_hours", 1, raising=False)
    _, expires_at = create_candidate_token(
        jti=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )
    delta = expires_at - datetime.now(UTC)
    assert timedelta(minutes=55) < delta < timedelta(minutes=65)


def test_expired_candidate_token_rejected():
    """Fabricate a token with an exp in the past; verify must reject.

    verify_candidate_token() returns None on ExpiredSignatureError (it
    swallows the exception and logs a structured warning) — that None is
    the contract the middleware already relies on. Rejection therefore
    means "returns None", not "raises".
    """
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "jti": str(uuid.uuid4()),
        "sub": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "iat": now - 7200,
        "exp": now - 3600,
    }
    token = pyjwt.encode(claims, settings.candidate_jwt_secret, algorithm="HS256")
    assert verify_candidate_token(token) is None
