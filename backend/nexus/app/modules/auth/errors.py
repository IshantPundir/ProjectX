"""Auth-layer exceptions + helpers for the tenant-suspension gate.

`AccountSuspendedError` signals that the caller's tenant is in a non-active
lifecycle state. It is mapped to 403 by the handler in `app/main.py` with
a stable error code (`ACCOUNT_SUSPENDED`) the frontend can pattern-match on.

`suspended_response` is the canonical 403 envelope for use from middleware
(which can't rely on FastAPI's exception_handler dispatch — Starlette
middleware errors bubble outside the app's handler scope).
"""

from fastapi.responses import JSONResponse


class AccountSuspendedError(Exception):
    """Tenant is blocked or soft-deleted — caller must not be served.

    `status` is one of `"blocked"` or `"deleted"` and is included in the
    response envelope so the frontend can render the right message.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Tenant is {status}")


def suspended_response(status: str) -> JSONResponse:
    """Canonical 403 ACCOUNT_SUSPENDED response envelope. Used by both the
    AccountSuspendedError handler and AuthMiddleware (which short-circuits
    by returning a Response directly, not by raising)."""
    return JSONResponse(
        status_code=403,
        content={
            "detail": "This account has been suspended. Contact your administrator.",
            "code": "ACCOUNT_SUSPENDED",
            "status": status,
        },
    )
