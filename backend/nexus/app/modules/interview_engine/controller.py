"""The two-plane controller core (pure, no LLM, no livekit).

Holds the single current Directive and enforces the Option C delivery invariants:
staging, supersession, staleness, and speculative discard. The mouth asks
`current_for_turn(turn_ref)` at a turn boundary and delivers whatever it returns
(or nothing). Because the AI only ever delivers at a turn boundary (one-directional
interruption invariant, doc 08), supersession is always clean — there is never a
half-spoken Directive to abort.
"""

from __future__ import annotations

from app.modules.interview_engine.directive import Directive


class DirectiveController:
    def __init__(self) -> None:
        self._staged: Directive | None = None
        self._delivered_ids: set[str] = set()
        self._discarded_ids: set[str] = set()

    def stage(self, directive: Directive) -> None:
        """Stage a directive. If it supersedes the staged one, discard the old."""
        if (
            directive.supersedes is not None
            and self._staged is not None
            and self._staged.id == directive.supersedes
        ):
            self._discarded_ids.add(self._staged.id)
        self._staged = directive

    def discard_speculative(self) -> None:
        """Drop the staged directive iff it is a speculative pre-stage."""
        if self._staged is not None and self._staged.speculative:
            self._discarded_ids.add(self._staged.id)
            self._staged = None

    def staged_id(self) -> str | None:
        """The id of the currently-staged directive, or None.

        The agent uses this to decide whether its confirmed brain directive must supersede a
        still-staged speculative pre-stage (Option C) — never reaches into `_staged` directly.
        """
        return self._staged.id if self._staged is not None else None

    def current_for_turn(self, turn_ref: str) -> Directive | None:
        """The directive to deliver at this boundary, or None.

        Returns None when: nothing staged, the staged directive was discarded /
        superseded, or it was computed for a different turn (stale).
        """
        d = self._staged
        if d is None:
            return None
        if d.id in self._discarded_ids:
            return None
        if d.turn_ref != turn_ref:
            return None
        return d

    def mark_delivered(self, directive_id: str) -> None:
        """Record delivery; clears the staged slot if it matches."""
        self._delivered_ids.add(directive_id)
        if self._staged is not None and self._staged.id == directive_id:
            self._staged = None

    def was_delivered(self, directive_id: str) -> bool:
        return directive_id in self._delivered_ids

    def is_discarded(self, directive_id: str) -> bool:
        return directive_id in self._discarded_ids
