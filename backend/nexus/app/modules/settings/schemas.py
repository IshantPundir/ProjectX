from typing import Literal

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
    # "user" = real User row; "invite" = pending UserInvite; "ats" = ATS-imported
    # user not yet a member or invited.
    source: Literal["user", "invite", "ats"]
    # "active"/"inactive" for users, "pending" for invites, "ats_unlinked" for ATS rows.
    status: str
    assignments: list[TeamMemberAssignment]
    created_at: str
    # ATS-only — populated when source == "ats". The external_user_id is the
    # vendor's stable identifier (used as the invite's source pointer if we
    # ever want to backtrack which ATS row drove an invite).
    external_user_id: str | None = None
    ats_vendor: str | None = None
    external_role: str | None = None


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
