"""Deterministic, pure scoring math: signal → dimension → knockout gate → overall → verdict.
No LLM, no IO. This is the auditable core; everything here is unit-tested exhaustively."""
from __future__ import annotations
from dataclasses import dataclass

from app.modules.reporting.scoring.constants import LEVEL_POINTS
from app.modules.reporting.scoring.types import BarsLevel, Opportunity, SignalState

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
