"""Audit log helper — single INSERT, never raises."""

import uuid as uuid_mod

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = structlog.get_logger()


async def log_event(
    db: AsyncSession,
    *,
    tenant_id: uuid_mod.UUID,
    actor_id: uuid_mod.UUID | None,
    actor_email: str | None,
    action: str,
    resource: str,
    resource_id: uuid_mod.UUID | None = None,
    payload: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Append one audit event. Always call within an existing transaction.

    This function does NOT commit. If it fails, it logs the error and
    returns silently. Audit logging must never break a business operation.
    """
    try:
        async with db.begin_nested():
            entry = AuditLog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                resource=resource,
                resource_id=resource_id,
                payload=payload,
                ip_address=ip_address,
            )
            db.add(entry)
            await db.flush()
    except Exception as exc:
        logger.error(
            "audit.log_event_failed",
            action=action,
            resource=resource,
            error=str(exc),
        )
