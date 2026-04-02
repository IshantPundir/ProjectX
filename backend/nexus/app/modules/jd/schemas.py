from pydantic import BaseModel


class JobDescriptionCreate(BaseModel):
    title: str
    description: str
    requirements: list[str] = []
    tenant_id: str


class JobDescriptionResponse(BaseModel):
    id: str
    title: str
    description: str
    requirements: list[str]
    tenant_id: str
