import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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
      {token} from the URL path segment and calls verify_candidate_token().
      On success the candidate payload is attached to
      request.state.candidate_token_payload so downstream handlers never
      have to re-verify manually — classic "forgot to check auth" bug
      class is eliminated at the layer.

      Note: single-use / replay-prevention markers are a Phase 3 TODO —
      currently verify_candidate_token() only checks signature + expiry +
      hardcoded HS256 algorithm. The stubs under
      /api/candidate-session/{token}/start and /consent still return
      not_implemented, but now do so after authentication is enforced.
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
            request.state.candidate_token_payload = candidate_payload
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
