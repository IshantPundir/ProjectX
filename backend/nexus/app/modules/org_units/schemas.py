from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None
    company_profile: dict | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None
    admin_delete_disabled: bool | None = None
    company_profile: dict | None = None
    set_company_profile: bool = False


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    is_root: bool
    company_profile: dict | None
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool
    is_accessible: bool = True
    admin_emails: list[str] = []


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
