from pydantic import BaseModel


class TeamInviteRequest(BaseModel):
    email: str


class TeamInviteResponse(BaseModel):
    invite_id: str
    email: str
    invite_url: str  # Only present in dry-run mode; empty in production


class TeamMemberAssignment(BaseModel):
    org_unit_id: str
    org_unit_name: str
    role_name: str


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    is_active: bool
    is_super_admin: bool
    source: str  # "user" or "invite"
    status: str  # "active", "inactive" for users; "pending" for invites
    assignments: list[TeamMemberAssignment]
    created_at: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
