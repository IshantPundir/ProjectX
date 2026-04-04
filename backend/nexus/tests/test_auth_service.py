"""Tests for provider-agnostic JWT verification (ES256 via JWKS)."""

import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.modules.auth.schemas import TokenPayload


@pytest.fixture
def ec_key_pair():
    """Generate a fresh EC P-256 key pair for testing."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture
def mock_jwks_client(ec_key_pair):
    """Mock PyJWKClient that returns our test public key."""
    _, public_key = ec_key_pair
    mock_jwk = MagicMock()
    mock_jwk.key = public_key
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_jwk
    return mock_client


def _make_token(private_key, **overrides) -> str:
    """Create a JWT signed with the test private key."""
    payload = {
        "sub": "test-user-uuid",
        "tenant_id": "test-tenant-uuid",
        "email": "admin@acme.com",
        "role": "authenticated",
        "is_projectx_admin": False,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "aud": "authenticated",
    }
    payload.update(overrides)
    return pyjwt.encode(payload, private_key, algorithm="ES256")


class TestVerifyAccessToken:
    def test_valid_token_returns_payload(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token
            result = verify_access_token(token)

        assert result is not None
        assert isinstance(result, TokenPayload)
        assert result.sub == "test-user-uuid"
        assert result.tenant_id == "test-tenant-uuid"
        assert result.email == "admin@acme.com"
        assert result.is_projectx_admin is False

    def test_expired_token_returns_none(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key, exp=int(time.time()) - 10)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token
            result = verify_access_token(token)

        assert result is None

    def test_tampered_token_returns_none(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key)
        tampered = token[:-5] + "XXXXX"

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token
            result = verify_access_token(tampered)

        assert result is None

    def test_projectx_admin_token(self, ec_key_pair, mock_jwks_client):
        private_key, _ = ec_key_pair
        token = _make_token(private_key, tenant_id="", is_projectx_admin=True)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token
            result = verify_access_token(token)

        assert result is not None
        assert result.is_projectx_admin is True
        assert result.tenant_id == ""

    def test_empty_custom_claims_returns_defaults(self, ec_key_pair, mock_jwks_client):
        """Token without custom claims (new user, no invite) should return defaults."""
        private_key, _ = ec_key_pair
        token = _make_token(private_key, tenant_id="", is_projectx_admin=False)

        with patch("app.modules.auth.service._get_jwks_client", return_value=mock_jwks_client):
            from app.modules.auth.service import verify_access_token
            result = verify_access_token(token)

        assert result is not None
        assert result.tenant_id == ""
        assert result.is_projectx_admin is False
