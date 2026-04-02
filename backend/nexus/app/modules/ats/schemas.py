from pydantic import BaseModel


class Job(BaseModel):
    external_id: str
    title: str
    tenant_id: str


class Candidate(BaseModel):
    external_id: str
    name: str
    email: str
    tenant_id: str


class InterviewOutcome(BaseModel):
    session_id: str
    candidate_id: str
    status: str  # "advanced" | "rejected" | "borderline"
    tenant_id: str
