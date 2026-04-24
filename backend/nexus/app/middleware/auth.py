import sqlalchemy
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.database import get_bypass_session
from app.modules.auth.service import verify_access_token, verify_candidate_token

logger = structlog.get_logger()

# Routes that skip authentication entirely
_PUBLIC_PATHS: set[str] = {
    "/health",
    "/docs",
    "/openapi.json",
}

# Candidate-session path prefix. The URL pattern is
# /api/candidate-session/{token}/... — the middleware extracts {token}
# from the path and verifies it via verify_candidate_token().
_CANDIDATE_PREFIX: str = "/api/candidate-session/"

# Path prefixes that skip auth entirely (public endpoints)
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/verify-invite",  # Public — invite token verification
    "/api/auth/accept-invite",  # Public — raw invite token proves possession
    "/api/auth/login",  # Public — credential exchange for session tokens
)


def _extract_candidate_token(path: str) -> str | None:
    """Pull {token} out of /api/candidate-session/{token}/... path.

    Returns None if the path doesn't include a token segment (e.g., a
    bare /api/candidate-session/ hit).
    """
    suffix = path[len(_CANDIDATE_PREFIX):]
    if not suffix:
        return None
    token = suffix.split("/", 1)[0]
    return token or None


class AuthMiddleware(BaseHTTPMiddleware):
    """Provider-agnostic JWT verification for dashboard + candidate flows.

    Dashboard flow:
      Extracts the Bearer token, verifies it via the JWKS-backed
      `verify_access_token()`, and attaches the identity payload to
      request.state. No role data in JWT — loaded per-request via
      UserContext.

    Candidate flow (Phase 3 wiring):
      For /api/candidate-session/{token}/... the middleware extracts
      {token} from the URL path segment and calls verify_candidate_token()
      (sig + exp + HS256 algo pinning). On success it then looks up the
      JTI in the `candidate_session_tokens` table to enforce:

        - TOKEN_UNKNOWN  — JWT signature valid but no matching row (forged
                           claim with guessable JTI or a revoked-via-DELETE
                           scenario).
        - TOKEN_SUPERSEDED — resend_invite minted a newer JWT and flipped
                           `superseded_at` on this row. Old token must stop
                           working immediately.

      It does NOT consume `used_at`. `used_at` is exclusively written by
      the `/start` endpoint via an atomic
      `UPDATE … WHERE used_at IS NULL RETURNING` — doing it in middleware
      would race with concurrent requests and break legitimate multi-
      endpoint flows (pre-check / consent / request-otp / verify-otp all
      run before `/start`, and rejoin scenarios in Phase 3D also rely on
      a still-usable token post-start).

      On success the candidate payload is attached to
      request.state.candidate_token_payload and request.state.tenant_id is
      set from the DB row (defense in depth — the JWT tenant_id claim is
      signed, but we trust the DB as the source of truth for what
      tenant/session this JTI belongs to).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        if path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        if path.startswith(_CANDIDATE_PREFIX):
            # Candidate-session path — verify the JWT embedded in the URL.
            token = _extract_candidate_token(path)
            if token is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing candidate session token in path"},
                )
            candidate_payload = verify_candidate_token(token)
            if candidate_payload is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired candidate session token"},
                )

            # DB-side gate: look up the JTI and reject if unknown or
            # superseded. We do NOT consume `used_at` here — that is the
            # sole responsibility of the /start endpoint via its atomic
            # UPDATE (see class docstring for the race/rejoin rationale).
            async with get_bypass_session() as db:
                row = (
                    await db.execute(
                        sqlalchemy.text(
                            "SELECT jti, tenant_id, session_id, superseded_at "
                            "FROM candidate_session_tokens WHERE jti = :jti"
                        ),
                        {"jti": str(candidate_payload.jti)},
                    )
                ).mappings().first()

            if row is None:
                logger.warning(
                    "auth.candidate_token_unknown",
                    jti=str(candidate_payload.jti),
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Token unknown", "code": "TOKEN_UNKNOWN"},
                )
            if row["superseded_at"] is not None:
                logger.warning(
                    "auth.candidate_token_superseded",
                    jti=str(candidate_payload.jti),
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Token has been superseded",
                        "code": "TOKEN_SUPERSEDED",
                    },
                )

            request.state.candidate_token_payload = candidate_payload
            # Prefer DB-side tenant_id as the source of truth for RLS
            # downstream. The JWT claim is signed, but the DB row is what
            # scheduler/session services will reconcile against when the
            # candidate endpoints read rows under tenant RLS.
            request.state.tenant_id = str(row["tenant_id"])
            # Do NOT fall through to the dashboard JWT check — candidate
            # sessions never carry a Bearer header.
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})

        token = auth_header.removeprefix("Bearer ").strip()

        payload = verify_access_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        # Thin state — only identity + tenant
        request.state.token_payload = payload
        request.state.user_id = payload.sub
        request.state.tenant_id = payload.tenant_id
        request.state.is_projectx_admin = payload.is_projectx_admin

        return await call_next(request)
