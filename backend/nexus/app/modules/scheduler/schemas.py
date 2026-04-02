from pydantic import BaseModel


class SessionInvite(BaseModel):
    candidate_email: str
    job_id: str
    scheduled_at: str  # ISO 8601
    otp_required: bool = False


class InviteResponse(BaseModel):
    invite_id: str
    session_url: str
    expires_at: str
