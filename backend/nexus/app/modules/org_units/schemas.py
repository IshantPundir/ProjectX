from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    created_at: str


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None


class AssignUserRequest(BaseModel):
    user_id: str
    role: str | None = None          # role within this unit
    is_admin: bool = False            # admin of this unit


class UpdateMemberRequest(BaseModel):
    role: str | None = None
    is_admin: bool | None = None
    permissions: list[str] | None = None


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str | None                  # role within THIS unit (from assignment)
    is_admin: bool                    # admin of THIS unit
    permissions: list[str]            # permissions within THIS unit
    assignment_type: str              # "primary" or "assigned"
    assigned_at: str
