import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.modules.auth.service import verify_access_token
from app.modules.auth.schemas import TokenPayload

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


class AuthMiddleware(BaseHTTPMiddleware):
    """Provider-agnostic JWT verification and RBAC enforcement.

    Extracts the Bearer token, verifies it through the auth module's
    provider-agnostic interface, and attaches the token payload to
    request.state for downstream use.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip auth for public endpoints
        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        # Candidate paths use a different token flow — handled in the session module
        if path.startswith(_CANDIDATE_PREFIXES):
            return await call_next(request)

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid authorization header"})

        token = auth_header.removeprefix("Bearer ").strip()

        # Verify through provider-agnostic interface
        payload = verify_access_token(token)
        if payload is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        # Attach to request state for downstream handlers
        request.state.token_payload = payload
        request.state.user_id = payload.sub
        request.state.tenant_id = payload.tenant_id
        request.state.role = payload.role

        return await call_next(request)


def require_roles(*allowed_roles: str):
    """FastAPI dependency that enforces RBAC on a route.

    Usage:
        @router.get("/jobs", dependencies=[Depends(require_roles("Recruiter", "Company Admin"))])
    """
    from fastapi import Depends, HTTPException, Request as FastAPIRequest

    async def _check(request: FastAPIRequest) -> TokenPayload:
        payload: TokenPayload | None = getattr(request.state, "token_payload", None)
        if payload is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if payload.role not in allowed_roles:
            logger.warning("rbac.denied", role=payload.role, allowed=allowed_roles, path=request.url.path)
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return payload

    return Depends(_check)
