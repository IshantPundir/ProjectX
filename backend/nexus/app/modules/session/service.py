"""Session service — orchestration layer.

This task fills:
  - create_session       — insert sessions row (state=created)
  - mint_token           — insert candidate_session_tokens row + return JWT
  - supersede_token      — atomic SET superseded_at on prior row, link to successor

Later tasks extend this file with pre-check/consent/OTP/start/list functions.

Rules (per Phase 3B lessons-learned):
  * Services flush only — never commit. Session factories auto-commit on context exit.
  * `log_event(db, *, tenant_id=, actor_id=, actor_email=, action=, resource=, resource_id=, payload=)`
  * `user.user.id` / `user.user.email` (never `user.user_id`)
"""
from __future__ import annotations

import uuid
from datetime import datetime, UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CandidateJobAssignment,
    CandidateSessionToken,
    JobPipelineStage,
    Session,
)
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.auth.service import create_candidate_token


async def create_session(
    db: AsyncSession,
    *,
    assignment: CandidateJobAssignment,
    stage: JobPipelineStage,
    otp_required: bool,
    user: UserContext,
) -> Session:
    """Insert a sessions row at state='created'.

    Caller (scheduler.send_invite) provides the already-loaded assignment + stage
    so this function does not re-query. `otp_required` is the *final* flag —
    callers are responsible for applying the stage-default/invite-override
    resolution before calling here.
    """
    sess = Session(
        tenant_id=assignment.tenant_id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        otp_required=otp_required,
        created_by=user.user.id,
    )
    db.add(sess)
    await db.flush()
    return sess


async def mint_token(
    db: AsyncSession,
    *,
    session: Session,
    candidate_id: UUID,
) -> tuple[str, CandidateSessionToken]:
    """Mint a candidate JWT + insert the matching candidate_session_tokens row.

    Returns (token_str, token_row). The caller is responsible for any
    state-machine / audit logging.
    """
    jti = uuid.uuid4()
    token_str, expires_at = create_candidate_token(
        jti=jti,
        candidate_id=candidate_id,
        session_id=session.id,
        tenant_id=session.tenant_id,
    )
    row = CandidateSessionToken(
        jti=jti,
        tenant_id=session.tenant_id,
        session_id=session.id,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return token_str, row


async def supersede_token(
    db: AsyncSession,
    *,
    prior: CandidateSessionToken,
    successor: CandidateSessionToken,
) -> None:
    """Mark `prior` as superseded by `successor`. Caller flushes.

    Idempotent — if prior.superseded_at is already set, leaves it alone.
    """
    if prior.superseded_at is not None:
        return
    prior.superseded_at = datetime.now(UTC)
    prior.superseded_by = successor.jti
    await db.flush()
