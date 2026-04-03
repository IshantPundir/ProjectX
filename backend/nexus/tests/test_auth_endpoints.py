"""Tests for auth endpoints — verify-invite, complete-invite, me.

The complete-invite endpoint is the most security-critical endpoint:
it atomically claims an invite token and creates a user row. These tests
verify error paths that don't require a live database.
"""

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def ec_key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def mock_jwks(ec_key_pair):
    _, public_key = ec_key_pair
    mock_jwk = MagicMock()
    mock_jwk.key = public_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_jwk
    return mock_client


def _make_jwt(private_key, **overrides) -> str:
    payload = {
        "sub": "auth-user-uuid",
        "tenant_id": "tenant-uuid",
        "app_role": "Company Admin",
        "email": "admin@acme.com",
        "role": "authenticated",
        "is_projectx_admin": False,
        "exp": int(time.time()) + 3600,
        "aud": "authenticated",
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="ES256")


class TestVerifyInvite:
    @pytest.mark.asyncio
    async def test_missing_token_param_returns_422(self):
        """Missing required query param should return 422."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/auth/verify-invite")
        assert resp.status_code == 422


class TestCompleteInvite:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self):
        """complete-invite without a Bearer token should return 401."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/complete-invite",
                json={"raw_token": "some-token"},
            )
        assert resp.status_code == 401
