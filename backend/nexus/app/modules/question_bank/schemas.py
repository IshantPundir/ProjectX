from pydantic import BaseModel


class Question(BaseModel):
    id: str
    text: str
    category: str
    is_mandatory: bool = False
    order: int = 0


class QuestionBankResponse(BaseModel):
    job_id: str
    version: int
    questions: list[Question]
