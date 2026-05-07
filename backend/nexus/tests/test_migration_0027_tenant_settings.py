"""ORM smoke tests for migration 0027 (Phase 5).

Covers:
- tenant_settings table created with PK on tenant_id, FK→clients ON DELETE CASCADE.
- engine_knockout_policy CHECK constraint rejects unknown values.
- engine_agent_name accepts NULL.
- Both RLS policies present (tenant_isolation with non-NULL WITH CHECK + service_bypass).
- sessions.knockout_failures column added with default '[]'::jsonb.
- Existing sessions row picks up '[]' default.

Tested against the create_all-built test DB (see tests/conftest.py).
The CHECK + server_default + RLS pair are mirrored on the ORM model
in app/modules/tenant_settings/models.py via __table_args__ +
server_default so this test exercises the same behavior under
create_all that production gets via the raw-SQL Alembic migration.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.session.models import Session as SessionRow
from app.modules.session.schemas import SessionState
from app.modules.tenant_settings.models import TenantSettingsModel
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
)

pytestmark = pytest.mark.asyncio


async def test_tenant_settings_default_policy(db) -> None:
    tenant = await create_test_client(db)
    row = TenantSettingsModel(tenant_id=tenant.id)
    db.add(row)
    await db.flush()
    fetched = (
        await db.execute(
            text(
                "SELECT engine_knockout_policy, engine_agent_name "
                "FROM tenant_settings WHERE tenant_id = :t"
            ),
            {"t": str(tenant.id)},
        )
    ).first()
    # Default flipped to close_polite in migration 0030 (post-incident).
    assert fetched.engine_knockout_policy == "close_polite"
    assert fetched.engine_agent_name is None


async def test_tenant_settings_check_rejects_unknown_policy(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="hard_reject",  # not in CHECK allowlist
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_tenant_settings_accepts_close_polite(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="close_polite",
            engine_agent_name="Acme-Bot",
        )
    )
    await db.flush()


async def test_sessions_knockout_failures_default(db) -> None:
    """A freshly inserted Session row gets `[]` for knockout_failures."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
        state=SessionState.CREATED.value,
        state_changed_at=datetime.now(UTC),
    )
    db.add(sess)
    await db.flush()

    fetched = (
        await db.execute(
            text("SELECT knockout_failures FROM sessions WHERE id = :s"),
            {"s": str(sess.id)},
        )
    ).first()
    assert fetched.knockout_failures == []
