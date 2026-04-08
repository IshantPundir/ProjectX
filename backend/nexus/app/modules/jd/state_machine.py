"""Single source of truth for job_posting.status transitions.

Every code path that mutates job_posting.status MUST go through transition()
in this module — including the Dramatiq actor.

LEGAL_TRANSITIONS is the canonical set. New states (2B's signals_confirmed,
2C's template_draft etc.) are added here and the corresponding 409 message
mapping is added in app/main.py's exception handler."""

from typing import Final
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

# IllegalTransitionError is defined in app/modules/jd/errors.py — exception
# handlers and other modules can import it without pulling in the state machine.
from app.modules.jd.errors import IllegalTransitionError

logger = structlog.get_logger()


LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},  # retry
    "signals_extracted": set(),                            # terminal in 2A
    # Future states added here as phases land:
    # "signals_confirmed", "template_generating", ...
}


def is_legal_transition(from_state: str, to_state: str) -> bool:
    """Pure function — no DB access. Useful for unit tests and dispatch logic."""
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


async def transition(
    db: AsyncSession,
    job,  # JobPosting — typed loosely to avoid circular imports
    *,
    to_state: str,
    actor_id: UUID | None,
    correlation_id: str,
) -> None:
    """Atomically update job.status and write an audit_log row.

    Caller is responsible for db.commit() / rollback. This function only
    flushes the model change — the outer transaction decides whether it
    persists.

    The audit call uses app.modules.audit.service.log_event(), which requires
    tenant_id. We pull it directly from job.tenant_id so callers do not need
    to thread it through separately.

    actor_email is not available in the async actor / service context where
    this helper is typically called, so it is passed as None. The actor_id
    is sufficient for human-review forensics.

    Raises:
        IllegalTransitionError: if the transition is not in LEGAL_TRANSITIONS.
    """
    from_state = job.status
    if not is_legal_transition(from_state, to_state):
        raise IllegalTransitionError(from_state, to_state)

    job.status = to_state

    # Lazy import to avoid circular dependency between jd and audit modules.
    from app.modules.audit.service import log_event

    await log_event(
        db,
        tenant_id=job.tenant_id,
        actor_id=actor_id,
        actor_email=None,
        action="job_posting.status_changed",
        resource="job_posting",
        resource_id=job.id,
        payload={
            "from": from_state,
            "to": to_state,
            "correlation_id": correlation_id,
        },
    )

    logger.info(
        "jd.state_machine.transition",
        job_posting_id=str(job.id),
        from_state=from_state,
        to_state=to_state,
        correlation_id=correlation_id,
    )
