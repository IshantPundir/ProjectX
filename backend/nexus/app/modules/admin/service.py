"""Admin service — client provisioning and management."""

import hashlib
import secrets
import uuid as uuid_mod
from datetime import UTC, datetime

import sqlalchemy
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.audit import actions as audit_actions, log_event
from app.modules.auth import AuthProviderError, UserInvite, get_auth_provider
from app.modules.notifications import render_template, send_email
from app.modules.org_units import Client

logger = structlog.get_logger()


def _client_status(client: Client) -> str:
    """Derived state from the (blocked_at, deleted_at) pair."""
    if client.deleted_at is not None:
        return "deleted"
    if client.blocked_at is not None:
        return "blocked"
    return "active"


class ClientNotFoundError(Exception):
    pass


class InvalidClientStateError(Exception):
    """Raised when a state transition is impossible (e.g. unblock a deleted tenant)."""

    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"Cannot transition client from '{current}' to '{requested}'"
        )


class ConfirmationMismatchError(Exception):
    """Raised when the typed confirmation name does not match
    `client.name` exactly. Mapped to 422 with `code = 'CONFIRMATION_MISMATCH'`
    by the handler in `app/main.py`."""


async def provision_client(
    *,
    db: AsyncSession,
    client_name: str,
    admin_email: str,
    domain: str = "",
    industry: str = "",
    plan: str = "trial",
    admin_identity: str,  # email of the ProjectX admin performing this action
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> tuple[Client, UserInvite, str]:
    """Create a client + invite for the Company Admin.

    Returns (client, invite, raw_token_or_url). The raw_token is passed to the
    email sender and then discarded — never stored.
    """
    # Create client
    client = Client(name=client_name, domain=domain or None, industry=industry or None, plan=plan)
    db.add(client)
    await db.flush()  # get client.id

    # Generate invite token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Create invite
    invite = UserInvite(
        tenant_id=client.id,
        email=admin_email,
        token_hash=token_hash,
        projectx_admin_id=admin_identity,
    )
    db.add(invite)
    await db.flush()  # get invite.id

    # Send invite email
    invite_url = f"{settings.frontend_base_url}/invite?token={raw_token}"

    html = render_template(
        "company_admin_invite.html",
        company_name=client_name,
        invite_url=invite_url,
        expires_hours=72,
    )
    await send_email(
        to=admin_email,
        subject=f"You've been invited to set up {client_name} on ProjectX",
        html=html,
    )

    logger.info(
        "admin.client_provisioned",
        client_id=str(client.id),
        admin_email=admin_email,
    )

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_PROVISIONED,
        resource="client",
        resource_id=client.id,
        payload={"client_name": client_name, "admin_email": admin_email, "plan": plan},
        ip_address=ip_address,
    )

    # Log the invite URL explicitly in dry-run mode so it's easy to copy from terminal
    if settings.notifications_dry_run:
        logger.info("admin.invite_url_dry_run", invite_url=invite_url)

    return client, invite, invite_url if settings.notifications_dry_run else ""


async def list_clients(db: AsyncSession) -> list[dict]:
    """List all companies with their latest invite status."""
    from sqlalchemy import func

    latest_invite = (
        select(
            UserInvite.tenant_id,
            func.max(UserInvite.created_at).label("max_created"),
        )
        .where(UserInvite.projectx_admin_id.isnot(None))
        .group_by(UserInvite.tenant_id)
        .subquery()
    )

    result = await db.execute(
        select(Client, UserInvite)
        .outerjoin(
            latest_invite,
            Client.id == latest_invite.c.tenant_id,
        )
        .outerjoin(
            UserInvite,
            (UserInvite.tenant_id == latest_invite.c.tenant_id)
            & (UserInvite.created_at == latest_invite.c.max_created)
            & (UserInvite.projectx_admin_id.isnot(None)),
        )
        .order_by(Client.created_at.desc())
    )
    rows = result.all()

    return [
        {
            "client_id": str(company.id),
            "client_name": company.name,
            "domain": company.domain,
            "plan": company.plan,
            "onboarding_complete": company.onboarding_complete,
            "admin_email": invite.email if invite else None,
            "invite_status": invite.status if invite else None,
            "created_at": company.created_at.isoformat(),
            "status": _client_status(company),
            "blocked_at": company.blocked_at.isoformat() if company.blocked_at else None,
            "deleted_at": company.deleted_at.isoformat() if company.deleted_at else None,
        }
        for company, invite in rows
    ]


async def _load_client(db: AsyncSession, client_id: uuid_mod.UUID) -> Client:
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        raise ClientNotFoundError()
    return client


async def _purge_auth_users(
    auth_user_ids: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Best-effort bulk delete of Supabase Auth users.

    Returns `(purged, failed)`. `failed` is `[(auth_user_id, reason_str), ...]`.
    Each call is independently try/excepted so one failure does not abort
    the rest. Reuses the provider abstraction so a future Cognito swap
    requires no change here.
    """
    provider = get_auth_provider()
    purged: list[str] = []
    failed: list[tuple[str, str]] = []
    for uid in auth_user_ids:
        try:
            await provider.delete_user(uid)
            purged.append(uid)
        except AuthProviderError as e:
            failed.append((uid, str(e)))
            logger.warning(
                "admin.hard_delete.auth_user_purge_failed",
                auth_user_id=uid,
                error=str(e),
            )
    return purged, failed


async def block_client(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    admin_identity: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> Client:
    """Mark a tenant as blocked. Idempotent: re-blocking refreshes blocked_at.

    Deleted tenants cannot be blocked — that's a no-op state transition.
    """
    client = await _load_client(db, client_id)
    if client.deleted_at is not None:
        raise InvalidClientStateError(current="deleted", requested="blocked")

    client.blocked_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_BLOCKED,
        resource="client",
        resource_id=client.id,
        payload={"client_name": client.name},
        ip_address=ip_address,
    )
    logger.info("admin.client_blocked", client_id=str(client.id))
    return client


async def unblock_client(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    admin_identity: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> Client:
    """Clear `blocked_at`. Deleted tenants cannot be unblocked from this path —
    they need a separate undelete operation that does not exist yet.
    """
    client = await _load_client(db, client_id)
    if client.deleted_at is not None:
        raise InvalidClientStateError(current="deleted", requested="active")

    client.blocked_at = None
    await db.flush()

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_UNBLOCKED,
        resource="client",
        resource_id=client.id,
        payload={"client_name": client.name},
        ip_address=ip_address,
    )
    logger.info("admin.client_unblocked", client_id=str(client.id))
    return client


async def delete_client(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    admin_identity: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> Client:
    """Soft-delete: set `deleted_at` on the client AND cascade-soft-delete
    all rows that would block re-onboarding the same admin email.

    Cascade scope:
      - users:        deleted_at = now, is_active = false
                      (frees `users_auth_user_id_active_uniq` for the same
                      Supabase Auth identity to be re-bound to a fresh
                      tenant)
      - user_invites: any pending → revoked
                      (the deleted tenant's invites must not be claimable
                      after deletion)

    NOT cascaded (preserved for audit + restore):
      - jobs, candidates, sessions, audit_log, org_units, role assignments
      - the underlying Supabase Auth user (left alive — `accept_invite`'s
        find-and-reuse path handles re-onboarding correctly)

    Idempotent (re-running refreshes the timestamps). Restoring a deleted
    tenant requires a direct DB edit — no UI restore path on purpose.
    """
    client = await _load_client(db, client_id)
    now = datetime.now(UTC)
    tenant_uuid_str = str(client.id)

    client.deleted_at = now

    # Cascade: free auth_user_id for re-binding by soft-deleting users.
    # Both `deleted_at` and `is_active` are set so login + auth context
    # filters (each filters on `is_active = TRUE`) reject these rows
    # immediately — no race window where a deleted-tenant user could log
    # in between this UPDATE committing and the rest of the transaction.
    await db.execute(
        sqlalchemy.text(
            "UPDATE public.users "
            "SET deleted_at = :now, is_active = FALSE, updated_at = :now "
            "WHERE tenant_id = :tenant_id AND deleted_at IS NULL"
        ),
        {"now": now, "tenant_id": tenant_uuid_str},
    )

    # Cascade: revoke pending invites so the now-orphan tokens cannot be
    # accepted post-deletion. Already-accepted invites are left as-is
    # (their status='accepted' history is part of the audit trail).
    await db.execute(
        sqlalchemy.text(
            "UPDATE public.user_invites "
            "SET status = 'revoked' "
            "WHERE tenant_id = :tenant_id AND status = 'pending'"
        ),
        {"tenant_id": tenant_uuid_str},
    )

    await db.flush()

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_DELETED,
        resource="client",
        resource_id=client.id,
        payload={"client_name": client.name},
        ip_address=ip_address,
    )
    logger.info("admin.client_deleted", client_id=str(client.id))
    return client


async def hard_delete_client(
    *,
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    admin_identity: str,
    confirmation_name: str,
    actor_id: uuid_mod.UUID | None = None,
    ip_address: str | None = None,
) -> dict:
    """Permanently purge a tenant.

    Preconditions:
      - The client must be in `deleted` state (soft-deleted first).
      - `confirmation_name` must equal `client.name` exactly.

    On success:
      - DB cascade unwinds every tenant-scoped table except `audit_log`.
      - Supabase Auth users are purged best-effort (post-commit; failures
        are logged but do not roll back the DB delete).
      - Returns `{client_id, purged_at, auth_user_ids}`.

    Raises:
      - `ClientNotFoundError` if the client doesn't exist.
      - `InvalidClientStateError` if not in `deleted` state.
      - `ConfirmationMismatchError` if name doesn't match.
    """
    client = await _load_client(db, client_id)

    # State gate: must be soft-deleted.
    current = _client_status(client)
    if current != "deleted":
        raise InvalidClientStateError(current=current, requested="purged")

    # Confirmation gate.
    if confirmation_name != client.name:
        raise ConfirmationMismatchError()

    # Snapshot before the cascade for audit + auth purge.
    auth_user_ids_result = await db.execute(
        sqlalchemy.text(
            "SELECT auth_user_id::text FROM public.users WHERE tenant_id = :tid"
        ),
        {"tid": str(client.id)},
    )
    auth_user_ids = [row[0] for row in auth_user_ids_result.all()]
    snapshot = {"client_name": client.name, "user_count": len(auth_user_ids)}

    # Pre-cascade audit. Written inside the same transaction as the DELETE
    # so either both happen or neither does.
    await log_event(
        db,
        tenant_id=client.id,
        actor_id=actor_id,
        actor_email=admin_identity,
        action=audit_actions.CLIENT_HARD_DELETED,
        resource="client",
        resource_id=client.id,
        payload=snapshot,
        ip_address=ip_address,
    )

    # The cascade. Postgres unwinds every CASCADE-marked FK in dependency
    # order; audit_log rows survive because their FKs were dropped by
    # migration 0023.
    await db.execute(
        sqlalchemy.text("DELETE FROM public.clients WHERE id = :id"),
        {"id": str(client.id)},
    )
    await db.flush()

    purged_at = datetime.now(UTC)
    logger.info(
        "admin.client_hard_deleted",
        client_id=str(client.id),
        client_name=client.name,
        user_count=len(auth_user_ids),
    )

    return {
        "client_id": str(client.id),
        "purged_at": purged_at.isoformat(),
        "auth_user_ids": auth_user_ids,  # router consumes for the auth-purge step
    }
