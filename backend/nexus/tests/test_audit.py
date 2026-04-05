"""Tests for audit log helper."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.modules.audit.actions import ORG_UNIT_CREATED, USER_INVITED
from app.modules.audit.service import log_event
from tests.conftest import create_test_client, create_test_user


@pytest.mark.asyncio
async def test_log_event_inserts_row_with_correct_fields(db: AsyncSession):
    """log_event should insert an AuditLog row with all provided fields."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    resource_id = uuid.uuid4()
    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=USER_INVITED,
        resource="user_invite",
        resource_id=resource_id,
        payload={"invited_email": "new@test.com"},
        ip_address="127.0.0.1",
    )

    result = await db.execute(select(AuditLog).where(AuditLog.tenant_id == client.id))
    row = result.scalar_one()

    assert row.actor_id == user.id
    assert row.actor_email == user.email
    assert row.action == "user.invited"
    assert row.resource == "user_invite"
    assert row.resource_id == resource_id
    assert row.payload == {"invited_email": "new@test.com"}
    assert row.ip_address == "127.0.0.1"


@pytest.mark.asyncio
async def test_log_event_does_not_raise_on_failure(db: AsyncSession):
    """log_event must swallow exceptions and log them, never re-raise."""
    await create_test_client(db)

    fake_tenant = uuid.uuid4()

    # This should NOT raise
    await log_event(
        db,
        tenant_id=fake_tenant,
        actor_id=None,
        actor_email=None,
        action=USER_INVITED,
        resource="user_invite",
    )

    result = await db.execute(select(AuditLog).where(AuditLog.tenant_id == fake_tenant))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_log_event_action_and_resource_correct_for_multiple_types(db: AsyncSession):
    """Verify action and resource strings for two different action types."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=USER_INVITED,
        resource="user_invite",
    )

    await log_event(
        db,
        tenant_id=client.id,
        actor_id=user.id,
        actor_email=user.email,
        action=ORG_UNIT_CREATED,
        resource="org_unit",
    )

    result = await db.execute(
        select(AuditLog).where(AuditLog.tenant_id == client.id).order_by(AuditLog.created_at.asc())
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    assert rows[0].action == "user.invited"
    assert rows[0].resource == "user_invite"
    assert rows[1].action == "org_unit.created"
    assert rows[1].resource == "org_unit"
