"""Tests for extract_and_enhance_jd actor — happy path + failure transitions.

The OpenAI client is mocked. Tests exercise the full DB + state machine
integration but never hit the network."""

from unittest.mock import AsyncMock, MagicMock

import openai
import pytest
from sqlalchemy import func, select

from app.ai.schemas import EnrichmentOutput, ExtractedSignals, SignalExtractionOutput, SignalItemV2
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.actors import _run_enrichment, _run_signal_extraction
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


@pytest.mark.asyncio
async def test_actor_happy_path_persists_snapshot(db, monkeypatch):
    """Happy path: two-phase enrichment + signal extraction persists a snapshot.

    Mocks two LLM calls (enrichment then signal extraction) and asserts that
    after both phases the job status is 'signals_extracted', description_enriched
    is set, and a signal snapshot with 5 signals at version 1 is written.
    """
    tenant, user, job = await _make_extracting_job(db)

    enrichment = _fake_enrichment_output()
    signals = _fake_signal_extraction_output()

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=[enrichment, signals])
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    # Phase 1
    await _run_enrichment(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="corr-happy",
        retries_so_far=0,
    )
    await db.commit()

    # Phase 2
    await _run_signal_extraction(
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
    assert len(snap.signals) == 5


@pytest.mark.asyncio
async def test_actor_final_retry_failure_sanitizes(db, monkeypatch):
    """Final retry (retries_so_far=2): a failing phase-1 LLM call sets
    enrichment_status='failed', transitions main status to
    'signals_extraction_failed', and sanitizes the error (no API keys leaked).
    """
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
        await _run_enrichment(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-fail",
            retries_so_far=2,  # final retry
        )
    await db.flush()
    await db.refresh(job)
    assert job.enrichment_status == "failed"
    assert job.status == "signals_extraction_failed"
    assert job.status_error is not None
    assert "sk-abc" not in job.status_error
    assert "rate-limiting" in job.status_error.lower()


@pytest.mark.asyncio
async def test_actor_intermediate_retry_does_not_flip_state(db, monkeypatch):
    """Intermediate retry (retries_so_far=0): a failing phase-1 LLM call raises
    so Dramatiq retries, without committing any failed state to the DB.
    """
    tenant, user, job = await _make_extracting_job(db)

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        side_effect=openai.APITimeoutError("timeout")
    )
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: fake_client)

    with pytest.raises(openai.APITimeoutError):
        await _run_enrichment(
            db,
            job_posting_id=str(job.id),
            tenant_id=str(tenant.id),
            correlation_id="corr-intermediate",
            retries_so_far=0,  # not final
        )
    await db.refresh(job)
    assert job.status == "signals_extracting"  # unchanged
    assert job.status_error is None
    assert job.enrichment_status == "idle"


# --- Phase 1 (two-phase split) tests ----------------------------------------


def _fake_enrichment_output() -> EnrichmentOutput:
    """Returns a valid EnrichmentOutput with at least 50 chars."""
    return EnrichmentOutput(
        enriched_jd=(
            "## Header\n"
            "Senior Backend Engineer · Remote · 5+ years experience\n\n"
            "## The Role\n"
            "Build distributed systems for a fintech platform.\n"
        )
    )


def _fake_signal_extraction_output() -> SignalExtractionOutput:
    """Returns a valid SignalExtractionOutput satisfying coverage rules."""
    signals = [
        SignalItemV2(
            value="Python",
            type="competency",
            priority="required",
            weight=3,
            knockout=True,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="distributed systems design",
            type="competency",
            priority="required",
            weight=3,
            knockout=False,
            stage="interview",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="5+ years backend",
            type="experience",
            priority="required",
            weight=2,
            knockout=True,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="BS in CS or equivalent",
            type="credential",
            priority="preferred",
            weight=1,
            knockout=False,
            stage="screen",
            source="ai_extracted",
            inference_basis=None,
        ),
        SignalItemV2(
            value="mentor juniors",
            type="behavioral",
            priority="preferred",
            weight=2,
            knockout=False,
            stage="interview",
            source="ai_inferred",
            inference_basis="Senior title implies mentoring scope",
        ),
    ]
    return SignalExtractionOutput(
        signals=ExtractedSignals(
            signals=signals,
            seniority_level="senior",
            role_summary="Senior backend engineer building distributed systems for a fintech platform.",
        )
    )


@pytest.mark.asyncio
async def test_two_phase_extraction_runs_both_llm_calls_in_order(db, monkeypatch):
    """Phase 1 (enrichment) must complete BEFORE phase 2 (signal extraction).

    The actor calls jd_enrichment then jd_signal_extraction, in that order.
    enrichment_status flips to 'completed' between them; final state lands
    at signals_extracted with description_enriched + snapshot v1 written.
    """
    tenant, user, job = await _make_extracting_job(db)
    await db.flush()

    enrichment = _fake_enrichment_output()
    signals = _fake_signal_extraction_output()

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=[enrichment, signals])
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: mock_client)

    # Phase 1
    await _run_enrichment(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="cid-test",
        retries_so_far=0,
    )
    await db.flush()
    await db.refresh(job)
    assert job.enrichment_status == "completed"
    assert job.description_enriched is not None
    assert job.description_enriched.startswith("## Header")
    # Snapshot has NOT been written yet — phase 2 hasn't run.
    snap_count = await db.scalar(
        select(func.count(JobPostingSignalSnapshot.id)).where(
            JobPostingSignalSnapshot.job_posting_id == job.id
        )
    )
    assert snap_count == 0

    # Phase 2
    await _run_signal_extraction(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id="cid-test",
        retries_so_far=0,
    )
    await db.flush()
    await db.refresh(job)
    assert job.status == "signals_extracted"
    snap = (
        await db.execute(
            select(JobPostingSignalSnapshot).where(
                JobPostingSignalSnapshot.job_posting_id == job.id
            )
        )
    ).scalar_one()
    assert snap.version == 1
    assert len(snap.signals) == 5

    # Two LLM calls happened, in the right order, with the right prompts.
    assert mock_client.chat.completions.create.call_count == 2
    first_call = mock_client.chat.completions.create.call_args_list[0]
    second_call = mock_client.chat.completions.create.call_args_list[1]
    assert first_call.kwargs["response_model"].__name__ == "EnrichmentOutput"
    assert second_call.kwargs["response_model"].__name__ == "SignalExtractionOutput"


@pytest.mark.asyncio
async def test_run_signal_extraction_uses_raw_when_no_enrichment(db, monkeypatch):
    """When phase 1 was skipped (enrichment_status='idle'), phase 2 reads raw JD."""
    tenant, user, job = await _make_extracting_job(db)
    job.description_raw = "RAW_JD_FIXTURE_MARKER " + ("filler content " * 10)
    await db.commit()
    assert job.enrichment_status == "idle"

    signals = _fake_signal_extraction_output()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=signals)
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: mock_client)

    await _run_signal_extraction(
        db, job_posting_id=str(job.id),
        tenant_id=str(tenant.id), correlation_id="cid", retries_so_far=0,
    )

    user_message = mock_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "RAW_JD_FIXTURE_MARKER" in user_message


@pytest.mark.asyncio
async def test_phase_2_reads_enriched_jd_when_phase_1_ran(db, monkeypatch):
    """Signal extraction must use description_enriched as input when phase 1 ran."""
    tenant, user, job = await _make_extracting_job(db)
    # Embed a distinctive marker in description_raw so the negative-control
    # assertion is real: if _run_signal_extraction reads raw instead of enriched,
    # the marker will appear in the LLM call and the `not in` check will fail.
    job.description_raw = "RAW_JD_FIXTURE_MARKER " + ("filler content " * 10)
    await db.commit()
    await db.flush()

    enrichment = _fake_enrichment_output()
    signals = _fake_signal_extraction_output()
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=[enrichment, signals])
    monkeypatch.setattr("app.modules.jd.actors.get_openai_client", lambda: mock_client)

    await _run_enrichment(
        db, job_posting_id=str(job.id),
        tenant_id=str(tenant.id), correlation_id="cid", retries_so_far=0,
    )
    await db.flush()
    await _run_signal_extraction(
        db, job_posting_id=str(job.id),
        tenant_id=str(tenant.id), correlation_id="cid", retries_so_far=0,
    )

    # Phase 2's user message must contain the enriched JD, NOT the raw JD.
    second_call = mock_client.chat.completions.create.call_args_list[1]
    user_message = second_call.kwargs["messages"][1]["content"]
    assert "## Header\nSenior Backend Engineer" in user_message
    # The raw JD marker must NOT appear — if _run_signal_extraction wrongly
    # reads description_raw instead of description_enriched, this will FAIL.
    assert "RAW_JD_FIXTURE_MARKER" not in user_message
