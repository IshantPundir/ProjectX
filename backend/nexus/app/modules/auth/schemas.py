"""Auth schemas — JWT payload, invite/me responses."""

from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Decoded JWT payload from Supabase Auth (ES256 via JWKS).

    Thin JWT: only sub, tenant_id, is_projectx_admin.
    Role/permission data is loaded per-request from DB.
    """

    sub: str  # Supabase Auth user UUID
    tenant_id: str = ""  # company UUID (empty for admins and pre-onboarding)
    email: str = ""
    role: str = "authenticated"  # Postgres role — always "authenticated", NOT for RBAC
    is_projectx_admin: bool = False  # True only for ProjectX internal team
    exp: int = 0


class CandidateTokenPayload(BaseModel):
    """Decoded JWT for single-use candidate session tokens (HS256)."""

    sub: str = ""
    session_id: str = ""
    tenant_id: str = ""
    exp: int = 0
    iat: int = 0


class VerifyInviteResponse(BaseModel):
    email: str
    client_name: str


class CompleteInviteRequest(BaseModel):
    raw_token: str


class CompleteInviteResponse(BaseModel):
    redirect_to: str  # "/onboarding" or "/"
    user_id: str
    tenant_id: str
    root_unit_id: str


class RoleAssignmentResponse(BaseModel):
    org_unit_id: str
    org_unit_name: str
    role_name: str
    permissions: list[str]


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    tenant_id: str
    client_name: str
    is_super_admin: bool
    onboarding_complete: bool
    has_org_units: bool
    workspace_mode: str
    assignments: list[RoleAssignmentResponse]
