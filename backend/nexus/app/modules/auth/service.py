"""Provider-agnostic JWT verification.

Dashboard tokens are verified via JWKS (ES256) — no shared secret required.
Candidate tokens still use HS256 with a separate signing key.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
import jwt
from jwt import PyJWKClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EngineDispatchToken, EngineTokenUse
from app.modules.auth.schemas import (
    CandidateTokenPayload,
    EngineTokenPayload,
    TokenPayload,
)
from app.modules.interview_runtime.errors import EngineTokenInvalidError

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

    Algorithm allowlist is fixed to ES256 — our Supabase auth hook signs
    only ES256, so accepting RS256 or anything else would only widen the
    attack surface (a compromised JWKS entry under a different alg could
    be accepted).

    Issuer check:
        Supabase GoTrue stamps `iss` with the API URL it sees from its OWN
        process, which in Supabase local under Docker is `127.0.0.1` even
        though the backend reaches Supabase via `host.docker.internal`.
        We therefore use `settings.supabase_jwt_issuer` if explicitly set
        (the right answer for any environment where the network-reachable
        Supabase URL doesn't match the issuer Supabase advertises).
        Otherwise we fall back to `{supabase_url}/auth/v1` (works for
        Supabase Cloud, where the two URLs are the same). Empty for both
        skips the issuer check — only safe in tests / JWKS-mocked CI.

    Audience check:
        Supabase signs user tokens with `aud = "authenticated"`. Always
        enforce it — the string is a GoTrue invariant, not a per-
        deployment setting.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        decode_kwargs: dict = {
            "algorithms": ["ES256"],
            "audience": "authenticated",
            "options": {"verify_exp": True, "verify_aud": True},
        }
        expected_issuer: str | None = None
        if settings.supabase_jwt_issuer:
            expected_issuer = settings.supabase_jwt_issuer
        elif settings.supabase_url:
            expected_issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
        if expected_issuer:
            decode_kwargs["issuer"] = expected_issuer
        payload = jwt.decode(
            token,
            signing_key.key,
            **decode_kwargs,
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
    """Verify a single-use candidate session JWT.

    Signing algorithm is hardcoded to HS256 as a policy decision — never
    read from config. A misconfigured environment variable must not be
    able to weaken verification (e.g., accept 'none' or swap to a weaker
    HMAC variant).
    """
    try:
        payload = jwt.decode(
            token,
            settings.candidate_jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )
        return CandidateTokenPayload(
            jti=payload["jti"],
            sub=payload["sub"],
            session_id=payload["session_id"],
            tenant_id=payload["tenant_id"],
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.candidate_token_expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("auth.candidate_token_invalid", error=str(exc))
        return None


def create_candidate_token(
    *,
    jti: uuid.UUID,
    candidate_id: uuid.UUID,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> tuple[str, datetime]:
    """Mint a single-use candidate JWT.

    Returns (token, expires_at). The caller is responsible for inserting a
    matching row into candidate_session_tokens with the same `jti`.

    TTL controlled by settings.candidate_jwt_ttl_hours (default 72).
    """
    iat = datetime.now(UTC)
    exp = iat + timedelta(hours=settings.candidate_jwt_ttl_hours)
    claims = {
        "jti": str(jti),
        "sub": str(candidate_id),
        "session_id": str(session_id),
        "tenant_id": str(tenant_id),
        "iat": int(iat.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(claims, settings.candidate_jwt_secret, algorithm="HS256")
    return token, exp


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


async def verify_engine_token(
    token: str,
    db: AsyncSession,
    *,
    expected_session_id: uuid.UUID,
    endpoint: Literal["config", "results"],
    used_ip: str | None = None,
) -> EngineTokenPayload:
    """Verify a single-use engine dispatch JWT.

    On success, atomically records (jti, endpoint) in engine_token_uses
    and returns the decoded payload. Raises EngineTokenInvalidError on any
    failure — algorithm rejection, claim shape, purpose mismatch, session_id
    mismatch, expiry, replay, unknown parent jti, revoked parent.

    The caller MUST be on a bypass-RLS session — both engine_token_uses
    (bypass-only by design) and the cross-tenant engine_dispatch_tokens
    lookup require it.

    Algorithm is hardcoded to ["HS256"]. Never read it from config — a
    misconfigured env must not be able to weaken verification.
    """
    try:
        decoded = jwt.decode(
            token,
            settings.interview_engine_jwt_secret,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise EngineTokenInvalidError(f"jwt decode failed: {exc}") from exc

    try:
        payload = EngineTokenPayload.model_validate(decoded)
    except ValidationError as exc:
        raise EngineTokenInvalidError(f"claim shape mismatch: {exc}") from exc

    # purpose is enforced by the Literal in EngineTokenPayload (validation
    # rejects any other value), but make the check explicit here too —
    # belt + suspenders for a security-critical claim.
    if payload.purpose != "interview_engine":
        raise EngineTokenInvalidError("purpose claim mismatch")
    if payload.sub != expected_session_id:
        raise EngineTokenInvalidError("session_id mismatch")

    # Atomic single-use INSERT. ON CONFLICT DO NOTHING returns 0 rows on
    # replay; the FK to engine_dispatch_tokens(jti) raises IntegrityError
    # if the parent row doesn't exist (unknown jti).
    insert_stmt = (
        pg_insert(EngineTokenUse)
        .values(jti=payload.jti, endpoint=endpoint, used_ip=used_ip)
        .on_conflict_do_nothing(index_elements=["jti", "endpoint"])
    )
    try:
        result = await db.execute(insert_stmt)
    except IntegrityError as exc:
        # FK violation — token's jti has no row in engine_dispatch_tokens.
        # Ensure the bypass session is rolled back to a usable state for
        # the caller's subsequent queries; the caller's transaction
        # boundary owns commit/rollback for the request.
        raise EngineTokenInvalidError("token unknown") from exc

    if result.rowcount == 0:
        raise EngineTokenInvalidError("token already used for this endpoint")

    # Defense-in-depth: confirm the parent token is not revoked. Expiry
    # was already enforced by jwt.decode via the exp claim; this check
    # only catches admin-revocation, which can happen between issuance
    # and use.
    #
    # Ordering note: the INSERT into engine_token_uses commits before this
    # SELECT runs. If a concurrent admin revoke commits in that window, the
    # use row stays in engine_token_uses and we still reject this request
    # ("revoked"). A subsequent retry with the same (jti, endpoint) hits
    # rowcount=0 and is rejected as "already used." Both rejections are
    # correct — the leftover use row is benign.
    parent = (
        await db.execute(
            select(EngineDispatchToken).where(EngineDispatchToken.jti == payload.jti)
        )
    ).scalar_one_or_none()
    if parent is None or parent.revoked_at is not None:
        raise EngineTokenInvalidError("token unknown or revoked")

    return payload
