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

    Raised by `validate_mandatory_fits_session` at confirm time as the final
    safety net. Generation-time enforcement uses `BudgetExceededError`
    instead so the retry loop can feed the violation back into the LLM
    context without conflating with confirm-time semantics.
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


class BudgetExceededError(Exception):
    """LLM output violated the generation-time budget contract.

    Two flavours, distinguished by `kind`:
      - `kind="mandatory"` — `mandatory_total` exceeds `duration_minutes`
      - `kind="total"`     — full bank exceeds `duration_minutes + margin_min`

    The error message is fed back into the LLM as a follow-up user message
    on the retry attempt, so it must be self-contained, blameless, and
    actionable. Distinct from `MandatoryOverrunError` (confirm-time path)
    because the generation-time path retries with feedback rather than
    asking a recruiter to fix the bank.
    """

    def __init__(
        self,
        *,
        kind: str,  # "mandatory" | "total"
        observed_minutes: float,
        cap_minutes: float,
        duration_minutes: int,
        margin_min: int,
    ):
        self.kind = kind
        self.observed_minutes = observed_minutes
        self.cap_minutes = cap_minutes
        self.duration_minutes = duration_minutes
        self.margin_min = margin_min
        if kind == "mandatory":
            msg = (
                f"Budget violation (mandatory): mandatory questions sum to "
                f"{observed_minutes:g} min, which exceeds the stage duration "
                f"of {duration_minutes} min. Reduce mandatory time by "
                f"shortening per-question estimated_minutes or demoting "
                f"questions to is_mandatory=false."
            )
        elif kind == "total":
            msg = (
                f"Budget violation (total): all questions combined sum to "
                f"{observed_minutes:g} min, which exceeds the cap of "
                f"{cap_minutes:g} min (stage duration {duration_minutes} min "
                f"+ {margin_min} min optional buffer). Drop the lowest-priority "
                f"optional questions until total fits."
            )
        else:
            msg = f"Budget violation ({kind}): {observed_minutes:g} > {cap_minutes:g}"
        super().__init__(msg)


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
