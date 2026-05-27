"""Engine-result snapshot models — the neutral, self-contained home for the
SignalLedger / QuestionQueue / ClaimsPool snapshots.

These live in interview_runtime (the engine↔nexus wire contract) so they
have no dependency on the interview_engine package. Pure pydantic — no
imports from interview_engine. The engine's live coverage model is
interview_engine/coverage.py; the snapshots here are the optional
structured shapes the report builder may consume, kept optional on
SessionResult (the engine emits `coverage_summary` for the per-signal
final state, and these remain populated for any historical rows that
carried them).
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


# --- ledger ---------------------------------------------------------------
class CoverageState(StrEnum):
    none = "none"
    partial = "partial"
    sufficient = "sufficient"
    failed = "failed"  # terminal — set on no-experience or knockout disclosure
    # No "strong" state. Answer-quality grading lives in the post-session Report Builder.


class LedgerEntry(BaseModel):
    seq: int = Field(ge=1)
    turn_id: str
    signal_value: str
    anchor_id: int = Field(
        ge=-1,
        description="Index into positive_evidence list. -1 sentinel for failure entries.",
    )
    evidence_quote: str = Field(min_length=1, max_length=500)
    coverage_before: CoverageState
    coverage_after: CoverageState
    recorded_at_ms: int = Field(ge=0)


class SignalSnapshot(BaseModel):
    signal_value: str
    coverage: CoverageState
    anchors_hit: list[int] = Field(default_factory=list)
    last_observation_seq: int | None = None


class SignalLedgerSnapshot(BaseModel):
    entries: list[LedgerEntry] = Field(default_factory=list)
    snapshots: dict[str, SignalSnapshot] = Field(default_factory=dict)
    next_seq: int = Field(ge=1, default=1)


# --- queue ----------------------------------------------------------------
class QuestionStatus(StrEnum):
    pending = "pending"
    active = "active"
    completed = "completed"
    skipped = "skipped"  # only legal for non-mandatory questions


class QuestionState(BaseModel):
    question_id: str
    position: int = Field(ge=0)
    is_mandatory: bool
    status: QuestionStatus
    main_asked_at_turn: int | None = None
    probes_asked_ids: list[str] = Field(default_factory=list)
    probes_remaining_ids: list[str] = Field(default_factory=list)
    anchors_hit_ids: list[int] = Field(default_factory=list)
    time_spent_ms: int = Field(ge=0, default=0)
    turn_count: int = Field(ge=0, default=0)
    push_back_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Number of push_back actions the State Engine has applied to "
            "this question. Hard-capped per difficulty (easy 1 / medium 2 "
            "/ hard 3): the engine downgrades an over-cap push_back to "
            "advance to prevent loops on candidates who genuinely cannot "
            "give specifics. Surfaced to the Judge via "
            "JudgeInputPayload.active_question_push_back_count."
        ),
    )
    still_confused_count: int = Field(
        ge=0,
        default=0,
        description=(
            "Consecutive turns on this question where the Judge set "
            "turn_metadata.candidate_still_confused=true (generic confusion "
            "/ cannot engage). Reset to 0 on any other turn or on advance. "
            "The State Engine escalates to acknowledge-and-advance once this "
            "reaches 2 (i.e. on the 3rd consecutive confusion). Surfaced to "
            "the Judge via JudgeInputPayload.active_question_still_confused_count."
        ),
    )
    quality_observations: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Cumulative count of observations on this question by quality "
            "grade (`thin` / `concrete` / `strong`). Used by the State "
            "Engine to gate `advance` (must have at least one >= concrete) "
            "and surfaced to the Report Builder for downstream analytics."
        ),
    )
    signal_values: list[str] = Field(
        default_factory=list,
        description=(
            "Signal values this question targets. Used by the non-mandatory "
            "question selector (Cluster G) to check whether any of the "
            "question's signals still have uncovered coverage, gating "
            "whether the non-mandatory question should be asked."
        ),
    )


class QuestionQueueSnapshot(BaseModel):
    questions: list[QuestionState] = Field(default_factory=list)
    active_index: int | None = None  # None before first question is delivered


# --- claims ---------------------------------------------------------------
class ClaimEntry(BaseModel):
    """Canonical ClaimEntry shape with capture metadata.

    The Judge emits a narrower shape (no captured_at_*) in models.judge.ClaimEntry;
    the State Engine canonicalizes to this shape when ingesting.
    """

    claim_topic: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=200)
    source_quote: str = Field(min_length=1, max_length=500)
    captured_at_turn: int = Field(ge=0)
    captured_at_seq: int = Field(ge=1)


class ClaimsPoolSnapshot(BaseModel):
    entries: list[ClaimEntry] = Field(default_factory=list)


__all__ = [
    "CoverageState", "LedgerEntry", "SignalSnapshot", "SignalLedgerSnapshot",
    "QuestionStatus", "QuestionState", "QuestionQueueSnapshot",
    "ClaimEntry", "ClaimsPoolSnapshot",
]
