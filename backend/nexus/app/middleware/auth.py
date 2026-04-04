import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.modules.auth.service import verify_access_token

logger = structlog.get_logger()

# Routes that skip authentication entirely
_PUBLIC_PATHS: set[str] = {
    "/health",
    "/docs",
    "/openapi.json",
}

# Path prefixes that use candidate JWT (not dashboard auth)
_CANDIDATE_PREFIXES: tuple[str, ...] = (
    "/api/candidate-session/",
)

# Path prefixes that skip auth entirely (public endpoints)
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/auth/verify-invite",  # Public — invite token verification
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Provider-agnostic JWT verification.

    Extracts the Bearer token, verifies it, and attaches
    sub, tenant_id, is_projectx_admin to request.state.
    No role data in JWT — loaded per-request via UserContext.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        if path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        if path.startswith(_CANDIDATE_PREFIXES):
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
