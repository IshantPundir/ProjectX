from pydantic import BaseModel

from app.modules.analysis.schemas import SignalScore


class QuestionScorecard(BaseModel):
    question_index: int
    question_text: str
    answer_summary: str
    signals: list[SignalScore]
    overall_score: float


class SessionReport(BaseModel):
    session_id: str
    candidate_id: str
    job_id: str
    overall_score: float
    classification: str  # "advance" | "borderline" | "reject"
    scorecards: list[QuestionScorecard]
    summary: str
