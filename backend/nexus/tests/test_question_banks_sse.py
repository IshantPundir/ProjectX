"""SSE generator tests — pub/sub fast path (T17), backstop poll (T18), E2E (T19).

T17: An envelope published to job:{id} is yielded by the SSE generator
     within 3 seconds via the pub/sub fast path.

T18: With the fast path broken (subscribe replaced with an infinite sleeper),
     the backstop poll still emits an event when a question is inserted into
     the DB.  The backstop's get_tenant_session is monkeypatched to reuse the
     per-test DB session so the flushed data is visible within the same
     connection-level transaction.

T19: Full end-to-end — HTTP PATCH fires BackgroundTasks which publishes to real
     Redis; the SSE subscriber receives the event within fast-path latency.
     No pubsub monkeypatching; exercises every seam introduced in Batch 2.

Both T17/T18 call _sse_generator directly (no FastAPI Request object needed)
so they exercise the inner fan-in logic in isolation.

Prerequisites for T17/T19:
  - Redis must be reachable (docker-compose ensures this).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch

from app import pubsub
from app.database import get_tenant_db
from app.main import app
from app.modules.jd.models import (
    JobPosting,
    JobPostingSignalSnapshot,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
)
from app.modules.question_bank.models import StageQuestionBank
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.modules.question_bank import sse
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio

_VALID_PROFILE = {
    "about": "Minimal seed company for SSE tests.",
    "industry": "Fintech / Financial Services",
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
        db, tenant.id, unit_type="company", **_VALID_PROFILE
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
    # Patch both cadences — the gating in _poll_loop picks one based on
    # observed bank statuses, and we want both branches to be slow here.
    monkeypatch.setattr(sse, "POLL_INTERVAL_SEC", 60.0)
    monkeypatch.setattr(sse, "POLL_INTERVAL_IDLE_SEC", 60.0)

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


# ---------------------------------------------------------------------------
# T18: backstop poll emits when pub/sub is unavailable
# ---------------------------------------------------------------------------


async def test_sse_backstop_emits_when_pubsub_unavailable(db: AsyncSession, monkeypatch):
    """With the pub/sub fast path replaced by an infinite sleeper, the backstop
    poll still emits a bank.question_updated event when a question is INSERTed
    into stage_questions (bumping question_count AND max_updated_at, both of
    which are now tracked in the detection tuple after the T18 interval bump).

    get_tenant_session is monkeypatched to yield the per-test DB session so
    the flushed INSERT data is visible to the backstop within the same
    connection-level transaction that conftest wraps each test in.

    POLL_INTERVAL_SEC is patched to 0.2s for speed (production: 5s).
    """
    from sqlalchemy import text

    bank = await _build_seed_bank(db)
    job_id = bank.job_posting_id
    tenant_id = str(bank.tenant_id)

    # Replace fast path with an async generator that sleeps forever.
    async def _broken_subscribe(*_channels):
        while True:
            await asyncio.sleep(30.0)
            yield  # unreachable; required for async-generator shape

    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    # Speed up the backstop for the test. Patch both cadences so the test
    # is robust to the seed bank's status — _build_seed_bank currently sets
    # status="generating" (fast cadence applies), but patching both means
    # this test stays green if that seed default ever changes.
    monkeypatch.setattr(sse, "POLL_INTERVAL_SEC", 0.2)
    monkeypatch.setattr(sse, "POLL_INTERVAL_IDLE_SEC", 0.2)

    # Route the backstop's DB access through the test session so it can see
    # flushed-but-not-committed data within the per-test connection transaction.
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
            if "bank." in frame:
                break

    consumer = asyncio.create_task(consume())

    # Let the backstop do one silent first-observation poll before mutating.
    await asyncio.sleep(0.5)

    # INSERT a stage_questions row. This bumps both question_count (0→1) and
    # max_updated_at (None→timestamp), both tracked in the detection tuple.
    # The backstop detects the change and emits bank.question_updated.
    await db.execute(
        text("""
            INSERT INTO stage_questions (
                id, tenant_id, bank_id, position, source, text,
                signal_values, estimated_minutes, is_mandatory,
                follow_ups, positive_evidence, red_flags, rubric,
                evaluation_hint, edited_by_recruiter
            )
            VALUES (
                gen_random_uuid(), :tid, :bid, 1, 'generated',
                'SSE backstop test question',
                ARRAY['signal_one']::text[], 3.0, false,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                '{}'::jsonb,
                'hint for backstop test', false
            )
        """),
        {"tid": bank.tenant_id, "bid": bank.id},
    )
    await db.flush()

    try:
        await asyncio.wait_for(consumer, timeout=5.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail(
            "Backstop poll did not emit a bank.* event within 5s after INSERT"
        )
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any("bank." in f for f in received), (
        f"No bank.* event in received frames: {received}"
    )
    assert any(pubsub.Events.BANK_QUESTION_UPDATED in f for f in received), (
        f"Expected bank.question_updated, got: {received}"
    )


# ---------------------------------------------------------------------------
# T19: End-to-end — HTTP PATCH → BackgroundTasks → publish → subscribe → SSE
# ---------------------------------------------------------------------------

_T19_BEARER = "test-sse-e2e-token"


async def test_e2e_mutation_to_sse_happy_path(db: AsyncSession, monkeypatch):
    """HTTP PATCH → background task publishes → SSE subscribe emits → client sees frame.

    No pubsub monkeypatching — exercises the real pub/sub transport end to end.

    The test seeds a bank + question via the shared _build_seed_bank helper,
    then installs a raw-SQL question row (same pattern as T18) so the PATCH
    handler finds a question to update.  Auth + DB dependency overrides mirror
    test_question_banks_events.py so the HTTP handler uses the test session.
    The SSE generator's backstop poll is slowed to 60 s so the fast path is
    the only realistic delivery path within the 5 s timeout.

    Prerequisites:
      - Redis is reachable (docker-compose provides it).
    """
    from sqlalchemy import text

    # ── Seed ─────────────────────────────────────────────────────────────────
    bank = await _build_seed_bank(db)
    # _build_seed_bank sets status="generating"; set it to "reviewing" so
    # update_question / auto_revert_on_edit works without raising.
    bank.status = "reviewing"
    await db.flush()

    # Insert one stage_questions row (the PATCH needs an existing question).
    # rubric must satisfy the QuestionRubric schema (excellent/meets_bar/below_bar).
    # The rubric is a static inline JSON literal — safe to embed as a SQL literal
    # because this is test-only hardcoded data with no user input.
    import uuid as _uuid
    _question_id = _uuid.uuid4()
    await db.execute(
        text("""
            INSERT INTO stage_questions (
                id, tenant_id, bank_id, position, source, text,
                signal_values, estimated_minutes, is_mandatory,
                follow_ups, positive_evidence, red_flags, rubric,
                evaluation_hint, edited_by_recruiter
            )
            VALUES (
                :qid, :tid, :bid, 1, 'recruiter',
                'Original E2E test question text',
                ARRAY[]::text[], 3.0, false,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                '{"excellent": "A strong answer names specific tools and describes structured approaches.", "meets_bar": "An acceptable answer mentions at least one tool or technique.", "below_bar": "A weak answer is vague with no concrete examples."}'::jsonb,
                'hint for e2e test', false
            )
        """),
        {
            "qid": _question_id,
            "tid": bank.tenant_id,
            "bid": bank.id,
        },
    )
    await db.flush()

    question_id = _question_id

    job_id = bank.job_posting_id
    stage_id = bank.stage_id
    tenant_id = bank.tenant_id

    # ── Auth + DB overrides (mirrors test_question_banks_events.py) ───────────
    # We need a User row so UserContext.user is populated.  Re-use
    # create_test_user from conftest helpers already imported at module level.
    user = await create_test_user(db, tenant_id)

    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )
    ctx = UserContext(
        user=user,
        is_super_admin=True,   # super-admin bypasses all permission checks
        assignments=[],
    )

    async def _user_override() -> UserContext:
        return ctx

    async def _db_override():
        await db.execute(
            sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        )
        yield db

    def _fake_verify(token: str):
        if token == _T19_BEARER:
            return fake_payload
        return None

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override
    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    # ── SSE generator configuration ───────────────────────────────────────────
    # Slow backstop so the fast path is the only realistic delivery route.
    # Bank is in `reviewing` (terminal) here, so _poll_loop picks the IDLE
    # cadence — must patch BOTH constants or the default 30s IDLE cadence
    # would let the backstop fire inside the 5s test timeout.
    monkeypatch.setattr(sse, "POLL_INTERVAL_SEC", 60.0)
    monkeypatch.setattr(sse, "POLL_INTERVAL_IDLE_SEC", 60.0)

    # Route the backstop's DB access through the test session so it doesn't
    # open a real tenant session (which would need a committed tenant row).
    @asynccontextmanager
    async def _fake_tenant_session(_tid: str):
        yield db

    monkeypatch.setattr(sse, "get_tenant_session", _fake_tenant_session)

    # ── Consume SSE frames ────────────────────────────────────────────────────
    received: list[str] = []

    async def consume():
        async for frame in sse._sse_generator(
            tenant_id=str(tenant_id),
            job_id=job_id,
        ):
            received.append(frame)
            if pubsub.Events.BANK_QUESTION_UPDATED in frame:
                break

    consumer = asyncio.create_task(consume())
    # Give the subscribe coroutine time to connect to Redis before PATCHing.
    await asyncio.sleep(0.2)

    # ── Real HTTP PATCH — goes through handler, commits, fires BackgroundTask ─
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.patch(
                f"/api/jobs/{job_id}/pipeline/stages/{stage_id}/questions/{question_id}",
                json={"text": "E2E test update — new question text"},
                headers={"Authorization": f"Bearer {_T19_BEARER}"},
            )
        assert resp.status_code == 200, f"PATCH failed: {resp.status_code} {resp.text}"
    finally:
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    # ── Wait for the SSE frame ────────────────────────────────────────────────
    try:
        await asyncio.wait_for(consumer, timeout=5.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail(
            f"SSE did not receive the mutation event end-to-end within 5s. "
            f"Frames received so far: {received}"
        )
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any(pubsub.Events.BANK_QUESTION_UPDATED in f for f in received), (
        f"bank.question_updated not in received frames: {received}"
    )
    assert any("correlation_id" in f for f in received), (
        f"correlation_id not preserved in SSE frames: {received}"
    )


# ---------------------------------------------------------------------------
# T20: backstop cadence is gated on bank.status == "generating"
# ---------------------------------------------------------------------------


async def test_sse_backstop_uses_idle_cadence_when_no_bank_generating(
    db: AsyncSession, monkeypatch
):
    """When no bank is in `generating`, the backstop must use POLL_INTERVAL_IDLE_SEC.

    This test inverts the patch values: the fast cadence (POLL_INTERVAL_SEC)
    is set slow (60s) and the idle cadence (POLL_INTERVAL_IDLE_SEC) is set
    fast (0.2s). Bank is in `draft` (not generating, not terminal). If the
    gating works, the backstop polls every 0.2s and detects the question
    INSERT within the test timeout. If the gating is broken (or absent —
    i.e. the production code reverts to always sleeping POLL_INTERVAL_SEC),
    the test would block on the 60s cadence and the timeout would fire.
    """
    from sqlalchemy import text

    bank = await _build_seed_bank(db)
    # Override seed default ("generating") with "draft" so any_generating=False.
    bank.status = "draft"
    await db.flush()

    job_id = bank.job_posting_id
    tenant_id = str(bank.tenant_id)

    # Replace fast path with an async generator that sleeps forever — same
    # pattern as T18, isolates the backstop as the only delivery route.
    async def _broken_subscribe(*_channels):
        while True:
            await asyncio.sleep(30.0)
            yield  # unreachable; required for async-generator shape

    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    # Inverted cadences: fast slow, idle fast. If the gating logic
    # ever regresses to "always use POLL_INTERVAL_SEC", this test fails.
    monkeypatch.setattr(sse, "POLL_INTERVAL_SEC", 60.0)
    monkeypatch.setattr(sse, "POLL_INTERVAL_IDLE_SEC", 0.2)

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
            if "bank." in frame:
                break

    consumer = asyncio.create_task(consume())

    # First silent poll establishes baseline state. With idle cadence at
    # 0.2s, this completes well within 0.5s.
    await asyncio.sleep(0.5)

    # INSERT a question to trigger a state change the backstop should detect.
    await db.execute(
        text("""
            INSERT INTO stage_questions (
                id, tenant_id, bank_id, position, source, text,
                signal_values, estimated_minutes, is_mandatory,
                follow_ups, positive_evidence, red_flags, rubric,
                evaluation_hint, edited_by_recruiter
            )
            VALUES (
                gen_random_uuid(), :tid, :bid, 1, 'generated',
                'idle-cadence test question',
                ARRAY['signal_one']::text[], 3.0, false,
                '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                '{}'::jsonb,
                'hint', false
            )
        """),
        {"tid": bank.tenant_id, "bid": bank.id},
    )
    await db.flush()

    try:
        # Generous 5s timeout — at the idle cadence of 0.2s, the next poll
        # fires within 0.2-0.4s of the INSERT. If the gating regressed to
        # the 60s fast cadence, the timeout fires instead.
        await asyncio.wait_for(consumer, timeout=5.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail(
            "Backstop did not emit at the IDLE cadence — gating logic may be "
            "using POLL_INTERVAL_SEC unconditionally."
        )
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any(pubsub.Events.BANK_QUESTION_UPDATED in f for f in received), (
        f"Expected bank.question_updated, got: {received}"
    )
