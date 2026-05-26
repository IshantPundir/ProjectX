from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.reporting.scoring.types import (
    BarsLevel,
    Confidence,
    KnockoutStatus,
    Opportunity,
    SignalState,
    Verdict,
)


class JudgeVerdict(BaseModel):
    """Strict structured output from the per-answer judge. Evidence BEFORE score."""

    evidence_quotes: list[str] = Field(
        default_factory=list,
        description="Verbatim spans copied from the transcript that justify the level.",
    )
    red_flags_hit: list[str] = Field(default_factory=list)
    justification: str = Field(
        description="Map the cited evidence to the rubric anchor."
    )
    level: BarsLevel


class CommunicationVerdict(BaseModel):
    """Strict output from the communication judge (content-level)."""

    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str
    level: Literal["weak", "adequate", "strong"]


class EvidenceOut(BaseModel):
    quote: str
    timestamp_ms: int
    question_id: str
    grounded: bool = True


class AnswerRating(BaseModel):
    """Judge result for one delivered question, post-grounding."""

    question_id: str
    level: BarsLevel
    evidence_quotes: list[str] = Field(default_factory=list)
    red_flags_hit: list[str] = Field(default_factory=list)
    justification: str = ""
    grounded: bool = True


class SignalScorecard(BaseModel):
    value: str
    type: str
    weight: int
    knockout: bool
    state: SignalState
    score: int | None
    opportunity: Opportunity | None = None
    evidence: list[EvidenceOut] = Field(default_factory=list)
    covered_by: list[str] = Field(default_factory=list)


class DimensionScoreOut(BaseModel):
    name: str
    score: int | None
    coverage: float
    confidence: Confidence
    note: str | None = None


class KnockoutResultOut(BaseModel):
    signal: str
    status: KnockoutStatus
    reason: str
    evidence: list[EvidenceOut] = Field(default_factory=list)


class QuestionScorecard(BaseModel):
    question_id: str
    question_text: str
    level: BarsLevel | Literal["not_assessed"]
    evidence: list[EvidenceOut] = Field(default_factory=list)
    red_flags_hit: list[str] = Field(default_factory=list)
    probes_fired: int = 0
    opportunity: Opportunity | None = None


class SummaryOut(BaseModel):
    headline: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    rationale: str = ""


class ScoringManifest(BaseModel):
    scorer_model: str | None = None
    reasoning_effort: str | None = None
    verbosity: str | None = None
    prompt_version: str | None = None
    prompt_cache_key: str | None = None
    scorer_code_version: str | None = None
    bank_id: str | None = None
    signal_snapshot_id: str | None = None
    n_samples: int | None = None
    cache_hit_rate: float | None = None
    evidence_grounding_summary: dict | None = None
    generated_at: str | None = None
    correlation_id: str | None = None


class HumanDecisionIn(BaseModel):
    decision: Literal["advance", "reject", "hold"]
    rationale: str


class ReportIndexItem(BaseModel):
    """One row in the /reports hub: a completed session + its report status."""
    session_id: str
    candidate_id: str | None = None
    candidate_name: str | None = None
    job_title: str | None = None
    stage_name: str | None = None
    completed_at: str | None = None
    report_status: str  # none | pending | generating | ready | failed
    verdict: Verdict | None = None
    overall_score: int | None = None


class ReportIndexPage(BaseModel):
    items: list[ReportIndexItem]
    total: int
    offset: int
    limit: int


class ReportRead(BaseModel):
    """Recruiter-facing report serialization (mirrors session_reports columns)."""

    # required core (constructed in the test):
    verdict: Verdict
    verdict_reason: str
    overall_score: int | None
    overall_coverage: float
    overall_confidence: Confidence
    dimension_scores: dict[str, DimensionScoreOut]
    knockout_results: list[KnockoutResultOut]
    signal_scorecards: list[SignalScorecard]
    question_scorecards: list[QuestionScorecard]
    summary: SummaryOut
    # optional metadata (defaults so the test construction works):
    id: str | None = None
    session_id: str | None = None
    status: str = "ready"
    engine_version: str | None = None
    version: int = 1
    scoring_manifest: ScoringManifest | None = None
    human_decision: dict | None = None
    generated_at: str | None = None
