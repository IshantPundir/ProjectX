"""QuestionQueue — per-question state machine with mandatory enforcement and hard-advance."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.interview_engine.models.ledger import CoverageState, SignalSnapshot
from app.modules.interview_engine.models.queue import (
    QuestionQueueSnapshot, QuestionState, QuestionStatus,
)

if TYPE_CHECKING:
    pass


class QueueError(Exception):
    """Generic queue invariant violation."""


class NoActiveQuestionError(QueueError):
    """Operation requires an active question, but there is none."""


class QuestionQueue:
    """Per-question state machine.

    Hard-advance: once a question is completed (advanced past), it never re-activates.
    Probes are consumed from probes_remaining_ids and recorded in probes_asked_ids.
    """

    def __init__(self, states: list[QuestionState]) -> None:
        self._states = states
        self._active_index: int | None = None

    @classmethod
    def from_initial(cls, *, questions: list[dict[str, Any]]) -> "QuestionQueue":
        """Build from a list of dicts: {question_id, is_mandatory, follow_ups: list[str],
        signal_values: list[str] (optional, empty list if not provided)}.

        Each follow_up's array index becomes its probe_id ('0', '1', ...).
        ``signal_values`` is used by the non-mandatory question selector
        (Cluster G) to check whether any of the question's signals still
        have uncovered coverage.
        """
        states: list[QuestionState] = []
        for position, q in enumerate(questions):
            probe_ids = [str(i) for i in range(len(q["follow_ups"]))]
            states.append(
                QuestionState(
                    question_id=q["question_id"],
                    position=position,
                    is_mandatory=q["is_mandatory"],
                    status=QuestionStatus.pending,
                    probes_remaining_ids=probe_ids,
                    signal_values=list(q.get("signal_values", [])),
                )
            )
        return cls(states)

    @classmethod
    def from_snapshot(cls, snap: QuestionQueueSnapshot) -> "QuestionQueue":
        q = cls([s.model_copy() for s in snap.questions])
        q._active_index = snap.active_index
        return q

    def snapshot(self) -> QuestionQueueSnapshot:
        return QuestionQueueSnapshot(
            questions=[s.model_copy() for s in self._states],
            active_index=self._active_index,
        )

    # --- Queries ---

    def active_question_id(self) -> str | None:
        if self._active_index is None:
            return None
        return self._states[self._active_index].question_id

    def active_state(self) -> QuestionState | None:
        if self._active_index is None:
            return None
        return self._states[self._active_index]

    def next_pending_mandatory_id(self) -> str | None:
        for s in self._states:
            if s.is_mandatory and s.status == QuestionStatus.pending:
                return s.question_id
        return None

    def next_pending_question_id(
        self,
        *,
        signal_coverage: dict[str, SignalSnapshot],
    ) -> tuple[str, bool] | None:
        """Return (question_id, is_mandatory) of the next question to ask.

        Selection rule:
        1. Mandatory pending questions first, in position order (unchanged from
           legacy next_pending_mandatory_id behavior — they still gate the
           session).
        2. When mandatory queue exhausted, walk non-mandatory pending questions
           in position order. For each candidate, check whether any of its
           signal_values have current coverage in {none, partial}. Return the
           first one that does — that question can still add signal evidence.
           Skip questions whose signals are all already ``sufficient``.
        3. Return None when no question qualifies. Caller treats None as
           "session done, emit polite_close".

        Time budget is NOT re-checked here; the bank-generator already owned
        that at generation time. The Judge prompt's existing
        ``time_remaining_seconds ≤ 60 + current_partial → polite_close`` rule
        handles in-flight termination.
        """
        # 1. Mandatory first (position order).
        for s in self._states:
            if s.is_mandatory and s.status == QuestionStatus.pending:
                return s.question_id, True

        # 2. Non-mandatory with at least one uncovered signal.
        for s in self._states:
            if s.is_mandatory or s.status != QuestionStatus.pending:
                continue
            for sv in s.signal_values:
                snap = signal_coverage.get(sv)
                if snap is None or snap.coverage in {CoverageState.none, CoverageState.partial}:
                    return s.question_id, False
            # All signals are sufficient (or failed) — skip this question.

        return None

    def all_mandatory_complete(self) -> bool:
        for s in self._states:
            if s.is_mandatory and s.status != QuestionStatus.completed:
                return False
        return True

    def find_position(self, question_id: str) -> int:
        for i, s in enumerate(self._states):
            if s.question_id == question_id:
                return i
        raise QueueError(f"Unknown question_id: {question_id!r}")

    # --- Mutations ---

    def advance_to(self, question_id: str, *, at_turn: int) -> None:
        target = self.find_position(question_id)
        if self._active_index is not None and target <= self._active_index:
            raise QueueError(
                f"Backward advance not allowed: active is index {self._active_index}, "
                f"target is index {target}"
            )
        # Mark prior active completed.
        if self._active_index is not None:
            self._states[self._active_index].status = QuestionStatus.completed
        # Mark intermediate pending questions as skipped.
        start = 0 if self._active_index is None else self._active_index + 1
        for i in range(start, target):
            if self._states[i].status == QuestionStatus.pending:
                self._states[i].status = QuestionStatus.skipped
        # Activate target.
        new_active = self._states[target]
        new_active.status = QuestionStatus.active
        new_active.main_asked_at_turn = at_turn
        self._active_index = target

    def apply_probe(self, *, probe_id: str, at_turn: int) -> None:
        active = self.active_state()
        if active is None:
            raise NoActiveQuestionError("Cannot apply probe without an active question")
        if probe_id not in active.probes_remaining_ids:
            raise QueueError(
                f"Probe id {probe_id!r} not in remaining {active.probes_remaining_ids!r}"
            )
        active.probes_remaining_ids.remove(probe_id)
        active.probes_asked_ids.append(probe_id)

    def record_anchor_hit(self, *, anchor_id: int) -> None:
        active = self.active_state()
        if active is None:
            raise NoActiveQuestionError("Cannot record anchor without an active question")
        if anchor_id >= 0 and anchor_id not in active.anchors_hit_ids:
            active.anchors_hit_ids.append(anchor_id)

    def increment_active_turn(self, *, elapsed_ms: int) -> None:
        active = self.active_state()
        if active is None:
            raise NoActiveQuestionError("Cannot increment without an active question")
        active.turn_count += 1
        active.time_spent_ms += elapsed_ms

    def increment_active_push_back_count(self) -> int:
        """Bump push_back_count on the active question. Returns the new value.

        The State Engine reads the returned value to decide whether to honor
        the next push_back (cap=2) — see ``StateEngine._handle_push_back``.
        """
        active = self.active_state()
        if active is None:
            raise NoActiveQuestionError(
                "Cannot increment push_back_count without an active question"
            )
        active.push_back_count += 1
        return active.push_back_count

    def active_push_back_count(self) -> int:
        """Read push_back_count on the active question. Returns 0 if no active."""
        active = self.active_state()
        if active is None:
            return 0
        return active.push_back_count

    def increment_active_still_confused_count(self) -> int:
        """Bump still_confused_count on the active question. Returns
        the new value. Drives the Judge prompt's escalation rule when the
        candidate signals generic confusion / cannot engage
        (candidate_still_confused=true on TurnMetadata). Returns 0
        silently when there is no active question (defensive — should not
        happen in normal flow)."""
        active = self.active_state()
        if active is None:
            return 0
        active.still_confused_count += 1
        return active.still_confused_count

    def reset_active_still_confused_count(self) -> None:
        """Reset still_confused_count on the active question to 0.
        Called when the candidate gives any turn where
        candidate_still_confused is not set (the streak is broken).
        No-op when there is no active question."""
        active = self.active_state()
        if active is None:
            return
        active.still_confused_count = 0

    def active_still_confused_count(self) -> int:
        """Read still_confused_count on the active question. Returns
        0 if no active question (matches Judge prompt default)."""
        active = self.active_state()
        if active is None:
            return 0
        return active.still_confused_count

    def record_quality_observation(self, *, quality: str) -> None:
        """Increment ``quality_observations[quality]`` on the active question.

        Called by the State Engine after every observation that successfully
        applied to the ledger. The counter is what the advance-gate checks
        for "at least one concrete or strong observation on this question."
        Returns silently when there is no active question — gracefully
        accommodates the synthetic session-start path.
        """
        active = self.active_state()
        if active is None:
            return
        active.quality_observations[quality] = (
            active.quality_observations.get(quality, 0) + 1
        )

    def active_has_quality_at_least_concrete(self) -> bool:
        """True iff the active question has accumulated >=1 concrete/strong obs.

        Used by ``StateEngine`` to gate ``advance``: a clean advance requires
        at least one observation on the active question to have reached
        ``concrete`` or ``strong`` quality. All-thin coverage triggers a
        downgrade to push_back with reason_code=missing_specifics.
        """
        active = self.active_state()
        if active is None:
            return False
        return (
            active.quality_observations.get("concrete", 0) > 0
            or active.quality_observations.get("strong", 0) > 0
        )

    def complete_active(self, *, at_turn: int) -> None:
        """Explicit completion of the currently active question (e.g. session-end while active)."""
        active = self.active_state()
        if active is None:
            return
        active.status = QuestionStatus.completed
