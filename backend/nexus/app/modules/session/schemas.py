from enum import StrEnum

from pydantic import BaseModel


class SessionState(StrEnum):
    CREATED = "created"
    WAITING = "waiting"
    PRE_CHECK = "pre_check"
    CONSENT = "consent"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class SessionResponse(BaseModel):
    id: str
    state: SessionState
    job_id: str
    candidate_id: str
    current_question_index: int = 0
    total_questions: int = 0
