from pydantic import BaseModel


class RoleResponse(BaseModel):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
