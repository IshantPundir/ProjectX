"""Tests for JD service create_job_posting — happy path + profile-gate failure."""

import pytest

from app.modules.jd.errors import CompanyProfileIncompleteError
from app.modules.jd.service import create_job_posting
from tests.conftest import create_test_client, create_test_org_unit, create_test_user


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


@pytest.mark.asyncio
async def test_create_job_posting_happy_path(db, monkeypatch):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    team = await create_test_org_unit(
        db, tenant.id, unit_type="team", parent_unit_id=company.id,
    )
    await db.flush()

    # Stub Dramatiq dispatch so the test doesn't actually enqueue
    dispatched: list = []
    def fake_send(*args, **kwargs):
        dispatched.append((args, kwargs))
    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        fake_send,
    )

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

    assert job.status == "signals_extracting"
    assert job.title == "Sr. Integration Engineer"
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_create_job_posting_blocks_without_profile(db, monkeypatch):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    # division has NO company_profile and no ancestor with one
    division = await create_test_org_unit(db, tenant.id, unit_type="division")
    await db.flush()

    monkeypatch.setattr(
        "app.modules.jd.actors.extract_and_enhance_jd.send",
        lambda *a, **k: None,
    )

    with pytest.raises(CompanyProfileIncompleteError):
        await create_job_posting(
            db,
            tenant_id=tenant.id,
            created_by=user.id,
            org_unit_id=division.id,
            title="Test Role",
            description_raw="A" * 200,
            project_scope_raw=None,
            target_headcount=None,
            deadline=None,
            correlation_id="test-corr-2",
        )
