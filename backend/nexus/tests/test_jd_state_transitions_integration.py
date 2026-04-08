"""Integration tests for state_machine.transition() against a real DB.
Verifies that transitions write audit_log rows and respect legality."""

import pytest
from sqlalchemy import select

from app.models import AuditLog, JobPosting
from app.modules.jd.errors import IllegalTransitionError
from app.modules.jd.state_machine import transition
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


async def _make_job(db, status="draft"):
    """Helper: build a tenant + user + minimal org unit + job in the requested
    status. Returns the (tenant, user, job) tuple."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    unit = await create_test_org_unit(db, tenant.id)
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=unit.id,
        title="T",
        description_raw="R" * 60,
        created_by=user.id,
        status=status,
    )
    db.add(job)
    await db.flush()
    return tenant, user, job


@pytest.mark.asyncio
async def test_draft_to_extracting_writes_audit_row(db):
    tenant, user, job = await _make_job(db, status="draft")

    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.id,
        correlation_id="corr-1",
    )
    await db.flush()

    assert job.status == "signals_extracting"

    audit = await db.execute(
        select(AuditLog).where(AuditLog.resource_id == job.id)
    )
    rows = list(audit.scalars().all())
    assert len(rows) == 1
    assert rows[0].action == "job_posting.status_changed"
    # Verify the payload carries from/to/correlation_id
    payload = rows[0].payload
    assert payload is not None
    assert payload.get("from") == "draft"
    assert payload.get("to") == "signals_extracting"
    assert payload.get("correlation_id") == "corr-1"


@pytest.mark.asyncio
async def test_illegal_transition_raises(db):
    _, user, job = await _make_job(db, status="draft")
    with pytest.raises(IllegalTransitionError):
        await transition(
            db, job,
            to_state="signals_extracted",
            actor_id=user.id,
            correlation_id="corr-illegal",
        )


@pytest.mark.asyncio
async def test_retry_from_failed_legal(db):
    _, user, job = await _make_job(db, status="signals_extraction_failed")
    await transition(
        db, job,
        to_state="signals_extracting",
        actor_id=user.id,
        correlation_id="corr-retry",
    )
    await db.flush()
    assert job.status == "signals_extracting"


@pytest.mark.asyncio
async def test_extracting_to_failed_writes_audit(db):
    """Failure path also writes an audit row."""
    _, user, job = await _make_job(db, status="signals_extracting")
    await transition(
        db, job,
        to_state="signals_extraction_failed",
        actor_id=user.id,
        correlation_id="corr-fail",
    )
    await db.flush()
    assert job.status == "signals_extraction_failed"

    audit = await db.execute(
        select(AuditLog).where(AuditLog.resource_id == job.id)
    )
    rows = list(audit.scalars().all())
    assert len(rows) == 1
    assert rows[0].payload.get("to") == "signals_extraction_failed"
