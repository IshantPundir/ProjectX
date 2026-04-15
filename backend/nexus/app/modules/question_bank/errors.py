"""Custom exceptions raised by the question_bank service.

Each exception is mapped to an HTTP response in app/main.py via FastAPI
exception handlers. Exceptions carry structured data so the handlers can
produce specific error messages.
"""

from __future__ import annotations

from uuid import UUID


class BankAlreadyGeneratingError(Exception):
    """Generation was triggered while another generation was already in progress."""

    def __init__(self, bank_id: UUID):
        self.bank_id = bank_id
        super().__init__(f"Bank {bank_id} is already in 'generating' state")


class IllegalTransitionError(Exception):
    """The bank's current state does not allow the requested transition.

    Distinct from BankAlreadyGeneratingError — that one signals the specific
    double-trigger case (bank is already generating); this one signals any
    OTHER illegal source state (e.g., trying to transition from 'generating'
    to 'confirmed' without going through 'reviewing'). Both map to HTTP 409
    but with different detail messages so the frontend can surface precise
    feedback.
    """

    def __init__(self, from_state: str, to_state: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal bank state transition: {from_state!r} → {to_state!r}"
        )


class BankNotInReviewingError(Exception):
    """Attempted to confirm a bank that is not in 'reviewing' state."""

    def __init__(self, bank_id: UUID, current_status: str):
        self.bank_id = bank_id
        self.current_status = current_status
        super().__init__(
            f"Cannot confirm bank {bank_id}: current status is "
            f"'{current_status}', expected 'reviewing'"
        )


class KnockoutUnprobedError(Exception):
    """A knockout signal has no mandatory question — blocks confirmation."""

    def __init__(self, signal_value: str, bank_id: UUID):
        self.signal_value = signal_value
        self.bank_id = bank_id
        super().__init__(
            f"Cannot confirm: knockout signal '{signal_value}' has no "
            f"mandatory question in bank {bank_id}"
        )


class MandatoryOverrunError(Exception):
    """Sum of mandatory questions' estimated_minutes exceeds the stage duration.

    Only mandatory questions count — optional depth probes may exceed duration
    in aggregate. The session bot skips optional questions when the clock runs out,
    but it cannot skip mandatory questions, so mandatory total must fit.
    """

    def __init__(
        self,
        bank_id: UUID,
        mandatory_minutes: float,
        stage_minutes: int,
    ):
        self.bank_id = bank_id
        self.mandatory_minutes = mandatory_minutes
        self.stage_minutes = stage_minutes
        super().__init__(
            f"Mandatory question time ({mandatory_minutes} min) exceeds the "
            f"stage's session duration ({stage_minutes} min). The session bot "
            f"cannot skip mandatory questions — either shorten mandatory questions, "
            f"demote some to optional, or increase the stage duration."
        )


class SignalValueNotInSnapshotError(Exception):
    """A signal_value referenced by a question does not exist in the pinned snapshot."""

    def __init__(self, signal_value: str, snapshot_id: UUID):
        self.signal_value = signal_value
        self.snapshot_id = snapshot_id
        super().__init__(
            f"Signal value '{signal_value}' does not exist in snapshot {snapshot_id}"
        )


class SignalTypeNotAllowedError(Exception):
    """A question probes a signal whose type is not in the stage's include_types."""

    def __init__(self, signal_value: str, signal_type: str, allowed_types: list[str]):
        self.signal_value = signal_value
        self.signal_type = signal_type
        self.allowed_types = allowed_types
        super().__init__(
            f"Signal '{signal_value}' has type '{signal_type}' which is not in "
            f"this stage's allowed types {allowed_types}"
        )


class ReorderMismatchError(Exception):
    """The set of question_ids in a reorder request doesn't match the bank's
    current questions.

    Raised by service.reorder_questions when the incoming list is a strict
    subset / superset / disjoint set relative to the existing bank.
    """

    def __init__(self, bank_id: UUID, expected: set[UUID], received: set[UUID]):
        self.bank_id = bank_id
        self.expected = expected
        self.received = received
        super().__init__(
            f"Reorder list for bank {bank_id} must contain exactly the "
            f"existing question IDs"
        )


class ReorderDuplicateError(Exception):
    """The reorder list contains duplicate question IDs."""

    def __init__(self, bank_id: UUID):
        self.bank_id = bank_id
        super().__init__(
            f"Reorder list for bank {bank_id} contains duplicate question IDs"
        )
