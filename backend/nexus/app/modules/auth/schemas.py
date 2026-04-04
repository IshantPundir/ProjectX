from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    ADMIN = "Admin"
    COMPANY_ADMIN = "Company Admin"
    RECRUITER = "Recruiter"
    HIRING_MANAGER = "Hiring Manager"
    INTERVIEWER = "Interviewer"
    OBSERVER = "Observer"


class TokenPayload(BaseModel):
    """Decoded JWT payload from Supabase Auth (ES256 via JWKS).

    Custom claims (app_role, tenant_id, is_projectx_admin) are injected
    by the projectx_custom_access_token_hook Postgres function.
    """
    sub: str                         # Supabase Auth user UUID
    tenant_id: str = ""              # company UUID (empty for admins and pre-onboarding)
    app_role: str = ""               # RBAC role: Company Admin, Recruiter, etc.
    email: str = ""
    role: str = "authenticated"      # Postgres role — always "authenticated", NOT for RBAC
    is_projectx_admin: bool = False  # True only for ProjectX internal team
    is_admin: bool = False
    org_unit_id: str | None = None
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
    role: str
    client_name: str


class CompleteInviteRequest(BaseModel):
    raw_token: str


class CompleteInviteResponse(BaseModel):
    redirect_to: str  # "/onboarding" or "/"
    user_id: str
    tenant_id: str
    role: str


class MeResponse(BaseModel):
    user_id: str
    auth_user_id: str
    email: str
    full_name: str | None
    role: str
    is_admin: bool
    permissions: list[str]
    org_unit_id: str | None
    tenant_id: str
    client_name: str
    onboarding_complete: bool
    has_org_units: bool           # TRUE if tenant has ≥1 org unit created
