"""Deterministic, pure scoring math: signal-state → dimension → knockout gate →
overall → verdict. No LLM, no IO. This is the auditable core; same logs → same number."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime.evidence import EvidenceNote, EvidenceStance, EvidenceTexture
from app.modules.reporting.scoring.types import DemonstrationLevel
from app.modules.reporting.scoring.constants import (
    ADVANCE_THRESHOLD,
    BORDERLINE_CEILING,
    HOLISTIC_ADJ_MAX,
    MIN_COVERAGE_FOR_ADVANCE,
    REJECT_CEILING,
    REJECT_THRESHOLD,
    STATE_TEXTURE_POINTS,
)
from app.modules.reporting.scoring.engine_signals import KnockoutClose
from app.modules.reporting.scoring.types import (
    Confidence, CovState, GradeTexture, KnockoutStatus, Verdict,
)


_TEXTURE_RANK = {EvidenceTexture.thin: 0, EvidenceTexture.concrete: 1, EvidenceTexture.strong: 2}
_RANK_LEVEL = {2: "strong", 1: "solid", 0: "thin"}


def level_for_signal(
    notes: list[EvidenceNote], *, provenance: str, closure: str | None
) -> DemonstrationLevel:
    """Roll a signal's notes + provenance/closure into one demonstration level.

    Supporting notes → level by best texture (strong>concrete>thin). No supports:
    `probed_absent` → absent; an un-retracted contradiction → absent; else
    (`not_reached`, including closure=truncated) → not_reached.
    """
    supports = [n for n in notes if n.stance == EvidenceStance.supports]
    if supports:
        best = max(_TEXTURE_RANK[n.texture] for n in supports)
        return _RANK_LEVEL[best]  # type: ignore[return-value]

    unretracted_contradiction = any(
        n.stance == EvidenceStance.contradicts and n.retracts_seq is None for n in notes
    )
    if provenance == "probed_absent" or unretracted_contradiction:
        return "absent"
    return "not_reached"


def score_signal(state: CovState, texture: GradeTexture | None) -> int | None:
    """Per-signal points from coverage state AND evidence texture.
    `none` → None (excluded from the denominator). Texture defaults to
    `concrete` (no penalty) when a signal was not LLM-rechecked."""
    if state == "none":
        return None
    return STATE_TEXTURE_POINTS[state][texture or "concrete"]


def score_state(state: CovState) -> int | None:
    """Back-compat: concrete-texture baseline (no bluff penalty)."""
    return score_signal(state, "concrete")


@dataclass(frozen=True)
class ScoredSignal:
    value: str
    type: str
    weight: int
    knockout: bool
    priority: str
    state: CovState
    score: int | None
    texture: GradeTexture = "concrete"


@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int | None
    coverage: float       # assessed weight / total weight in this dimension
    confidence: Confidence


def confidence_from_coverage(coverage: float) -> Confidence:
    if coverage >= 0.75:
        return "high"
    if coverage >= 0.4:
        return "medium"
    return "low"


def score_dimension(
    name: str, signals: list[ScoredSignal], types: frozenset[str]
) -> DimensionScore:
    members = [s for s in signals if s.type in types]
    total_w = sum(s.weight for s in members)
    assessed = [s for s in members if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return DimensionScore(name=name, score=None, coverage=0.0, confidence="low")
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w  # type: ignore[operator]
    coverage = (assessed_w / total_w) if total_w else 0.0
    return DimensionScore(name=name, score=int(round(weighted)),
                          coverage=coverage, confidence=confidence_from_coverage(coverage))


def score_overall(signals: list[ScoredSignal]) -> tuple[int | None, float]:
    """Overall = weighted mean over ALL assessed JD signals (tech + behavioral).
    Communication is scored separately and is NOT included here."""
    total_w = sum(s.weight for s in signals)
    assessed = [s for s in signals if s.score is not None]
    assessed_w = sum(s.weight for s in assessed)
    if assessed_w == 0:
        return None, 0.0
    weighted = sum(s.weight * s.score for s in assessed) / assessed_w  # type: ignore[operator]
    return int(round(weighted)), (assessed_w / total_w if total_w else 0.0)


def knockout_status(*, state: CovState) -> KnockoutStatus:
    if state == "none":
        return "insufficient"      # never assessed → couldn't confirm the must-have
    if state == "failed":
        return "failed"            # genuine absence/disclaim of a must-have → hard knockout reject
    if state == "partial":
        return "insufficient"      # engaged but didn't establish depth → couldn't confirm
    return "passed"                # sufficient | exceeded


@dataclass(frozen=True)
class KnockoutResult:
    signal: str
    status: KnockoutStatus
    reason: str


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    reason: str


def signal_ceiling(
    signals: list[ScoredSignal], *, knockout_close: bool, coverage: float
) -> int | None:
    """The fit ceiling implied by must-have status + coverage. None = no cap."""
    must_haves = [s for s in signals if s.knockout]
    if knockout_close or any(s.state == "failed" for s in must_haves):
        return REJECT_CEILING
    if any(s.state in ("none", "partial") for s in must_haves) or (
        coverage < MIN_COVERAGE_FOR_ADVANCE
    ):
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
    *, overall: int | None, coverage: float,
    knockouts: list[KnockoutResult], knockout_close: KnockoutClose | None,
) -> VerdictResult:
    """Score-driven verdict. `overall` already encodes must-have/coverage caps
    (see signal_ceiling), so the score band is the primary decision. knockout_close
    and a failed must-have remain categorical reject backstops (defense-in-depth)."""
    if knockout_close is not None:
        sig = knockout_close.signal or "a must-have skill"
        return VerdictResult("reject", f"Interview closed on a must-have gap: {sig}")
    if any(k.status == "failed" for k in knockouts):
        failed = next(k for k in knockouts if k.status == "failed")
        return VerdictResult("reject", f"failed must-have: {failed.signal}")
    if overall is None:
        return VerdictResult("borderline", "no assessable evidence collected")
    if overall >= ADVANCE_THRESHOLD:
        return VerdictResult("advance", "meets the bar across assessed signals")
    if overall < REJECT_THRESHOLD:
        return VerdictResult("reject", "below the bar across assessed signals")
    return VerdictResult("borderline", "mixed evidence — human review")
