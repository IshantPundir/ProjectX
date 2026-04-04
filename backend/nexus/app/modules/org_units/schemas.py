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
    created_at: str


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None


class AssignUserRequest(BaseModel):
    user_id: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str
    is_admin: bool
    assignment_type: str  # "primary" (from users.org_unit_id) or "assigned" (from junction)
    assigned_at: str
