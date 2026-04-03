from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
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
    exp: int = 0


class CandidateTokenPayload(BaseModel):
    """Decoded JWT for single-use candidate session tokens (HS256)."""
    sub: str = ""
    session_id: str = ""
    tenant_id: str = ""
    exp: int = 0
    iat: int = 0
