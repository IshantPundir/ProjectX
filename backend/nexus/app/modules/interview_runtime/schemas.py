"""Pydantic schemas exchanged between Nexus and the interview engine.

These models define the wire contract. The interview engine imports them
via the path-dep on the nexus package — `from app.modules.interview_runtime.schemas import ...`.

Lifted from `backend/interview_engine/models.py` with two intentional changes:
1. `CandidateContext.email` is removed — engine never receives PII (CLAUDE.md
   "no raw PII in logs"; spec Section 6.5).
2. `StageType` reflects the v5 stage-type set (post-migration 0016), not the
   stale set in the engine source. The runtime allowlist (ai_screening +
   phone_screen) is enforced separately in build_session_config.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Type aliases (Literals so they cross the wire as plain strings)
# ---------------------------------------------------------------------------

StageType = Literal[
    "intake",
    "phone_screen",
    "ai_screening",
    "human_interview",
    "debrief",
    "take_home",
]

StageDifficulty = Literal["easy", "medium", "hard"]

AdvanceBehavior = Literal["auto_advance", "manual_review"]


# ---------------------------------------------------------------------------
# Input models (Nexus -> interview engine)
# ---------------------------------------------------------------------------

class QuestionRubric(BaseModel):
    """Mirrors question_bank/schemas.py QuestionRubric."""

    excellent: str
    meets_bar: str
    below_bar: str


class QuestionConfig(BaseModel):
    """Mirrors question_bank/schemas.py GeneratedQuestion + adds id.

    ``id`` is a UUID-as-string assigned by Nexus when the question bank
    is persisted.  The interview engine uses it to key transcript entries
    and observations back to specific questions.
    """

    id: str
    position: int = Field(ge=0)
    text: str = Field(min_length=10, max_length=500)
    signal_values: list[str] = Field(min_length=1, max_length=3)
    estimated_minutes: float = Field(gt=0, le=15)
    is_mandatory: bool
    follow_ups: list[str] = Field(min_length=0, max_length=3)
    positive_evidence: list[str] = Field(min_length=3, max_length=5)
    red_flags: list[str] = Field(min_length=2, max_length=3)
    rubric: QuestionRubric
    evaluation_hint: str = Field(min_length=10, max_length=200)


class CompanyContext(BaseModel):
    """Mirrors org_units/company_profile.py CompanyProfile.

    Uses plain ``str`` for industry/company_stage instead of Nexus enums
    so the wire format stays decoupled from Nexus enum definitions.
    """

    about: str = Field(min_length=30, max_length=500)
    industry: str
    company_stage: str
    hiring_bar: str = Field(min_length=20, max_length=280)


class CandidateContext(BaseModel):
    """Minimal candidate info the agent needs during the session.

    Email and any other PII are intentionally omitted — the engine never
    receives them. The agent's prompt only personalizes by ``name``.
    """

    name: str


class StageConfig(BaseModel):
    """Interview stage configuration pushed from Nexus."""

    stage_id: str
    stage_type: StageType
    name: str
    duration_minutes: int = Field(gt=0)
    difficulty: StageDifficulty
    questions: list[QuestionConfig]
    advance_behavior: AdvanceBehavior = "manual_review"


class SessionConfig(BaseModel):
    """The full input contract sent from Nexus to the interview engine.

    In standalone mode this is loaded from a fixture JSON file.
    In integration mode this arrives via /api/internal/sessions/{id}/config.
    """

    session_id: str
    job_title: str
    role_summary: str
    seniority_level: str
    company: CompanyContext
    candidate: CandidateContext
    stage: StageConfig
    signals: list[str] = Field(
        default_factory=list,
        description="Top-level signal dimensions the evaluator cares about.",
    )


# ---------------------------------------------------------------------------
# Steering models (agent's real-time observations)
# ---------------------------------------------------------------------------

class SteeringObservation(BaseModel):
    """What the LLM reports after each candidate answer.

    These are *steering* signals used by the state machine to decide
    whether to probe deeper or advance to the next question.  They are
    NOT evaluation scores — scoring happens post-session in the Nexus
    analysis pipeline.
    """

    answer_summary: str = Field(
        description="2-3 sentence summary of what the candidate said.",
    )
    signals_demonstrated: list[str] = Field(
        default_factory=list,
        description="signal_values from QuestionConfig evidenced in this answer.",
    )
    wants_to_probe: bool = Field(
        description="LLM's recommendation to probe deeper (state machine validates).",
    )
    candidate_disengaged: bool = Field(
        default=False,
        description=(
            "True when the candidate explicitly says they want to stop, "
            "leave, or refuse to continue (e.g. 'I'm done', 'I don't want "
            "to answer any more'). NOT set for 'I don't know' — that's just "
            "a weak answer, not disengagement."
        ),
    )
    notes: str = Field(
        default="",
        description="Free-form notes for the post-session evaluator.",
    )


# ---------------------------------------------------------------------------
# Output models (interview engine -> /api/internal/sessions/{id}/results)
# ---------------------------------------------------------------------------

class TranscriptEntry(BaseModel):
    """A single utterance in the interview transcript."""

    role: Literal["agent", "candidate"]
    text: str
    timestamp_ms: int = Field(
        ge=0,
        description="Milliseconds since session start.",
    )
    question_id: str | None = None


class QuestionResult(BaseModel):
    """Outcome of a single question within the session."""

    question_id: str
    question_text: str
    position: int
    is_mandatory: bool
    was_skipped: bool
    probes_fired: int = Field(ge=0)
    observations: list[SteeringObservation]
    transcript_entries: list[TranscriptEntry]


class SessionResult(BaseModel):
    """Complete output of an interview session.

    Posted to /api/internal/sessions/{id}/results by the engine on close.
    """

    session_id: str
    job_title: str
    stage_id: str
    stage_type: str
    candidate_name: str
    duration_seconds: float = Field(ge=0)
    questions_asked: int = Field(ge=0)
    questions_skipped: int = Field(ge=0)
    total_probes_fired: int = Field(ge=0)
    question_results: list[QuestionResult]
    full_transcript: list[TranscriptEntry]
    completed_at: str = Field(
        description="ISO 8601 timestamp of session completion.",
    )
