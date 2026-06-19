from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.modules.reel import ReelPlayback
from app.modules.reporting.scoring.types import Confidence, Verdict
from app.modules.session import RecordingPlayback
from app.modules.vision import ProctoringAnalysisRead


class QuestionGradeOut(BaseModel):
    """Structured output from the per-QUESTION post-interview grade (Layer 2).
    The question is graded against its OWN full bank card (rubric + listen-for
    + red-flags + evaluation_hint), difficulty-calibrated and probe-aware."""
    level: Literal["strong", "solid", "thin", "absent"]
    listen_for_hits: list[str] = Field(default_factory=list)
    red_flags_tripped: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    needs_verification: bool = False
    verification_note: str | None = None
    overridden: bool = False
    override_reason: str | None = None


class CommunicationVerdict(BaseModel):
    """Strict output from the communication judge (content-level)."""

    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str
    level: Literal["weak", "adequate", "strong"]


class HolisticAdjustmentOut(BaseModel):
    """Layer-2.5 cross-signal gestalt adjustment to the deterministic session score.
    Bounded; cannot override a categorical guarantee (re-capped after the fact)."""
    evidence_quotes: list[str] = Field(default_factory=list)
    justification: str = ""
    delta: int = 0  # raw model output; hard-bounded to ±HOLISTIC_ADJ_MAX downstream


class ScoringManifest(BaseModel):
    scorer_model: str | None = None
    reasoning_effort: str | None = None
    prompt_version: str | None = None
    prompt_cache_key: str | None = None
    scorer_code_version: str | None = None
    bank_id: str | None = None
    signal_snapshot_id: str | None = None
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
    overall_score: float | None = None


class ReportIndexPage(BaseModel):
    items: list[ReportIndexItem]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Narrative sub-models (prose-only LLM output — no scores/verdict)
# ---------------------------------------------------------------------------


class WhyColumn(BaseModel):
    title: str
    body: str


class DecisionOut(BaseModel):
    headline: str
    why_positive: WhyColumn
    why_negative: WhyColumn


class StrengthOut(BaseModel):
    title: str
    detail: str


class ConcernOut(BaseModel):
    title: str
    detail: str
    severity: Literal["deal_breaker", "major", "moderate"]


class QuestionNarrative(BaseModel):
    question_id: str
    candidate_quote: str          # cleaned, readable; meaning preserved
    our_read: str


class MethodologyOut(BaseModel):
    note: str
    charity_flags: list[str] = Field(default_factory=list)


class NarrativeOut(BaseModel):
    """Prose-only LLM output. Contains NO scores/verdict."""
    decision: DecisionOut
    quick_summary: str
    strengths: list[StrengthOut] = Field(default_factory=list)
    concerns: list[ConcernOut] = Field(default_factory=list)
    questions: list[QuestionNarrative] = Field(default_factory=list)
    methodology: MethodologyOut


# ---------------------------------------------------------------------------
# PDF-shaped report output models (Task 8)
# ---------------------------------------------------------------------------


class ScoreOut(BaseModel):
    score: float | None
    tier_label: str
    tone: str                      # ok | caution | danger | neutral
    confidence: Confidence
    coverage: float = 0.0
    session_score: float | None = None   # pre-adjustment deterministic base (overall only)
    holistic_delta: float | None = None  # bounded ±5 delta applied (overall only)


class QuestionOut(BaseModel):
    seq: int
    question_id: str
    title: str
    status_badge: str
    status_tone: str
    question_text: str
    candidate_quote: str
    our_read: str = ""
    asked_at_ms: int | None = None       # ms since session start (None for legacy sessions)
    thumbnail_url: str | None = None     # presigned R2 GET, attached at read time only
    level: str = "not_reached"            # per-question grade: strong|solid|thin|absent|not_reached
    closure: str | None = None            # engine per-question verdict: satisfied|tapped_out|absent|truncated (None if never asked)
    difficulty: str | None = None         # easy|medium|hard (bank)
    listen_for_hits: list[str] = Field(default_factory=list)
    red_flags_tripped: list[str] = Field(default_factory=list)
    probes_used: int = 0
    probes_available: int = 0


class SignalAssessmentOut(BaseModel):
    signal: str
    type: str
    weight: int
    knockout: bool
    priority: str
    provenance: Literal["not_reached", "asked_directly", "cross_credited", "probed_absent"]
    level: Literal["strong", "solid", "thin", "absent", "not_reached"]
    score: float | None = None
    evidence: list[str] = Field(default_factory=list)
    overridden: bool = False
    override_reason: str | None = None
    cross_credit_applied: bool = False
    level_basis: str = ""                 # e.g. "dedicated: thin; +1 cross-credit → solid"


class ReportRead(BaseModel):
    """Recruiter-facing report (PDF-shaped). Mirrors session_reports JSONB columns."""
    verdict: Verdict
    verdict_reason: str
    overall_score: float | None
    overall_coverage: float
    overall_confidence: Confidence
    decision: DecisionOut
    scores: dict[str, ScoreOut]                       # overall|technical|behavioral|communication
    quick_summary: str = ""
    strengths: list[StrengthOut] = Field(default_factory=list)
    concerns: list[ConcernOut] = Field(default_factory=list)
    questions: list[QuestionOut] = Field(default_factory=list)
    methodology: MethodologyOut
    signal_assessments: list[SignalAssessmentOut] = Field(default_factory=list)
    id: str | None = None
    session_id: str | None = None
    status: str = "ready"
    engine_version: str | None = None
    version: int = 1
    scoring_manifest: ScoringManifest | None = None
    human_decision: dict | None = None
    generated_at: str | None = None
    reference_photo_url: str | None = None  # presigned R2 GET, attached at read time


class ShareReportIn(BaseModel):
    recipient_email: EmailStr


class ShareReportOut(BaseModel):
    share_id: str
    status: str


class PublicRecordingsEnvelope(BaseModel):
    """Everything the public /recordings/<token> page needs, in one payload."""
    candidate_name: str
    job_title: str
    stage_label: str
    report: ReportRead
    recording: RecordingPlayback
    proctoring: ProctoringAnalysisRead
    reel: ReelPlayback
