from pydantic import BaseModel


class TeamInviteRequest(BaseModel):
    email: str  # Email only — role/permissions/org assigned later


class TeamInviteResponse(BaseModel):
    invite_id: str
    email: str
    role: str | None
    invite_url: str  # Only present in dry-run mode; empty in production


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str | None
    is_active: bool
    is_admin: bool
    permissions: list[str]
    source: str  # "user" or "invite"
    status: str  # "active", "inactive" for users; "pending" for invites
    created_at: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
