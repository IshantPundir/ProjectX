from pydantic import BaseModel


class ProvisionClientRequest(BaseModel):
    client_name: str
    admin_email: str
    domain: str = ""
    industry: str = ""
    plan: str = "trial"


class ProvisionClientResponse(BaseModel):
    client_id: str
    invite_id: str
    admin_email: str
    invite_url: str  # Only present in dry-run mode; empty in production


class ClientListItem(BaseModel):
    client_id: str
    client_name: str
    domain: str | None
    plan: str
    onboarding_complete: bool
    admin_email: str | None
    invite_status: str | None
    created_at: str
    status: str  # "active" | "blocked" | "deleted"
    blocked_at: str | None
    deleted_at: str | None


class ClientStatusResponse(BaseModel):
    client_id: str
    status: str
    blocked_at: str | None
    deleted_at: str | None


class HardDeleteRequest(BaseModel):
    """Body for POST /api/admin/clients/{id}/hard-delete.

    `confirmation_name` must equal the target client's `name` exactly,
    enforced server-side. The admin UI also gates the submit button on
    this match — server-side check is defense in depth against direct
    API calls.
    """

    confirmation_name: str


class HardDeleteResponse(BaseModel):
    """Returned on successful hard delete. The `clients` row is gone, so
    `purged_at` is synthesized at response time, not read back from the
    DB."""

    client_id: str
    purged_at: str  # ISO-8601 UTC
    auth_users_purged: int
    auth_users_failed: int
