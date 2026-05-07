"""QuestionQueue — per-question state machine with mandatory enforcement and hard-advance."""
from __future__ import annotations

from typing import Any

from app.modules.interview_engine.models.queue import (
    QuestionQueueSnapshot, QuestionState, QuestionStatus,
)


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
        """Build from a list of dicts: {question_id, is_mandatory, follow_ups: list[str]}.

        Each follow_up's array index becomes its probe_id ('0', '1', ...).
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

    def complete_active(self, *, at_turn: int) -> None:
        """Explicit completion of the currently active question (e.g. session-end while active)."""
        active = self.active_state()
        if active is None:
            return
        active.status = QuestionStatus.completed
