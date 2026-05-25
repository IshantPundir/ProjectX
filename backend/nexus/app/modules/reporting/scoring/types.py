"""Frozen value objects + Literals shared across the scoring pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BarsLevel = Literal["below_bar", "meets_bar", "excellent"]
Opportunity = Literal["full", "partial", "none"]
SignalState = Literal["excellent", "meets_bar", "below_bar", "not_assessed"]
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
