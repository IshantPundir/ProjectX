"""Per-turn audit decision record — the brain's full reasoning trail.

This is the auditable artifact (EEOC/bias defensibility, forensic debugging). It is
the SUPERSET of what the Directive carries: the Directive is just the speakable
projection of this record (DESIGN-SPEC §13, doc 11). The brain's rubric/evidence
reasoning lives HERE and never travels to the mouth.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TurnDecisionRecord(BaseModel):
    """One brain decision, logged alongside (not inside) the Directive it produced."""

    turn_ref: str = Field(min_length=1)
    candidate_quote: str = Field(
        description="The candidate utterance graded this turn (quoted DATA).",
    )
    attributed_signals: list[str] = Field(
        default_factory=list, description="Signal value(s) the answer was credited to."
    )
    grade: Literal["thin", "concrete", "strong"] | None = Field(
        default=None, description="Evidence grade vs rubric; None when not a gradeable turn."
    )
    coverage_delta: dict[str, str] = Field(
        default_factory=dict,
        description="signal_value -> new coverage state (none/partial/sufficient/failed).",
    )
    move: str = Field(
        min_length=1,
        description="The chosen move (probe/advance/clarify/knockout/...).",
    )
    reasoning: str = Field(description="The brain's autoregressive reasoning for this decision.")
    policy_checks: list[str] = Field(
        default_factory=list, description="Deterministic gates that passed/fired this turn."
    )
    directive_id: str = Field(min_length=1, description="The Directive this record produced.")
