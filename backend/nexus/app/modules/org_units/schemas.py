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
