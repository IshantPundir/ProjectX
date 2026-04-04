from pydantic import BaseModel


class TeamInviteRequest(BaseModel):
    email: str
    role: str  # "Admin", "Recruiter", "Hiring Manager", "Interviewer", "Observer"
    is_admin: bool = False
    permissions: list[str] = []
    org_unit_id: str | None = None


class TeamInviteResponse(BaseModel):
    invite_id: str
    email: str
    role: str
    invite_url: str  # Only present in dry-run mode; empty in production


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    is_admin: bool
    permissions: list[str]
    source: str  # "user" or "invite"
    status: str  # "active", "inactive" for users; "pending" for invites
    created_at: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
