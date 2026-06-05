"""
Brain deterministic policy gates — D5 task.

All three gates run on the live critical path.  They are PURE Python functions
(no livekit, no I/O, no LLM) and must NEVER raise under any input.  Each gate
is built with defensive guards so unexpected inputs produce a safe fallback
rather than propagating an exception to the engine loop.

Gates
------
1. gate_knockout      — verified-knockout state machine.
   Blocks a premature `close` when a mandatory-absent signal has not yet been
   walked through the full probe → check_alternatives → reflect_confirm chain.
   Exposes the current step so the engine can steer toward verification even
   when it is not blocking a close.

2. scrub_composed_say — no-leak scrub.
   Checks whether `composed_say` echoes any known rubric secret string
   (literal substring match, case-insensitive, length-gated to avoid short
   coincidental words).  A matched secret → safe fallback.  This is NOT intent
   classification; it is literal known-string detection.

3. coerce_probe_index — probe coherence.
   Ensures the brain's chosen probe_index is a valid, unused index into the
   active question's follow_ups list.  Coerces an invalid or repeat index to
   the first unused one; returns None when no probes remain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from app.modules.interview_engine.contracts import ActiveQuestionRubric, BrainMove

# ---------------------------------------------------------------------------
# Shared constant
# ---------------------------------------------------------------------------

SAFE_FALLBACK: str = (
    "Sorry, let me put that differently — could you tell me a bit more about that?"
)

# ---------------------------------------------------------------------------
# Optional belt-and-suspenders meta-phrase list.
# These are obvious rubric/evaluation meta-phrases that should NEVER appear in
# candidate-facing text regardless of whether they echo a known secret.  The
# primary leak check is the known-rubric-string match above; this list is a
# secondary catch for common slip-through patterns.  Keep it TINY and literal.
# ---------------------------------------------------------------------------
_META_PHRASES: tuple[str, ...] = (
    "what i'm looking for",
    "the rubric",
    "evaluation criteria",
    "evaluation hint",
    "the rubric says",
    "rubric says",
)


# ===========================================================================
# Gate 1 — Verified-knockout state machine
# ===========================================================================

class KnockoutStep(StrEnum):
    """Ordered steps the engine must walk through before confirming a knockout."""
    probe             = "probe"
    check_alternatives = "check_alternatives"
    reflect_confirm   = "reflect_confirm"
    confirmed         = "confirmed"


_KNOCKOUT_PROGRESSION: tuple[KnockoutStep, ...] = (
    KnockoutStep.probe,
    KnockoutStep.check_alternatives,
    KnockoutStep.reflect_confirm,
    KnockoutStep.confirmed,
)


class KnockoutTracker:
    """Per-session mutable tracker for verified-knockout progress.

    Plain Python, no pydantic.  Tracks each pending mandatory signal as an
    index into :data:`_KNOCKOUT_PROGRESSION`.  Designed to be safe against
    arbitrary signal names; unknown signals always default to ``probe``.
    """

    def __init__(self) -> None:
        # Maps signal string → int index in _KNOCKOUT_PROGRESSION.
        self._steps: dict[str, int] = {}

    def current_step(self, signal: str) -> KnockoutStep:
        """Return the current step for *signal* (defaults to ``probe`` for unseen)."""
        idx = self._steps.get(signal, 0)
        return _KNOCKOUT_PROGRESSION[idx]

    def advance(self, signal: str) -> None:
        """Advance *signal* to the next step; idempotent at ``confirmed``."""
        idx = self._steps.get(signal, 0)
        # Cap at the last index (confirmed) to stay idempotent.
        self._steps[signal] = min(idx + 1, len(_KNOCKOUT_PROGRESSION) - 1)

    def is_confirmed(self, signal: str) -> bool:
        """Return True iff *signal* has reached ``confirmed``."""
        return self.current_step(signal) == KnockoutStep.confirmed


@dataclass(frozen=True)
class KnockoutGate:
    """Result returned by :func:`gate_knockout`."""
    allow_move: bool
    """True → the brain's proposed move proceeds; False → the engine must run the knockout flow instead."""
    forced_step: KnockoutStep | None
    """The knockout step the engine should execute next, or None when there is nothing pending."""
    signal: str | None
    """Which mandatory signal is being driven, or None when there is nothing pending."""


def gate_knockout(
    *,
    proposed_move: BrainMove,
    knockout_pending: Sequence[str],
    tracker: KnockoutTracker,
) -> KnockoutGate:
    """Deterministic verified-knockout gate.

    Behaviour
    ---------
    - Empty *knockout_pending* → pass-through (allow_move=True, forced_step=None, signal=None).
    - If the first *unconfirmed* pending signal exists:
      - Proposed move is ``close`` → BLOCK (allow_move=False) and surface the current step.
      - Any other move → ALLOW but still surface the pending step so the engine can steer.
    - If all pending signals are already confirmed → pass-through.

    This function NEVER raises.
    """
    try:
        if not knockout_pending:
            return KnockoutGate(allow_move=True, forced_step=None, signal=None)

        # Find the first signal that is not yet confirmed.
        first_unconfirmed: str | None = None
        for signal in knockout_pending:
            if not tracker.is_confirmed(signal):
                first_unconfirmed = signal
                break

        if first_unconfirmed is None:
            # All pending signals have been verified — nothing to block.
            return KnockoutGate(allow_move=True, forced_step=None, signal=None)

        current_step = tracker.current_step(first_unconfirmed)

        if proposed_move == BrainMove.close:
            # Block: do NOT close until the knockout is verified.
            return KnockoutGate(
                allow_move=False,
                forced_step=current_step,
                signal=first_unconfirmed,
            )

        # Non-close move: allow it but expose the pending step.
        # The engine decides whether to act on forced_step; the gate never raises.
        return KnockoutGate(
            allow_move=True,
            forced_step=current_step,
            signal=first_unconfirmed,
        )

    except Exception:  # pragma: no cover — defensive catch-all
        # Should never be reached, but if something goes wrong we must not
        # crash the engine turn.  Pass-through is the safest fallback.
        return KnockoutGate(allow_move=True, forced_step=None, signal=None)


# ===========================================================================
# Gate 2 — No-leak scrub
# ===========================================================================

def scrub_composed_say(
    text: str | None,
    rubric: ActiveQuestionRubric,
    *,
    fallback: str = SAFE_FALLBACK,
    min_phrase_len: int = 12,
) -> str | None:
    """Check *text* for leaked rubric secrets and return a safe fallback if found.

    Detection strategy
    ------------------
    1. Collect the rubric's KNOWN secret strings:
       ``excellent``, ``meets_bar``, ``below_bar``, every ``positive_evidence``
       entry, every ``red_flags`` entry, ``evaluation_hint``.
    2. For each non-empty secret of length ≥ *min_phrase_len*, perform a
       case-insensitive substring search inside *text*.  A hit → LEAK → return
       *fallback*.
    3. Additionally check a small fixed list of obvious meta-phrases
       (``_META_PHRASES``) regardless of rubric content (belt-and-suspenders).
    4. If no leak found → return *text* unchanged.

    This is LITERAL known-string detection, NOT intent classification or regex.

    This function NEVER raises.
    """
    if text is None:
        return None

    try:
        text_lower = text.lower()

        # --- Primary check: known rubric secret strings ---
        secrets: list[str] = []
        try:
            candidates_raw: list[str | None] = [
                rubric.excellent,
                rubric.meets_bar,
                rubric.below_bar,
                rubric.evaluation_hint,
                *(rubric.positive_evidence or []),
                *(rubric.red_flags or []),
            ]
            for s in candidates_raw:
                if s and len(s) >= min_phrase_len:
                    secrets.append(s)
        except Exception:  # pragma: no cover
            pass  # rubric access error → no secrets to check, skip to meta-phrases

        for secret in secrets:
            try:
                if secret.lower() in text_lower:
                    return fallback
            except Exception:  # pragma: no cover
                pass

        # --- Secondary check: obvious meta-phrases (belt-and-suspenders) ---
        for phrase in _META_PHRASES:
            if phrase in text_lower:
                return fallback

        return text

    except Exception:  # pragma: no cover
        # Defensive catch-all — if anything goes wrong, return the text
        # unchanged rather than surfacing an exception to the engine turn.
        return text


# ===========================================================================
# Gate 3 — Probe coherence
# ===========================================================================

def coerce_probe_index(
    probe_index: int | None,
    *,
    follow_ups: list[str],
    probes_used: list[int],
) -> int | None:
    """Coerce the brain's *probe_index* to a valid, unused index.

    Returns
    -------
    int
        A valid index ``i`` such that ``0 <= i < len(follow_ups)`` and
        ``i not in probes_used``.
    None
        When no unused probe remains (all used, or *follow_ups* is empty).

    This function NEVER raises.
    """
    try:
        if not follow_ups:
            return None

        # Build the ordered list of available (unused) indices.
        # Use a set for O(1) membership test to guard against a large probes_used.
        used_set: set[int] = set()
        try:
            used_set = set(probes_used)
        except Exception:  # pragma: no cover
            pass

        available = [i for i in range(len(follow_ups)) if i not in used_set]

        if not available:
            return None

        # Check whether the proposed index is already valid and unused.
        if (
            probe_index is not None
            and 0 <= probe_index < len(follow_ups)
            and probe_index not in used_set
        ):
            return probe_index

        # Coerce to the first available unused probe.
        return available[0]

    except Exception:  # pragma: no cover
        return None
