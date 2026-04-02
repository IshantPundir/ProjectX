from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    COMPANY_ADMIN = "Company Admin"
    RECRUITER = "Recruiter"
    HIRING_MANAGER = "Hiring Manager"
    INTERVIEWER = "Interviewer"
    OBSERVER = "Observer"


class TokenPayload(BaseModel):
    """Decoded JWT payload — provider-agnostic."""
    sub: str                  # user ID
    tenant_id: str            # company / tenant UUID
    role: str                 # one of Role values
    email: str = ""
    exp: int = 0              # expiry timestamp
    iat: int = 0              # issued-at timestamp


class CandidateTokenPayload(BaseModel):
    """Decoded candidate session JWT."""
    sub: str                  # candidate session ID
    session_id: str
    tenant_id: str
    exp: int = 0
    iat: int = 0
