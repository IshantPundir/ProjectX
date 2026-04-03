from pydantic import BaseModel


class ProvisionClientRequest(BaseModel):
    company_name: str
    admin_email: str
    domain: str = ""
    industry: str = ""
    plan: str = "trial"


class ProvisionClientResponse(BaseModel):
    company_id: str
    invite_id: str
    admin_email: str
    invite_url: str  # Only present in dry-run mode; empty in production


class ClientListItem(BaseModel):
    company_id: str
    company_name: str
    domain: str | None
    plan: str
    onboarding_complete: bool
    admin_email: str | None
    invite_status: str | None
    created_at: str
