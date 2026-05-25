"""Deterministic, pure scoring math: signal → dimension → knockout gate → overall → verdict.
No LLM, no IO. This is the auditable core; everything here is unit-tested exhaustively."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD,
    LEVEL_POINTS,
    MIN_COVERAGE_FOR_ADVANCE,
    REJECT_THRESHOLD,
)
from app.modules.reporting.scoring.types import (
    BarsLevel,
    Confidence,
    KnockoutStatus,
    Opportunity,
    SignalState,
    Verdict,
)

_RANK = {"below_bar": 0, "meets_bar": 1, "excellent": 2}


@dataclass(frozen=True)
class SignalObservation:
    level: BarsLevel
    opportunity: Opportunity
    red_flags_hit: bool = False


def combine_signal(observations: list[SignalObservation]) -> tuple[SignalState, int | None]:
    """Collapse all observations of one signal into a state + integer score.
    Opportunity gating: observations without full/partial opportunity don't count;
    a `below_bar` only becomes a real low score at `full` opportunity."""
    assessed = [o for o in observations if o.opportunity in ("full", "partial")]
    # A below_bar is only a *confident* low score at full opportunity.
    confident = [o for o in observations if o.opportunity == "full"]
    if not confident and not assessed:
        return "not_assessed", None
    if not confident:
        # Only partial-opportunity touches → not enough to confidently rate.
        return "not_assessed", None

    best = max(confident, key=lambda o: _RANK[o.level])
    any_redflag = any(o.red_flags_hit for o in confident)

    if best.level == "excellent" and not any_redflag:
        state: SignalState = "excellent"
    elif best.level == "excellent" and any_redflag:
        state = "meets_bar"                 # red flag caps excellent down to meets_bar
    elif best.level == "meets_bar" and any_redflag:
        state = "below_bar"                 # red flag pulls meets_bar down to below_bar
    elif best.level == "below_bar":
        state = "below_bar"
    else:
        state = best.level                  # meets_bar, no red flag
    score = LEVEL_POINTS.get(state)         # not_assessed not reached here
    return state, score


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    state: SignalState
    score: int | None


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float          # assessed weight / total weight in this dimension
    confidence: Confidence


def _confidence(coverage: float) -> Confidence:
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.4:
        return "medium"
    return "low"


def score_dimension(
    name: str, signals: list[ScoredSignal], types: frozenset[str]
) -> DimensionScore:
    """Weighted mean over signals whose type is in `types`.

    Coverage = assessed weight / dimension total weight (not overall total).
    """
    members = [s for s in signals if s.type in types]
    total_w = sum(s.weight for s in members)
    assessed = [s for s in members if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return DimensionScore(name=name, score=None, coverage=0.0, confidence="low")
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w
    coverage = (assessed_w / total_w) if total_w else 0.0
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=coverage, confidence=_confidence(coverage))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Overall = weighted mean over ALL assessed JD signals; coverage over all JD signals."""
    total_w = sum(s.weight for s in signals)
    assessed = [s for s in signals if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w
    return int(round(weighted)), (assessed_w / total_w if total_w else 0.0)


def knockout_status(*, state: SignalState) -> KnockoutStatus:
    if state == "not_assessed":
        return "insufficient"
    if state == "below_bar":
        return "failed"
    return "passed"          # meets_bar | excellent


@dataclass(frozen=True)
class KnockoutResult:
    signal: str
    status: KnockoutStatus
    reason: str
    evidence: list  # list[Evidence-as-dict]


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


def resolve_verdict(*, overall: int | None, coverage: float,
                    knockouts: list[KnockoutResult]) -> VerdictResult:
    failed = [k for k in knockouts if k.status == "failed"]
    if failed:
        return VerdictResult("reject", f"failed must-have: {failed[0].signal}")
    insufficient = [k for k in knockouts if k.status == "insufficient"]
    if insufficient:
        return VerdictResult("borderline",
                             f"couldn't confirm must-have: {insufficient[0].signal}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if overall >= ADVANCE_THRESHOLD and coverage < MIN_COVERAGE_FOR_ADVANCE:
        return VerdictResult("borderline", "not enough assessed to advance confidently")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")
