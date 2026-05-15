from typing import Any, Literal

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
    """Team member row, unified storage model (spec 2026-05-14).

    Under the cutover, ATS-imported users live in the same `users` table
    as natively-invited users, tagged with `source = 'ats_<vendor>'` and
    `auth_user_id = NULL`. The legacy three-way 'user' | 'invite' | 'ats'
    enum is replaced by the boolean trio
    (has_auth_account, is_active, invite_state) plus the actual
    provenance string on `source`. The frontend derives the display
    category from these.
    """

    id: str
    email: str
    full_name: str | None
    # Provenance: 'native' for natively-invited users, 'ats_<vendor>' for
    # users imported from an ATS sync. Synthetic invite-only rows (a
    # pending UserInvite with no matching User row yet) ride on
    # source='native'.
    source: str
    external_id: str | None = None
    external_source_metadata: dict[str, Any] | None = None
    is_active: bool
    has_auth_account: bool
    invite_state: Literal["none", "pending", "accepted", "revoked"]
    is_super_admin: bool
    assignments: list[TeamMemberAssignment]
    created_at: str
    # Legacy convenience enum derived from the booleans above. Kept for
    # backwards-compat with callers that haven't migrated yet; new code
    # should read the booleans directly.
    status: str


class ResendInviteResponse(BaseModel):
    new_invite_id: str
    invite_url: str  # Only present in dry-run mode
