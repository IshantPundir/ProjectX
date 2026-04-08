"""Tests for extract_and_enhance_jd actor — happy path + failure transitions.

The OpenAI client is mocked. Tests exercise the full DB + state machine
integration but never hit the network."""

from unittest.mock import AsyncMock, MagicMock

import openai
import pytest
from sqlalchemy import select

from app.ai.schemas import ExtractedSignals, ExtractionOutput, SignalItem
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.actors import _run_extraction
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)


_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


async def _make_extracting_job(db):
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE,
    )
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Sr Engineer",
        description_raw="A" * 200,
        status="signals_extracting",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()
    return tenant, user, job


def _fake_extraction_output() -> ExtractionOutput:
    return ExtractionOutput(
        enriched_jd="A" * 80,
        signals=ExtractedSignals(
            required_skills=[
                SignalItem(value="Python", source="ai_extracted", inference_basis=None)
            ],
            preferred_skills=[],
            must_haves=[
                SignalItem(value="5+ years backend", source="ai_extracted", inference_basis=None)
            ],
            good_to_haves=[],
            min_experience_years=5,
            seniority_level="senior",
            role_summary="A senior backend engineer at a Series A fintech. Owns end-to-end.",
        ),
    )


@pytest.mark.asyncio
async def test_actor_happy_path_persists_snapshot(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_fake_extraction_output())
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    await _run_extraction(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="corr-happy",
        retries_so_far=0,
    )
    await db.flush()
    await db.refresh(job)
    assert job.status == "signals_extracted"
    assert job.description_enriched is not None
    assert len(job.description_enriched) >= 50

    snap_result = await db.execute(
        select(JobPostingSignalSnapshot).where(JobPostingSignalSnapshot.job_posting_id == job.id)
    )
    snap = snap_result.scalar_one()
    assert snap.version == 1
    assert snap.seniority_level == "senior"
    assert len(snap.required_skills) == 1


@pytest.mark.asyncio
async def test_actor_final_retry_failure_sanitizes(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    class FakeResponse:
        status_code = 429
        headers = {}
        request = None

    def raise_rate_limit(*a, **k):
        raise openai.RateLimitError(
            "boom with sensitive key sk-abc",
            response=FakeResponse(),
            body=None,
        )

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=raise_rate_limit)
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    with pytest.raises(openai.RateLimitError):
        await _run_extraction(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-fail",
            retries_so_far=2,  # final retry
        )
    await db.flush()
    await db.refresh(job)
    assert job.status == "signals_extraction_failed"
    assert job.status_error is not None
    assert "sk-abc" not in job.status_error
    assert "rate-limiting" in job.status_error.lower()


@pytest.mark.asyncio
async def test_actor_intermediate_retry_does_not_flip_state(db, monkeypatch):
    tenant, user, job = await _make_extracting_job(db)

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=openai.APITimeoutError("timeout")
    )
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    with pytest.raises(openai.APITimeoutError):
        await _run_extraction(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-intermediate",
            retries_so_far=0,  # not final
        )
    await db.refresh(job)
    assert job.status == "signals_extracting"  # unchanged
    assert job.status_error is None
