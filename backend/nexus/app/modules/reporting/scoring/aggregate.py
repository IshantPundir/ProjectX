"""Deterministic, pure scoring math: signal-state → dimension → knockout gate →
overall → verdict. No LLM, no IO. This is the auditable core; same logs → same number."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD,
    BORDERLINE_CEILING,
    HOLISTIC_ADJ_MAX,
    MIN_COVERAGE_FOR_ADVANCE,
    REJECT_CEILING,
    REJECT_THRESHOLD,
    level_score,
)
from app.modules.reporting.scoring.types import Confidence, DemonstrationLevel, Verdict


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    level: DemonstrationLevel
    score: int           # always 0..100 (every primary is scored, incl. floors)


def make_scored_signal(*, value, type, weight, knockout, priority, level) -> ScoredSignal:
    return ScoredSignal(value=value, type=type, weight=weight, knockout=knockout,
                        priority=priority, level=level, score=level_score(level))


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float
    confidence: Confidence


def confidence_from_coverage(coverage: float) -> Confidence:
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.4:
        return "medium"
    return "low"


def score_dimension(name: str, signals: list[ScoredSignal], types: frozenset[str]) -> DimensionScore:
    members = [s for s in signals if s.type in types]
    total_w = sum(s.weight for s in members)
    if total_w == 0:
        return DimensionScore(name=name, score=None, coverage=0.0, confidence="low")
    weighted = sum(s.weight * s.score for s in members) / total_w
    cov = compute_coverage(members)
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=cov, confidence=confidence_from_coverage(cov))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Weighted mean over ALL primary signals (every one is scored, incl. floors).
    Communication is scored separately. Returns (overall, real-data coverage)."""
    total_w = sum(s.weight for s in signals)
    if total_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in signals) / total_w
    return int(round(weighted)), compute_coverage(signals)


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


_REJECT_LEVELS = frozenset({"absent"})
_UNCONFIRMED_LEVELS = frozenset({"not_reached", "thin"})


def must_have_cap(
    must_haves: list[ScoredSignal], *, coverage: float
) -> int | None:
    """Fit ceiling from must-have status (the gate from spec §6)."""
    if any(s.level in _REJECT_LEVELS for s in must_haves):
        return REJECT_CEILING
    if any(s.level in _UNCONFIRMED_LEVELS for s in must_haves) or coverage < MIN_COVERAGE_FOR_ADVANCE:
        return BORDERLINE_CEILING
    return None



def clamp_to_ceiling(value: int | None, ceiling: int | None) -> int | None:
    """Cap a base score by its fit ceiling. A knockout (REJECT_CEILING) with no
    assessed signals (value None) still resolves to the reject band."""
    if value is None:
        return REJECT_CEILING if ceiling == REJECT_CEILING else None
    return min(value, ceiling) if ceiling is not None else value


def apply_holistic(
    session_score: int | None, delta: int, ceiling: int | None
) -> int | None:
    """Session score + bounded ±HOLISTIC_ADJ_MAX delta, clamped 0..100, then
    re-capped so the adjustment can never break a categorical guarantee."""
    if session_score is None:
        return None
    bounded = max(-HOLISTIC_ADJ_MAX, min(HOLISTIC_ADJ_MAX, delta))
    raw = max(0, min(100, session_score + bounded))
    return min(raw, ceiling) if ceiling is not None else raw


def resolve_verdict(
    *, overall: int | None, coverage: float, must_haves: list[ScoredSignal],
) -> VerdictResult:
    """Score-driven verdict; categorical must-have backstops first.
    The overall is assumed already ceiling-capped by the caller."""
    absent_mh = next((s for s in must_haves if s.level in _REJECT_LEVELS), None)
    if absent_mh is not None:
        return VerdictResult("reject", f"failed must-have: {absent_mh.value}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if any(s.level in _UNCONFIRMED_LEVELS for s in must_haves):
        return VerdictResult("borderline", "a must-have was not confirmed — human review")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")


# Levels that represent REAL data (the screen actually assessed the signal).
# not_reached scores at the floor but is NOT real data → it lowers confidence.
_COVERED_LEVELS: frozenset[str] = frozenset({"strong", "solid", "thin", "absent"})


def compute_coverage(signals: list["ScoredSignal"]) -> float:
    """Real-data fraction = covered weight / total weight. `not_reached` is excluded
    from 'covered' (it scores at the floor but we did not actually assess it)."""
    total_w = sum(s.weight for s in signals)
    if total_w == 0:
        return 0.0
    covered_w = sum(s.weight for s in signals if s.level in _COVERED_LEVELS)
    return covered_w / total_w
