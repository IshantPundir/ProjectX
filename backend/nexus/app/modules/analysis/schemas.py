from pydantic import BaseModel


class SignalScore(BaseModel):
    dimension: str  # depth | specificity | evidence_quality
    score: float
    confidence: float


class AnswerAnalysis(BaseModel):
    session_id: str
    question_index: int
    signals: list[SignalScore]
    suggested_probe: str | None = None
