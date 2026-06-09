"""Question→signal roll-up. The dedicated question (primary_signal match) anchors
a signal's level; cross-credit from other questions can lift it by at most ONE
tier; when the dedicated question was never reached, cross-credit is authoritative.
Pure — no IO/LLM. The signal stays the unit of the verdict (downstream aggregate)."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.reporting.scoring.types import DemonstrationLevel

# Level ladder (low→high). not_reached is OFF-ladder (no data) and handled separately.
_LADDER: list[str] = ["absent", "thin", "solid", "strong"]


def _rank(level: str | None) -> int | None:
    return _LADDER.index(level) if level in _LADDER else None


@dataclass(frozen=True)
class SignalRollup:
    level: DemonstrationLevel
    cross_credit_applied: bool
    level_basis: str


def pick_dedicated_question(signal: str, questions: list[dict], outcomes: dict[str, str]) -> dict | None:
    """The signal's dedicated question: primary_signal == signal, preferring
    asked over not_reached, then lowest position. Returns the question dict or None."""
    owned = [q for q in questions if q.get("primary_signal") == signal]
    if not owned:
        return None

    def key(q: dict) -> tuple[int, int]:
        asked = outcomes.get(q["id"]) == "asked"
        return (0 if asked else 1, q.get("position", 1_000_000))

    return sorted(owned, key=key)[0]


def roll_up_signal(
    *, signal: str, dedicated_level: DemonstrationLevel | None,
    dedicated_outcome: str | None, cross_credit_level: str | None,
) -> SignalRollup:
    # No dedicated question asked → cross-credit is authoritative (charitable).
    if dedicated_outcome != "asked" or dedicated_level is None:
        if cross_credit_level and _rank(cross_credit_level) is not None:
            return SignalRollup(level=cross_credit_level,  # type: ignore[arg-type]
                                cross_credit_applied=True,
                                level_basis=f"no dedicated question asked; cross-credit → {cross_credit_level}")
        return SignalRollup(level="not_reached", cross_credit_applied=False,
                            level_basis="never asked; no cross-credit")

    base = dedicated_level
    base_rank = _rank(base)
    # A genuine disclaim (absent) is never lifted by an incidental mention elsewhere.
    if base == "absent":
        return SignalRollup(level="absent", cross_credit_applied=False,
                            level_basis="dedicated: absent (disclaim) — not lifted")

    cc_rank = _rank(cross_credit_level)
    if cc_rank is not None and base_rank is not None and cc_rank > base_rank:
        lifted = _LADDER[base_rank + 1]
        return SignalRollup(level=lifted,  # type: ignore[arg-type]
                            cross_credit_applied=True,
                            level_basis=f"dedicated: {base}; +1 cross-credit → {lifted}")
    return SignalRollup(level=base, cross_credit_applied=False,
                        level_basis=f"dedicated: {base}")
