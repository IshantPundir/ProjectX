"""Audit log helper — single INSERT, never raises."""

import uuid as uuid_mod

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog

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
        # Audit logging must never break a business operation, so we swallow
        # the exception. But we MUST surface it loudly to observability so
        # regressions (e.g., RLS policy mis-configurations, missing INSERT
        # permission on audit_log) do not silently erode the compliance
        # audit trail. Include the full exception via exc_info so Sentry
        # captures the traceback.
        logger.error(
            "audit.log_event_failed",
            action=action,
            resource=resource,
            tenant_id=str(tenant_id),
            actor_id=str(actor_id) if actor_id else None,
            resource_id=str(resource_id) if resource_id else None,
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
