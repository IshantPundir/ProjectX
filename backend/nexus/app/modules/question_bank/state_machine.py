"""Per-bank state machine for question generation.

States: draft → generating → self_reviewing → reviewing → confirmed
                  ↑                ↓ ↓
                  └────────────────┘ failed (with error)

`self_reviewing` can restart to `generating` (retry recovery — see
`transition_to_generating` docstring). Transitions are enforced by explicit
helpers. The service layer calls these rather than mutating bank.status directly.
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
BankStatus = Literal["draft", "generating", "self_reviewing", "reviewing", "confirmed", "failed"]

# Legal transitions. Each value is the set of statuses the left-hand state can move to.
# NOTE: auto-revert (confirmed → reviewing on edit) is a separate helper because
# it's triggered by data mutations, not explicit state transitions.
# NOTE: generating → reviewing is intentionally absent. The AI self-critic phase
# (self_reviewing) is a permanent part of generation; the direct edge is unreachable.
LEGAL: dict[BankStatus, set[BankStatus]] = {
    "draft": {"generating", "reviewing", "failed"},
    "generating": {"self_reviewing", "failed"},
    "self_reviewing": {"reviewing", "failed", "generating"},
    "reviewing": {"generating", "confirmed"},
    "confirmed": {"generating", "reviewing"},
    "failed": {"generating"},
}


def _now_utc() -> datetime:
    return datetime.now(UTC)


def transition_to_generating(bank: StageQuestionBank) -> None:
    """draft | reviewing | confirmed | failed | self_reviewing → generating.

    The `self_reviewing` source is legal so that a Dramatiq retry (which
    re-enters Phase A of `_generate_one_bank`) can recover cleanly when the
    worker crashed after the `self_reviewing` commit but before the final
    `reviewing` commit — without stranding the bank in `self_reviewing` forever.

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


def transition_to_self_reviewing(bank: StageQuestionBank) -> None:
    """generating → self_reviewing (the bank enters the AI self-critic phase).

    Raises IllegalTransitionError on any other source state (defensive).
    """
    if bank.status not in LEGAL or "self_reviewing" not in LEGAL[bank.status]:
        raise IllegalTransitionError(
            from_state=bank.status, to_state="self_reviewing"
        )
    bank.status = "self_reviewing"
    bank.updated_at = _now_utc()


def transition_to_reviewing_after_critic(
    bank: StageQuestionBank, *, user_id: UUID
) -> None:
    """self_reviewing → reviewing on critic completion (success OR fallback).

    Caller-bug guard, not a user-facing error (survives `python -O`).
    """
    if bank.status != "self_reviewing":
        raise RuntimeError(
            f"transition_to_reviewing_after_critic requires "
            f"status='self_reviewing', got {bank.status!r}"
        )
    bank.status = "reviewing"
    bank.generated_at = _now_utc()
    bank.generated_by = user_id
    bank.updated_at = _now_utc()


def transition_to_failed(bank: StageQuestionBank, *, error: str) -> None:
    """generating | self_reviewing → failed with error message.

    NOTE: caller-bug guard (asserts are stripped under `python -O` — explicit
    raise so invalid-source bugs are never silently swallowed).
    """
    if bank.status not in ("generating", "self_reviewing"):
        raise RuntimeError(
            f"transition_to_failed requires status in "
            f"('generating','self_reviewing'), got {bank.status!r}"
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
