"""Per-bank state machine for question generation.

States: draft → generating → reviewing → confirmed
               ↓
            failed (with error)

Transitions are enforced by explicit helpers. The service layer calls these
rather than mutating bank.status directly.
"""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Literal
from uuid import UUID

from app.modules.question_bank.models import StageQuestionBank
from app.modules.question_bank.errors import (
    BankAlreadyGeneratingError,
    BankNotInReviewingError,
    IllegalTransitionError,
)

# Canonical BankStatus. schemas.py imports this and re-exports it so both
# layers stay in sync (B8 consolidation).
BankStatus = Literal["draft", "generating", "reviewing", "confirmed", "failed"]

# Legal transitions. Each value is the set of statuses the left-hand state can move to.
# NOTE: auto-revert (confirmed → reviewing on edit) is a separate helper because
# it's triggered by data mutations, not explicit state transitions.
LEGAL: dict[BankStatus, set[BankStatus]] = {
    "draft": {"generating", "reviewing", "failed"},
    "generating": {"reviewing", "failed"},
    "reviewing": {"generating", "confirmed"},
    "confirmed": {"generating", "reviewing"},
    "failed": {"generating"},
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def transition_to_generating(bank: StageQuestionBank) -> None:
    """draft | reviewing | confirmed | failed → generating.

    Raises:
      BankAlreadyGeneratingError — the bank is already in 'generating'.
        This is the common double-trigger case and carries a specific
        user-facing message.
      IllegalTransitionError — any OTHER illegal source state (defensive:
        should be unreachable if LEGAL is kept in sync with the diagram).
    """
    if bank.status == "generating":
        raise BankAlreadyGeneratingError(bank_id=bank.id)
    if bank.status not in LEGAL or "generating" not in LEGAL[bank.status]:
        raise IllegalTransitionError(
            from_state=bank.status, to_state="generating"
        )
    bank.status = "generating"
    bank.generation_error = None
    bank.updated_at = _now_utc()


def transition_to_reviewing_after_generation(bank: StageQuestionBank, *, user_id: UUID) -> None:
    """generating → reviewing on LLM success.

    NOTE: this is a caller-bug guard, not a user-facing error. Replaced
    `assert` with an explicit raise so the check survives `python -O`
    (which strips assertions and would silently mutate bank state
    through an invalid source).
    """
    if bank.status != "generating":
        raise RuntimeError(
            f"transition_to_reviewing_after_generation requires "
            f"status='generating', got {bank.status!r}"
        )
    bank.status = "reviewing"
    bank.generated_at = _now_utc()
    bank.generated_by = user_id
    bank.updated_at = _now_utc()


def transition_to_failed(bank: StageQuestionBank, *, error: str) -> None:
    """generating → failed with error message.

    NOTE: caller-bug guard (same reasoning as
    transition_to_reviewing_after_generation — asserts are stripped
    under `python -O`).
    """
    if bank.status != "generating":
        raise RuntimeError(
            f"transition_to_failed requires status='generating', "
            f"got {bank.status!r}"
        )
    bank.status = "failed"
    bank.generation_error = error
    bank.updated_at = _now_utc()


def transition_to_confirmed(bank: StageQuestionBank, *, user_id: UUID) -> None:
    """reviewing → confirmed. Caller MUST run coverage + budget checks first."""
    if bank.status != "reviewing":
        raise BankNotInReviewingError(bank_id=bank.id, current_status=bank.status)
    bank.status = "confirmed"
    bank.confirmed_at = _now_utc()
    bank.confirmed_by = user_id
    bank.updated_at = _now_utc()


def auto_revert_on_edit(bank: StageQuestionBank) -> bool:
    """Called after any data mutation on a bank's questions.

    - confirmed → reviewing (clears confirmed_at / confirmed_by)
    - draft → reviewing (first recruiter content)
    - everything else → no change

    Returns True if the bank status changed.
    """
    if bank.status == "confirmed":
        bank.status = "reviewing"
        bank.confirmed_at = None
        bank.confirmed_by = None
        bank.updated_at = _now_utc()
        return True
    if bank.status == "draft":
        bank.status = "reviewing"
        bank.updated_at = _now_utc()
        return True
    return False
