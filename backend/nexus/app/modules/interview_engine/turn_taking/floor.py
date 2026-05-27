"""Floor control — the one-directional-interruption invariant + a barge-in
resumption-classification SCAFFOLD (pure, no livekit).

INVARIANT (DESIGN-SPEC §4, doc 08 "resolved"): the candidate may interrupt the AI;
the AI NEVER interrupts the candidate, and yields to any genuine speech (>= min
words, not a backchannel). At runtime LiveKit's adaptive interruption enforces the
yield; `should_yield` is the pure statement of the rule (used for audit + tests).

SCAFFOLD (doc 08): `classify_resumption` maps signals already captured at the turn
boundary to a provisional continuation/early/barge-in/backchannel label. It is
recorded for audit and consumed later by the M5 brain (which does the AUTHORITATIVE,
semantic attribution by meaning). It MUST NOT gate realtime behavior — we never race
a real-time classifier (the old continuation-watcher's mistake). The AI yields
regardless of the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Resume within this gap, while the AI had only just started, reads as the candidate
# finishing their own prior thought (doc 03 O5 / doc 08). Tunable later if needed.
_CONTINUATION_GAP_MS = 1500


class ResumptionLabel(str, Enum):
    BACKCHANNEL = "backchannel"
    CONTINUATION = "continuation"
    EARLY_ANSWER = "early_answer"
    BARGE_IN = "barge_in"


@dataclass(frozen=True)
class ResumptionSignals:
    """Signals captured at the boundary (none individually decisive — doc 08)."""

    prior_utterance_complete: bool   # turn-detector view of the prior candidate turn
    gap_ms: int                      # ms from prior candidate EOU to this resume
    ai_prompt_fully_delivered: bool  # had the AI finished delivering its line?
    word_count: int
    is_backchannel: bool


def should_yield(*, word_count: int, is_backchannel: bool) -> bool:
    """The AI yields the floor to any genuine speech (not a backchannel)."""
    return word_count >= 1 and not is_backchannel


def classify_resumption(signals: ResumptionSignals) -> ResumptionLabel:
    """Provisional label for the audit trail (doc 08 flowchart). ADVISORY ONLY."""
    if signals.is_backchannel:
        return ResumptionLabel.BACKCHANNEL
    if not signals.prior_utterance_complete and signals.gap_ms <= _CONTINUATION_GAP_MS:
        return ResumptionLabel.CONTINUATION
    if not signals.ai_prompt_fully_delivered:
        # candidate spoke before the AI finished its new prompt -> cannot be an
        # answer to that prompt; it's a go-back / repair / continuation -> barge-in.
        return ResumptionLabel.BARGE_IN
    return ResumptionLabel.EARLY_ANSWER
