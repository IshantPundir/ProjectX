"""Auth schemas — JWT payload, invite/me responses."""

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


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
    """Decoded JWT for single-use candidate session tokens (HS256).

    All UUID-shaped claims are parsed into `uuid.UUID` so downstream code
    (middleware single-use check, session service authz) can compare and
    query against the `candidate_session_tokens` PK without re-parsing.
    `jti` is the PK of the row in `candidate_session_tokens`.
    """

    jti: uuid.UUID
    sub: uuid.UUID  # candidate_id
    session_id: uuid.UUID
    tenant_id: uuid.UUID
    exp: int = 0
    iat: int = 0


class EngineTokenPayload(BaseModel):
    """Decoded claims of a Nexus-minted engine dispatch JWT (HS256, single-use).

    Previously consumed by the interview-engine worker via the retired
    /api/internal/* HTTP boundary. Kept here until the ORM models and
    migration that drop engine_dispatch_tokens / engine_token_uses land
    (Phase 3 Task 14).
    """

    sub: uuid.UUID            # session_id
    tenant_id: uuid.UUID
    purpose: Literal["interview_engine"]
    iat: int
    exp: int
    jti: uuid.UUID


class VerifyInviteResponse(BaseModel):
    email: str
    client_name: str


class RoleAssignmentResponse(BaseModel):
    org_unit_id: str
    org_unit_name: str
    role_name: str
    permissions: list[str]


class AcceptInviteRequest(BaseModel):
    """Body for POST /api/auth/accept-invite.

    Public endpoint. `raw_token` is the single-use invite proof;
    `password` is what the new auth user will be created with.
    """

    raw_token: str
    password: str


class AcceptInviteResponse(BaseModel):
    """Success response for POST /api/auth/accept-invite.

    `access_token` + `refresh_token` are what the browser client feeds
    into `supabase.auth.setSession(...)` to install the cookie session.
    `expires_in` is seconds until the access_token expires.
    `redirect_to` is a same-origin relative path (validated client-side
    to avoid open-redirect).
    """

    access_token: str
    refresh_token: str
    expires_in: int
    redirect_to: str


class LoginRequest(BaseModel):
    """Request body for POST /api/auth/login.

    `email` uses `EmailStr` so the 422 path catches malformed addresses
    before the handler touches the AuthProvider — no user enumeration
    surface for syntax errors.

    `password` is bounded at 1..128 characters: enforces the user
    actually typed something (`min_length=1`) and stops oversized
    payloads at the validation layer (well below FastAPI's request
    body size limit, well above any reasonable password length).
    """

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Response body for POST /api/auth/login.

    `redirect_to` is computed server-side from `users.onboarding_complete`
    so the frontend never has to decode the access_token to pick a
    post-login route.
    """

    access_token: str
    refresh_token: str
    expires_in: int
    redirect_to: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    tenant_id: str
    client_name: str
    is_super_admin: bool
    onboarding_complete: bool
    has_org_units: bool
    assignments: list[RoleAssignmentResponse]
