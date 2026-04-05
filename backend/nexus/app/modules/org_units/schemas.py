from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None
    admin_delete_disabled: bool | None = None


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool
    is_accessible: bool = True  # False for ancestor-only units in non-super-admin views
    admin_emails: list[
        str
    ] = []  # Admin emails for inaccessible units (for "ask for access" message)


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
