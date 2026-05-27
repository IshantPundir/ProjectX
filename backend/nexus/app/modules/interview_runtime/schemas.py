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

from app.modules.interview_runtime.results import (
    ClaimsPoolSnapshot,
    QuestionQueueSnapshot,
    SignalLedgerSnapshot,
)


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
    question_kind: str = Field(
        default="technical_scenario",
        description=(
            "RELAXED READ PROJECTION (interview-engine-v2 M2, decision D1). The "
            "canonical taxonomy (experience_check | behavioral | technical_scenario "
            "| compliance_binary) is enforced at the WRITE boundary — the "
            "GeneratedQuestion generator model + the stage_questions.question_kind DB "
            "CHECK. This field is intentionally an unconstrained `str` (NOT a union) "
            "during v1 coexistence so the reference-only v1 engine suite + "
            "sample_session_config.json (which read QuestionConfig with the legacy "
            "strings 'behavioral_star'/'technical_depth') stay a TRUE untouched "
            "regression backstop. Tighten to the new Literal at M6 when v1 is retired."
        ),
    )
    primary_signal: str | None = Field(
        default=None,
        description=(
            "The single signal value the lead question opens (the v2 brain's crisp "
            "thread-satisfaction key). Projected from stage_questions.primary_signal; "
            "None for legacy/hand rows. signal_values stays the broader coverable set."
        ),
    )
    difficulty: StageDifficulty = Field(
        default="medium",
        description=(
            "Per-question difficulty. Falls back to the stage difficulty in "
            "build_session_config when the DB column is NULL. Drives the "
            "engine's advance quality-gate, push-back cap, and Speaker tone. "
            "Default 'medium' keeps back-compat for any caller that omits it."
        ),
    )


class CompanyContext(BaseModel):
    """Company profile context for the interview engine.

    Free-text prompt context — no length caps. The fields are dumped
    verbatim into the agent's system prompt and into the JD / question-bank
    prompts via the same ``find_company_profile_in_ancestry`` helper; the
    DB columns are ``TEXT`` and the recruiter-facing edit form imposes no
    length limit, so the engine boundary must accept whatever was stored.
    Non-emptiness is enforced upstream by the activation gate
    (``find_company_profile_in_ancestry`` returns ``None`` if any of
    about/industry/hiring_bar is empty after strip), which is what blocks
    a session from being dispatched against an incomplete profile.

    ``company_stage`` is retained for backward compatibility but defaults
    to empty string — it was dropped from the org_unit profile columns in
    migration 0034.
    """

    about: str
    industry: str
    company_stage: str = ""
    hiring_bar: str


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
    hiring_company_name: str | None = Field(
        default=None,
        description=(
            "The HIRING company (e.g., 'Workato'), NOT the ProjectX tenant "
            "(e.g., 'BinQle' if the tenant is a staffing agency). Populated "
            "from the closest org_unit to the job (depth 0 in ancestry). "
            "Used by the intro_brief Speaker turn."
        ),
    )
    role_summary: str
    jd_text: str | None = Field(
        default=None,
        description=(
            "The enriched JD body (or raw JD if enrichment never ran). "
            "Threaded through so the Speaker has full role context for "
            "answering candidate meta-questions ('Tell me about the role "
            "again', 'What does this job involve?') — see clarify.txt "
            "role_context path. Populated from JobPosting.description_enriched, "
            "falling back to description_raw. Never used to construct the "
            "intro brief (role_summary is the cleaner field there)."
        ),
    )
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
    keyterms: list[str] = Field(
        default_factory=list,
        description=(
            "STT keyterm-prompting list, extracted at bank-generation time "
            "(see question_bank/refine.py:extract_bank_keyterms) and cached "
            "on stage_question_banks.extracted_keyterms. Empty list when the "
            "bank hasn't had keyterm extraction run yet — the engine then "
            "falls back to candidate-name-only boosting. See spec "
            "docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md."
        ),
    )


# ---------------------------------------------------------------------------
# Steering models (agent's real-time observations)
# ---------------------------------------------------------------------------

# DEPRECATED — retained only for legacy `raw_result_json` parsing on
# pre-Phase-7 sessions persisted before the structured-engine cutover.
# The post-Phase-7 engine emits SignalLedger / QuestionQueue / ClaimsPool
# snapshots on `SessionResult` instead. New code MUST NOT construct or
# consume `SteeringObservation`; remove once the legacy rows are
# migrated/expired.
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

# TranscriptEntry was moved to a leaf module in 2026-05-16 to break the
# circular import with engine.models.speaker. Re-exported here so existing
# callers (`from app.modules.interview_runtime.schemas import TranscriptEntry`)
# keep working.
from app.modules.interview_runtime.models import TranscriptEntry  # noqa: F401


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
    signal_ledger: SignalLedgerSnapshot | None = Field(
        default=None,
        description=(
            "v1 structured-engine snapshot (append-only evidence + per-signal coverage). "
            "None for v2 sessions — the v2 brain emits coverage_summary instead. The "
            "report builder reads whichever is present."
        ),
    )
    question_queue: QuestionQueueSnapshot | None = Field(
        default=None, description="v1-only; None for v2."
    )
    claims_pool: ClaimsPoolSnapshot | None = Field(
        default=None, description="v1-only; None for v2."
    )
    coverage_summary: dict[str, str] | None = Field(
        default=None,
        description=(
            "v2-native per-signal final coverage state (signal_value -> "
            "none|partial|sufficient|failed), produced by interview_engine CoverageTracker "
            "at session close. None for v1 sessions (which fill signal_ledger). Richer v2 "
            "per-turn detail lives in the audit envelope via audit_envelope_ref."
        ),
    )
    audit_envelope_ref: str | None = Field(
        default=None,
        description=(
            "Filesystem path (or future blob URI) of the per-session audit "
            "envelope written by event_log/. None if the sink was disabled or "
            "writing failed."
        ),
    )
    push_back_total: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of push_back actions applied across the session, summed "
            "over all questions. Per-question detail lives on "
            "question_queue.questions[i].push_back_count. The Report Builder "
            "uses both: total for session-level signal, per-question for "
            "scoring nuance."
        ),
    )
    cap_forced_advance_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of questions whose advance was forced by hitting the "
            "push_back cap (>=2 push_backs without surfacing a concrete "
            "observation). A high count on a session signals a stalling "
            "candidate; the Report Builder must distinguish 'covered "
            "concretely' from 'cap-forced advance' when grading."
        ),
    )
    quality_distribution: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Total observation counts by quality grade across the entire "
            "session. Keys are 'thin' / 'concrete' / 'strong' (a key may be "
            "absent when the count is zero). The Report Builder uses this "
            "as a session-level density signal — many thin observations + "
            "few concrete ones = weak session even if all questions "
            "advanced."
        ),
    )

