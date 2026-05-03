"""tenant_settings service layer.

`get_tenant_settings(db, tenant_id)` is the single read path. It returns
the row's values when present, or the schema's defaults if the tenant
doesn't have a row yet (lazy-default pattern, P5-Q4). No backfill is
performed; when the future recruiter-UI editing path ships, the first
edit creates the row via UPSERT.

Note on `updated_at`: the tenant_settings table has a `server_default
= now()` for `updated_at` but NO BEFORE UPDATE trigger. Phase 5 is
read-only — this service has no UPDATE path, so `updated_at` will not
change here. When the future recruiter-UI editing path ships, that
path is responsible for setting `updated_at = now()` on every UPDATE
(or alternatively adding a `BEFORE UPDATE` trigger in a follow-up
migration).
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenant_settings.models import TenantSettingsModel
from app.modules.tenant_settings.schemas import TenantSettings


def DEFAULT_TENANT_SETTINGS(tenant_id: UUID) -> TenantSettings:  # noqa: N802
    """Build the default TenantSettings for a tenant with no row.

    Mirrors the DB-level defaults in migration 0027.
    """
    return TenantSettings(tenant_id=tenant_id)


async def get_tenant_settings(db: AsyncSession, tenant_id: UUID) -> TenantSettings:
    """Return the tenant's settings, falling back to defaults if no row.

    Caller may be on a tenant-scoped or bypass-RLS session; both paths
    work because RLS is enforced by the policies, not by the helper.
    On a tenant-scoped session reading a different tenant_id, the
    policy filter returns 0 rows and the helper falls back to defaults
    keyed on the **passed-in** ``tenant_id`` — same as if the row had
    never been written. Callers are expected to pass the requesting
    tenant's id; under a tenant-scoped session the RLS policy makes
    any other id behave as 'no row.'
    """
    row = (
        await db.execute(
            select(TenantSettingsModel).where(TenantSettingsModel.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return DEFAULT_TENANT_SETTINGS(tenant_id)
    return TenantSettings(
        tenant_id=row.tenant_id,
        engine_knockout_policy=row.engine_knockout_policy,
        engine_agent_name=row.engine_agent_name,
    )
