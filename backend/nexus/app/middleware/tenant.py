import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


class TenantMiddleware(BaseHTTPMiddleware):
    """Extracts tenant_id set by AuthMiddleware and binds it to structlog context.

    The actual SET LOCAL app.current_tenant is executed per-transaction in
    database.get_tenant_session(). This middleware just ensures tenant_id
    is available on request.state and in the log context.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tenant_id: str | None = getattr(request.state, "tenant_id", None)

        if tenant_id:
            structlog.contextvars.bind_contextvars(tenant_id=tenant_id)

        response = await call_next(request)

        if tenant_id:
            structlog.contextvars.unbind_contextvars("tenant_id")

        return response
