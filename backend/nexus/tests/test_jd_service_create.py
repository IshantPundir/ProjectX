"""Tests for JD service create_job_posting.

Per the unified job-creation flow (docs/superpowers/specs/
2026-05-14-unified-job-creation-flow-design.md), create_job_posting() lands
the job in 'draft' with no side-effects: no state transition, no actor
enqueue, no profile check. The profile gate moved to the explicit
/enrich and /extract-signals endpoints — exercised in test_jd_router.py.
"""

import pytest

from app.modules.jd.service import create_job_posting
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_create_job_posting_lands_in_draft(db):
    """Service inserts a row in 'draft' with no transition and no actor
    enqueue. The recruiter advances the lifecycle from /jobs/{id}."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(db, tenant.id, unit_type="company")
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id,
    )
    await db.flush()

    job = await create_job_posting(
        db,
        tenant_id=tenant.id,
        created_by=user.id,
        org_unit_id=team.id,
        title="Sr. Integration Engineer",
        description_raw="A" * 200,
        project_scope_raw=None,
        target_headcount=1,
        deadline=None,
        correlation_id="test-corr-1",
    )
    await db.flush()

    assert job.status == "draft"
    assert job.title == "Sr. Integration Engineer"
    assert job.description_enriched is None
    assert job.enrichment_status == "idle"


@pytest.mark.asyncio
async def test_create_job_posting_allows_empty_raw_jd(db):
    """The recruiter is expected to fill description_raw on /jobs/{id} after
    create, so the service accepts an empty value."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(db, tenant.id, unit_type="company")
    await db.flush()

    job = await create_job_posting(
        db,
        tenant_id=tenant.id,
        created_by=user.id,
        org_unit_id=company.id,
        title="Untitled placeholder",
        correlation_id="test-corr-2",
    )
    await db.flush()

    assert job.status == "draft"
    assert job.description_raw == ""


@pytest.mark.asyncio
async def test_create_job_posting_no_profile_check(db):
    """Profile completion is not checked at create time — the gate moved to
    /enrich and /extract-signals. A draft job can sit on a unit with no
    completed ancestor profile; the recruiter sees a 422 only when they
    try to run something that needs the profile."""
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    # No company-root profile anywhere up the chain.
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    job = await create_job_posting(
        db,
        tenant_id=tenant.id,
        created_by=user.id,
        org_unit_id=division.id,
        title="Test Role",
        description_raw="A" * 200,
        project_scope_raw=None,
        target_headcount=None,
        deadline=None,
        correlation_id="test-corr-3",
    )
    await db.flush()

    assert job.status == "draft"
