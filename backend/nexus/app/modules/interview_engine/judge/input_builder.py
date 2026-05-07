"""Judge input builder — assembles structured input for the Judge LLM call.

Active-question-only scope: the Judge sees rubric content for the active
question only. Cross-question evidence is captured in transcript and handled
post-session by the Report Builder.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_runtime import (
    QuestionConfig, TranscriptEntry,
)


class ActiveSignalMeta(BaseModel):
    """Per-signal metadata projected from SessionConfig.signal_metadata.

    Surfaces the knockout flag (and priority) for the *active* question's
    signals so the Judge can phrase its `thought` reasoning with the
    right context. Enforcement of knockout policy still happens
    deterministically at the State Engine layer; this is purely
    informational.
    """

    value: str
    knockout: bool
    priority: Literal["required", "preferred"]


class JudgeInputPayload(BaseModel):
    """Structured input passed to the Judge LLM (rendered into prompt JSON)."""

    active_question_id: str | None
    active_question_text: str | None
    active_question_positive_evidence: list[str] = Field(default_factory=list)
    active_question_red_flags: list[str] = Field(default_factory=list)
    active_question_remaining_probes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Remaining probes for the active question — keys are probe_ids "
            "(strings), values are the probe text. Probes that have already "
            "been consumed are NOT in this dict, so the Judge cannot pick a "
            "consumed probe id. Replaces the prior `active_question_follow_ups` "
            "list, which exposed every follow-up indexed from 0 and let the "
            "Judge pick consumed probes (the State Engine then had to self-heal)."
        ),
    )
    active_question_rubric: dict[str, str] = Field(default_factory=dict)
    active_question_evaluation_hint: str | None = None
    active_question_signal_metadata: list[ActiveSignalMeta] = Field(
        default_factory=list,
        description=(
            "Per-signal metadata for the active question's signals — "
            "carries the knockout flag so the Judge can identify which "
            "?->failed observations are session-ending. Enforcement is "
            "still done by the State Engine; this is informational."
        ),
    )

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
    active_signal_metadata: list[ActiveSignalMeta] | None = None,
    active_remaining_probes: dict[str, str] | None = None,
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
        active_question_remaining_probes=dict(active_remaining_probes or {}),
        active_question_rubric=(
            active_question.rubric.model_dump() if active_question else {}
        ),
        active_question_evaluation_hint=(
            active_question.evaluation_hint if active_question else None
        ),
        active_question_signal_metadata=list(active_signal_metadata or []),
        ledger_snapshot=ledger_snapshot,
        queue_snapshot=queue_snapshot,
        claims_snapshot=claims_snapshot,
        recent_turns=list(recent_turns)[-8:],
        candidate_utterance=candidate_utterance,
        time_remaining_seconds=time_remaining_seconds,
    )
