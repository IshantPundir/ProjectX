"""SSE generator tests — pub/sub fast path (T17) and backstop poll (T18).

T17: An envelope published to job:{id} is yielded by the SSE generator
     within 3 seconds via the pub/sub fast path.

T18: With the fast path broken (subscribe replaced with an infinite sleeper),
     the backstop poll still emits an event when a question is inserted into
     the DB.  The backstop's get_tenant_session is monkeypatched to reuse the
     per-test DB session so the flushed data is visible within the same
     connection-level transaction.

Both tests call _sse_generator directly (no FastAPI Request object needed)
so they exercise the inner fan-in logic in isolation.

Prerequisites for T17:
  - Redis must be reachable (docker-compose ensures this).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import pubsub
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    StageQuestionBank,
)
from app.modules.question_bank import sse
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio

_VALID_PROFILE = {
    "about": "Minimal seed company for SSE tests.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "High bar.",
}


# ---------------------------------------------------------------------------
# Shared seed helper — builds FK chain and returns a StageQuestionBank
# ---------------------------------------------------------------------------


async def _build_seed_bank(db: AsyncSession) -> StageQuestionBank:
    """Build the minimal FK chain and return a flushed StageQuestionBank.

    Chain:
        clients → organizational_units → users
        → job_postings → job_posting_signal_snapshots
        → job_pipeline_instances → job_pipeline_stages
        → stage_question_banks
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    org_unit = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_VALID_PROFILE
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=org_unit.id,
        title="SSE Test Job",
        description_raw="D" * 200,
        description_enriched="Enriched for SSE testing.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[],
        seniority_level="mid",
        role_summary="Minimal snapshot for SSE test.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
    )
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=30,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual",
    )
    db.add(stage)
    await db.flush()

    # Use "generating" as the initial status so the backstop's first
    # observation is non-terminal, ensuring the pipeline.generation_complete
    # check does not fire on the first poll cycle.
    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="generating",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    return bank


# ---------------------------------------------------------------------------
# T17: pub/sub fast path delivers events
# ---------------------------------------------------------------------------


async def test_sse_forwards_pubsub_events(db: AsyncSession, monkeypatch):
    """An envelope published to job:{id} is yielded by the SSE generator
    within 3 seconds via the pub/sub fast path.

    This test uses the REAL Redis transport.
    The backstop poll interval is set to 60s so it does not interfere.
    """
    bank = await _build_seed_bank(db)
    job_id = bank.job_posting_id
    tenant_id = str(bank.tenant_id)

    # Slow the backstop so the fast path is the only realistic delivery path.
    monkeypatch.setattr(sse, "POLL_INTERVAL_SEC", 60.0)

    # Patch get_tenant_session in sse so the backstop (if it ever runs) uses
    # the test DB session rather than the production engine.
    @asynccontextmanager
    async def _fake_tenant_session(_tid: str):
        yield db

    monkeypatch.setattr(sse, "get_tenant_session", _fake_tenant_session)

    received: list[str] = []

    async def consume():
        async for frame in sse._sse_generator(
            tenant_id=tenant_id,
            job_id=job_id,
        ):
            received.append(frame)
            if pubsub.Events.BANK_QUESTION_UPDATED in frame:
                break

    consumer = asyncio.create_task(consume())

    # Give the subscribe coroutine time to connect to Redis before publishing.
    await asyncio.sleep(0.2)

    await pubsub.publish(
        pubsub.job_channel(job_id),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {
            "job_id": str(job_id),
            "bank_id": str(bank.id),
            "mutation": "update",
        },
        correlation_id="test-sse-t17",
    )

    try:
        await asyncio.wait_for(consumer, timeout=3.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail("SSE generator did not forward pub/sub event within 3s")
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any(pubsub.Events.BANK_QUESTION_UPDATED in f for f in received), (
        f"bank.question_updated not in received frames: {received}"
    )
    assert any("test-sse-t17" in f for f in received), (
        "correlation_id 'test-sse-t17' not preserved in SSE frames"
    )
