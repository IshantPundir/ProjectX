"""Provider-agnostic JWT verification.

Dashboard tokens are verified via JWKS (ES256) — no shared secret required.
Candidate tokens still use HS256 with a separate signing key.
"""

import structlog
import jwt
from jwt import PyJWKClient

from app.config import settings
from app.modules.auth.schemas import CandidateTokenPayload, TokenPayload

logger = structlog.get_logger()

# Module-level singleton — PyJWKClient handles key caching internally
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    """Lazy-init the JWKS client (cached for process lifetime)."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
    return _jwks_client


def verify_access_token(token: str) -> TokenPayload | None:
    """Verify a dashboard user JWT via JWKS (ES256).

    Returns None if the token is invalid, expired, or malformed.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            options={"verify_exp": True, "verify_aud": False},
        )
        return TokenPayload(
            sub=payload["sub"],
            tenant_id=payload.get("tenant_id", ""),
            email=payload.get("email", ""),
            role=payload.get("role", "authenticated"),
            is_projectx_admin=payload.get("is_projectx_admin", False),
            exp=payload.get("exp", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.token_expired")
        return None
    except Exception as exc:
        logger.warning("auth.token_invalid", error=str(exc))
        return None


def verify_candidate_token(token: str) -> CandidateTokenPayload | None:
    """Verify a single-use candidate session JWT (HS256)."""
    try:
        payload = jwt.decode(
            token,
            settings.candidate_jwt_secret,
            algorithms=[settings.candidate_jwt_algorithm],
            options={"verify_exp": True},
        )
        return CandidateTokenPayload(
            sub=payload.get("sub", ""),
            session_id=payload.get("session_id", ""),
            tenant_id=payload.get("tenant_id", ""),
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.candidate_token_expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("auth.candidate_token_invalid", error=str(exc))
        return None


def require_projectx_admin():
    """FastAPI dependency factory — rejects requests without is_projectx_admin claim.

    Usage: dependencies=[require_projectx_admin()]  ← called, returns Depends()
    Same pattern as require_roles(). Do NOT wrap in Depends() at the call site.
    """
    from fastapi import Depends, HTTPException, Request

    async def _check(request: Request) -> TokenPayload:
        payload: TokenPayload | None = getattr(request.state, "token_payload", None)
        if payload is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if not payload.is_projectx_admin:
            raise HTTPException(status_code=403, detail="Not a ProjectX admin")
        return payload

    return Depends(_check)
