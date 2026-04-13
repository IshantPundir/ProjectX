"""Custom exceptions raised by the question_bank service.

Each exception is mapped to an HTTP response in app/main.py via FastAPI
exception handlers. Exceptions carry structured data so the handlers can
produce specific error messages.
"""

from __future__ import annotations

from uuid import UUID


class BankNotFoundError(Exception):
    """The requested bank does not exist (or is invisible due to RLS)."""

    def __init__(self, bank_id: UUID | None = None, stage_id: UUID | None = None):
        self.bank_id = bank_id
        self.stage_id = stage_id
        super().__init__(f"Bank not found (bank_id={bank_id}, stage_id={stage_id})")


class QuestionNotFoundError(Exception):
    """The requested question does not exist."""

    def __init__(self, question_id: UUID):
        self.question_id = question_id
        super().__init__(f"Question not found: {question_id}")


class BankAlreadyGeneratingError(Exception):
    """Generation was triggered while another generation was already in progress."""

    def __init__(self, bank_id: UUID):
        self.bank_id = bank_id
        super().__init__(f"Bank {bank_id} is already in 'generating' state")


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


class DurationBudgetOutOfRangeError(Exception):
    """Sum of estimated_minutes is outside the 50–150% range at confirm time."""

    def __init__(self, bank_id: UUID, total_minutes: float, stage_minutes: int):
        self.bank_id = bank_id
        self.total_minutes = total_minutes
        self.stage_minutes = stage_minutes
        self.min_allowed = round(stage_minutes * 0.5, 1)
        self.max_allowed = round(stage_minutes * 1.5, 1)
        super().__init__(
            f"Question time budget ({total_minutes} min) is outside the allowed "
            f"range for this {stage_minutes}-minute stage "
            f"({self.min_allowed}–{self.max_allowed} min)"
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


class StarterNotSupportedError(Exception):
    """Placeholder for unsupported generation actions."""

    pass
