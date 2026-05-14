"""_unblock_pending_jobs_for_org_unit: transition every
job_postings row in ``blocked_pending_client_setup`` state under one
org_unit to ``draft`` and emit one ``jd.unblocked_by_profile_completion``
audit row per JD.

Test-environment choice: Option B (per Task 9 / Task 16 decision).
Uses the standard ``db`` fixture from ``tests/conftest.py`` for per-test
connection-level transaction rollback, rather than the plan's
``async_session_factory`` (which would commit rows to the dev DB).
The SUT (``_unblock_pending_jobs_for_org_unit``) takes an ``AsyncSession``
— the fixture binding is purely a test-side choice.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog
from app.modules.jd.models import JobPosting
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


@pytest.mark.asyncio
async def test_unblock_pending_jobs_for_org_unit(db):
    """Two blocked JDs under org_unit_a flip to draft (and emit one audit
    row each); an unrelated draft JD under org_unit_b is untouched.
    """
    from app.modules.org_units.service import _unblock_pending_jobs_for_org_unit

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)

    org_unit_a = await create_test_org_unit(
        db,
        tenant.id,
        name="Client A",
        unit_type="client_account",
    )
    org_unit_b = await create_test_org_unit(
        db,
        tenant.id,
        name="Client B",
        unit_type="client_account",
    )

    # Two blocked JDs under org_unit_a
    blocked_1 = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit_a.id,
        title="Senior Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="blocked_pending_client_setup",
    )
    blocked_2 = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit_a.id,
        title="Staff Engineer",
        description_raw="R" * 60,
        created_by=user.id,
        status="blocked_pending_client_setup",
    )
    # Unrelated draft JD under org_unit_b
    other = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit_b.id,
        title="PM",
        description_raw="R" * 60,
        created_by=user.id,
        status="draft",
    )
    db.add_all([blocked_1, blocked_2, other])
    await db.flush()

    unblocked_ids = await _unblock_pending_jobs_for_org_unit(
        db, org_unit_a.id, tenant.id
    )

    assert sorted(unblocked_ids) == sorted([str(blocked_1.id), str(blocked_2.id)])

    # Status assertions: the two blocked rows are now draft, the unrelated
    # job is still draft (untouched).
    await db.refresh(blocked_1)
    await db.refresh(blocked_2)
    await db.refresh(other)
    assert blocked_1.status == "draft"
    assert blocked_2.status == "draft"
    assert other.status == "draft"

    # Audit rows: one per unblocked JD.
    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.action == "jd.unblocked_by_profile_completion",
                AuditLog.tenant_id == tenant.id,
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 2
    audited_ids = {str(r.resource_id) for r in audit_rows}
    assert audited_ids == {str(blocked_1.id), str(blocked_2.id)}
    for r in audit_rows:
        assert r.resource == "job_posting"
        assert r.actor_id is None
        assert r.actor_email == "system"
        assert r.payload == {"org_unit_id": str(org_unit_a.id)}
