"""Provider-agnostic JWT verification.

Business logic calls these functions — never the Supabase SDK directly.
Swapping from Supabase JWT to Cognito (or any other issuer) requires
changing only the config values and optionally the verification logic here.
"""

import structlog
import jwt

from app.config import settings
from app.modules.auth.schemas import CandidateTokenPayload, TokenPayload

logger = structlog.get_logger()


def verify_access_token(token: str) -> TokenPayload | None:
    """Verify a dashboard user JWT and return the decoded payload.

    Returns None if the token is invalid, expired, or malformed.
    """
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret or settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": True},
        )
        return TokenPayload(
            sub=payload.get("sub", ""),
            tenant_id=payload.get("tenant_id", ""),
            role=payload.get("role", ""),
            email=payload.get("email", ""),
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("auth.token_expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("auth.token_invalid", error=str(exc))
        return None


def verify_candidate_token(token: str) -> CandidateTokenPayload | None:
    """Verify a single-use candidate session JWT.

    Uses a separate signing key from dashboard tokens.
    """
    try:
        payload = jwt.decode(
            token,
            settings.candidate_jwt_secret,
            algorithms=[settings.jwt_algorithm],
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
