"""Interview engine data models.

Input models (SessionConfig) mirror Nexus schemas for wire-compatibility.
The interview engine does NOT import from Nexus — these are independent
definitions that must stay in sync with their Nexus counterparts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SessionState(StrEnum):
    """Mirrors session/schemas.py SessionState."""

    CREATED = "created"
    WAITING = "waiting"
    PRE_CHECK = "pre_check"
    CONSENT = "consent"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Type aliases (kept as Literals for zero-import wire compat)
# ---------------------------------------------------------------------------

StageType = Literal[
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
]

StageDifficulty = Literal["easy", "medium", "hard"]

AdvanceBehavior = Literal["auto_advance", "manual_review"]


# ---------------------------------------------------------------------------
# Input models (from Nexus -> interview engine)
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
    so the interview engine stays decoupled from Nexus enum definitions.
    """

    about: str = Field(min_length=30, max_length=500)
    industry: str
    company_stage: str
    hiring_bar: str = Field(min_length=20, max_length=280)


class CandidateContext(BaseModel):
    """Minimal candidate info the agent needs during the session."""

    name: str
    email: str | None = None


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
    In integration mode (Phase 3B) it arrives via LiveKit room metadata
    or a Nexus API call.
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
# Output models (interview engine -> results)
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

    Written to ``results/`` in standalone mode.
    Pushed to Nexus API in integration mode (Phase 3B).
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
