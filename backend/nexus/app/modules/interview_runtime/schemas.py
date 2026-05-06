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

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    question_kind: Literal[
        "technical_depth",
        "behavioral_star",
        "compliance_binary",
        "open_culture",
    ] = "technical_depth"


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


class SignalMetadata(BaseModel):
    """Per-signal metadata projected from the latest confirmed signal snapshot.

    Travels into the engine alongside ``SessionConfig.signals`` (the
    flat list of value strings). Reserved for use by future evaluators
    or agents that need structured signal info (weight / knockout /
    priority / stage) beyond the flat value list — the current generic
    chatbot does not consume it.

    The Literal values here must mirror ``app/modules/jd/schemas.py``
    exactly so the round-trip through ``snapshot.signals`` (JSONB) is
    validated on every session start. Mismatch → ValidationError at
    ``build_session_config`` — preferable to a silent drop.

    Provenance fields (``source`` / ``inference_basis``) are deliberately
    omitted: they are recruiter-facing signal-editing concerns, not agent
    decision inputs, and the wire format keeps PII / authorship metadata
    off the engine path by default.
    """

    value: str = Field(min_length=1)
    type: Literal["competency", "experience", "credential", "behavioral"]
    priority: Literal["required", "preferred"]
    weight: Literal[1, 2, 3]
    knockout: bool
    stage: Literal["screen", "interview"]
    evaluation_method: Literal[
        "verbal_response",
        "code_exercise",
        "scenario_walkthrough",
        "credential_verify",
        "behavioral_question",
    ]
    evaluation_hint: str | None = None


class SessionConfig(BaseModel):
    """The full input contract sent from Nexus to the interview engine.

    Constructed in-process by ``build_session_config`` and consumed
    directly by the engine entrypoint.
    """

    session_id: str
    job_id: str = Field(
        min_length=1,
        description=(
            "UUID-as-string of the source JobPosting. Required by the "
            "structured agent's InterviewState identity fields (state.py "
            "wire-format invariant). Populated by build_session_config."
        ),
    )
    candidate_id: str = Field(
        min_length=1,
        description=(
            "UUID-as-string of the candidate. Required by the structured "
            "agent's InterviewState identity fields (state.py wire-format "
            "invariant). Populated by build_session_config."
        ),
    )
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
    signal_metadata: list[SignalMetadata] = Field(
        default_factory=list,
        description=(
            "Per-signal metadata (weight, knockout, priority, stage, "
            "evaluation method) projected from the latest confirmed "
            "signal snapshot. One entry per ``signals`` value (same order). "
            "Empty list is the additive default — pre-rebuild engines "
            "ignore it; the structured agent (Phase B+) reads it."
        ),
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
# Output models (interview engine -> record_session_result, in-process)
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


# ---------------------------------------------------------------------------
# Knockout failure (Phase 5) — persisted summary of a hard-requirement
# failure surfaced by the engine's `disqualify_knockout` shared tool.
#
# Defense-in-depth PII boundary: the LLM prompt instructs the agent never
# to include PII in `knockout_reason`; this validator runs `_scrub_pii` on
# every construction path (including model_validate from a DB read) as
# a backstop. RLS on the `sessions` table enforces tenant isolation at
# the storage layer. Three layers; PII has to fail through all three.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b")


def _scrub_pii(text: str) -> str:
    """Replace email + phone-number matches with `[redacted]`.

    Idempotent. Runs unconditionally on every KnockoutFailure
    construction (validator mode='before') including model_validate
    from a DB read.
    """
    text = _EMAIL_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    return text


class KnockoutFailure(BaseModel):
    """Persisted record of a knockout failure (Phase 5).

    Authored by the engine's `disqualify_knockout` shared tool when a
    candidate self-discloses something that invalidates a hard
    requirement (e.g. "I cannot work UK shift hours"). Engine records,
    never auto-rejects — Phase 3D analytics consumes this list.

    `reason` is LLM-authored 1-3 sentence factual summary; the
    `_scrub_reason` validator strips emails + phone numbers as
    defense-in-depth.
    """

    question_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    signal_values: list[str] = Field(min_length=1)
    occurred_at_ms: int = Field(ge=0)

    @field_validator("reason", mode="before")
    @classmethod
    def _scrub_reason(cls, v: object) -> object:
        if not isinstance(v, str):
            # Let the str-coercion / min_length validator produce the
            # right ValidationError downstream. Don't shadow it here.
            return v
        return _scrub_pii(v)


class SessionResult(BaseModel):
    """Complete output of an interview session.

    Passed in-process to ``record_session_result`` by the engine on close.
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
    knockout_failures: list[KnockoutFailure] = Field(
        default_factory=list,
        description=(
            "Hard-requirement failures recorded during the interview "
            "(self-disclosed, factual). Engine records, never auto-rejects "
            "— Phase 3D analytics consumes this list."
        ),
    )
    audio_tuning_summary: dict[str, object] | None = Field(
        default=None,
        description=(
            "Per-session pause/interruption/latency snapshot computed by the "
            "engine at session close. Persisted to sessions.audio_tuning_summary "
            "for empirical-tuning analysis. None when the engine couldn't "
            "compute a summary (e.g. session aborted before any audio events)."
        ),
    )
