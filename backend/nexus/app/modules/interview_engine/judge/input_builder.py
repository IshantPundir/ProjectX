"""Judge input builder — assembles structured input for the Judge LLM call.

Active-question-only scope: the Judge sees rubric content for the active
question only. Cross-question evidence is captured in transcript and handled
post-session by the Report Builder.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_runtime import (
    QuestionConfig, TranscriptEntry,
)


class JudgeInputPayload(BaseModel):
    """Structured input passed to the Judge LLM (rendered into prompt JSON)."""

    active_question_id: str | None
    active_question_text: str | None
    active_question_positive_evidence: list[str] = Field(default_factory=list)
    active_question_red_flags: list[str] = Field(default_factory=list)
    active_question_follow_ups: list[str] = Field(default_factory=list)
    active_question_rubric: dict[str, str] = Field(default_factory=dict)
    active_question_evaluation_hint: str | None = None

    ledger_snapshot: SignalLedgerSnapshot
    queue_snapshot: QuestionQueueSnapshot
    claims_snapshot: ClaimsPoolSnapshot

    recent_turns: list[TranscriptEntry] = Field(default_factory=list, max_length=8)
    candidate_utterance: str
    time_remaining_seconds: int


def build_judge_input(
    *,
    active_question: QuestionConfig | None,
    ledger_snapshot: SignalLedgerSnapshot,
    queue_snapshot: QuestionQueueSnapshot,
    claims_snapshot: ClaimsPoolSnapshot,
    recent_turns: list[TranscriptEntry],
    candidate_utterance: str,
    time_remaining_seconds: int,
) -> JudgeInputPayload:
    return JudgeInputPayload(
        active_question_id=active_question.id if active_question else None,
        active_question_text=active_question.text if active_question else None,
        active_question_positive_evidence=(
            list(active_question.positive_evidence) if active_question else []
        ),
        active_question_red_flags=(
            list(active_question.red_flags) if active_question else []
        ),
        active_question_follow_ups=(
            list(active_question.follow_ups) if active_question else []
        ),
        active_question_rubric=(
            active_question.rubric.model_dump() if active_question else {}
        ),
        active_question_evaluation_hint=(
            active_question.evaluation_hint if active_question else None
        ),
        ledger_snapshot=ledger_snapshot,
        queue_snapshot=queue_snapshot,
        claims_snapshot=claims_snapshot,
        recent_turns=list(recent_turns)[-8:],
        candidate_utterance=candidate_utterance,
        time_remaining_seconds=time_remaining_seconds,
    )
