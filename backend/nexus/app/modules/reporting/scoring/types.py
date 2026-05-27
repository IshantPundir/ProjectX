"""Frozen value objects + Literals shared across the scoring pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

KnockoutStatus = Literal["passed", "failed", "insufficient"]
Verdict = Literal["advance", "borderline", "reject"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class ScoredUnit:
    """One delivered question + the candidate's answer to it."""
    question_id: str
    question_text: str
    candidate_answer: str
    answer_start_ms: int
    probes_fired: int
    clarifies: int
    word_count: int
    candidate_engaged: bool      # triage kind ∈ answering (not no_experience/off_topic/backchannel)
    # bank question_kind (e.g. "experience_check"); None for legacy units without the field
    question_kind: str | None = None


@dataclass(frozen=True)
class SignalDef:
    value: str
    type: str                    # experience | competency | behavioral | credential
    weight: int                  # 1..3
    knockout: bool
    priority: str                # required | preferred


@dataclass(frozen=True)
class Evidence:
    quote: str
    timestamp_ms: int
    question_id: str
    grounded: bool = True


# ---------------------------------------------------------------------------
# New coverage-state scoring vocabulary (additive — Task 2)
# ---------------------------------------------------------------------------

# Engine coverage states + the LLM-only `exceeded` headroom state.
CovState = Literal["exceeded", "sufficient", "partial", "failed", "none"]
GradeTexture = Literal["concrete", "thin", "null"]
CommLevel = Literal["weak", "adequate", "strong"]
StatusBadge = Literal[
    "passed", "partial", "failed_required",
    "not_demonstrated", "not_attempted", "not_fully_assessed",
]


@dataclass(frozen=True)
class SignalTurn:
    """One turn that touched a signal (from the audit envelope)."""
    candidate_quote: str
    grade: str | None            # concrete | thin | null
    reasoning: str
    question_id: str | None
