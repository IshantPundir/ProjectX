from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None
    # Column-level fields. All optional on create — recruiter fills them
    # later via the detail page edit mode. None means "leave NULL"; an
    # explicit value is set.
    about: str | None = None
    industry: str | None = None
    hiring_bar: str | None = None
    website: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    metadata: dict | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None
    set_deletable_by: bool = False
    admin_delete_disabled: bool | None = None

    # Column-level profile + address fields. Each is paired with a
    # `set_<field>` sentinel: only fields where the sentinel is True are
    # persisted (matches the existing `set_metadata` pattern). An empty
    # string with `set_<field>=True` clears the column to NULL after
    # .strip().
    about: str | None = None
    set_about: bool = False
    industry: str | None = None
    set_industry: bool = False
    hiring_bar: str | None = None
    set_hiring_bar: bool = False
    website: str | None = None
    set_website: bool = False
    country: str | None = None
    set_country: bool = False
    state: str | None = None
    set_state: bool = False
    city: str | None = None
    set_city: bool = False

    metadata: dict | None = None
    set_metadata: bool = False


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    is_root: bool
    # Column-level company-profile + address fields.
    about: str | None = None
    industry: str | None = None
    hiring_bar: str | None = None
    website: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    company_profile_completed_at: str | None = None
    company_profile_completion_status: str = "complete"
    metadata: dict | None = None
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool
    is_accessible: bool = True
    admin_emails: list[str] = []
    # Replaces inherited_locale + inherited_compliance. Same shape as the
    # old fields: {"values": {country, state, city}, "source_unit_id"}.
    inherited_address: dict | None = None
    # Populated only when the update flips status pending -> complete.
    # Count of jobs advanced out of blocked_pending_client_setup.
    unblocked_job_count: int = 0


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRole(BaseModel):
    role_id: str
    role_name: str
    assigned_at: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    roles: list[MemberRole]
