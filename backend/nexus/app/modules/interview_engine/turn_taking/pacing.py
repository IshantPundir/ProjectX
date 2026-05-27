"""Pacing — dynamic endpointing config + the hold-space pause reflex.

Pure (no livekit, no LLM). The harness (agent.py) builds the LiveKit
TurnHandlingOptions endpointing dict from build_endpointing_options(), and ticks
HoldSpacePacer off a silence timer to decide when to speak ONE warm "take your
time" cue on a long mid-answer pause. The turn-detector + endpointing decide
when the answer is actually COMPLETE; the pacer only fires while the turn is
still open (candidate formulating), so it never lands on a complete answer
(DESIGN-SPEC §3, doc 08 "resolved", M5 decision E).

Incompleteness gate (M5 R3): LiveKit's MultilingualModel does NOT expose a
mid-pause "incomplete/extending" signal or EOU probability. The gate is
enforced in agent.py via the delay-above-commit-latency proxy: the cue delay
is set above the worst-case complete-answer commit latency so a complete turn
always fires on_user_turn_completed (setting state["responding"]=True) before
the pacer elapses. Only a detector-held-open incomplete pause reaches the cue.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointingSettings:
    """The three values LiveKit's endpointing dict needs (DESIGN-SPEC §3)."""

    mode: str            # "dynamic" (per-candidate adaptive) | "fixed"
    min_delay: float
    max_delay: float


def build_endpointing_options(settings: EndpointingSettings) -> dict[str, object]:
    """Render the plain dict passed to TurnHandlingOptions(endpointing=...)."""
    return {
        "mode": settings.mode,
        "min_delay": settings.min_delay,
        "max_delay": settings.max_delay,
    }


class HoldSpacePacer:
    """Owes at most one hold-space cue per open mid-answer pause.

    Lifecycle, driven by the harness off user-state transitions:
      - on_pause_started(at_s): candidate stopped speaking, turn still open.
      - cue_due(now_s): True once `delay_s` has elapsed and the cue is unspent.
      - mark_cued(): record that the cue was spoken for this pause.
      - on_resume(): candidate started speaking again -> clear pause state.
    """

    def __init__(self, *, enabled: bool, delay_s: float) -> None:
        self._enabled = enabled
        self._delay_s = delay_s
        self._pause_started_at: float | None = None
        self._cued_this_pause = False

    def on_pause_started(self, at_s: float) -> None:
        self._pause_started_at = at_s
        self._cued_this_pause = False

    def on_resume(self) -> None:
        self._pause_started_at = None
        self._cued_this_pause = False

    def cue_due(self, now_s: float) -> bool:
        if not self._enabled or self._pause_started_at is None or self._cued_this_pause:
            return False
        return (now_s - self._pause_started_at) >= self._delay_s

    def mark_cued(self) -> None:
        self._cued_this_pause = True
