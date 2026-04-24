"""Tests for job_status_event_generator — pub/sub fast path, backstop, de-dup,
terminal close, and disconnect.

T1 (test_emits_initial_then_terminal): De-dup and terminal-state close via
   the backstop poll path. Pub/sub fast path replaced with an infinite sleeper.

T2 (test_terminates_on_disconnect): Client disconnect before first event.

T3 (test_missing_job_terminates_cleanly): Backstop detects job gone (None
   sentinel), generator exits without yielding.

T4 (test_sse_forwards_pubsub_events): An envelope published to job:{id} is
   forwarded by the SSE generator via the pub/sub fast path within 3s.
   Uses the real Redis transport.

T5 (test_sse_backstop_emits_when_pubsub_unavailable): With pub/sub replaced
   by an infinite sleeper, the backstop poll delivers an event when a new
   DB state is returned by get_job_status.

T6 (test_e2e_confirm_signals_to_sse): Real HTTP /signals/confirm →
   BackgroundTasks → publish → subscribe → SSE frame. No pubsub
   monkeypatching — exercises the real pub/sub transport end-to-end.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import pubsub
from app.database import get_tenant_db
from app.main import app
from app.models import JobPosting, JobPostingSignalSnapshot
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.schemas import TokenPayload
from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.sse import job_status_event_generator
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeRequest:
    """Mimics fastapi.Request for the is_disconnected() check."""

    def __init__(self, disconnect_after: int = 999) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


class FakeSession:
    """Minimal stand-in for AsyncSession used by the SSE generator."""

    async def execute(self, stmt):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@asynccontextmanager
async def _fake_tenant_session(tenant_id: str):
    yield FakeSession()


async def _broken_subscribe(*_channels):
    """Async generator that never yields — simulates pub/sub being down."""
    while True:
        await asyncio.sleep(30.0)
        yield  # unreachable; satisfies the async-generator shape


# ---------------------------------------------------------------------------
# T1: de-dup and terminal-state close via backstop
# ---------------------------------------------------------------------------


async def test_emits_initial_then_terminal(monkeypatch):
    """Initial status emits once; terminal state emits and closes.
    A middle duplicate is de-duplicated. Events delivered via backstop poll.
    """
    events_to_yield = [
        JobStatusEvent(
            job_id="00000000-0000-0000-0000-000000000001",
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        ),
        JobStatusEvent(
            job_id="00000000-0000-0000-0000-000000000001",
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        ),  # duplicate — should be de-duped
        JobStatusEvent(
            job_id="00000000-0000-0000-0000-000000000001",
            status="signals_extracted",
            error=None,
            signal_snapshot_version=1,
        ),
    ]
    idx = {"i": 0}

    async def fake_get_job_status(db, job_id):
        i = min(idx["i"], len(events_to_yield) - 1)
        idx["i"] += 1
        return events_to_yield[i]

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    gen = job_status_event_generator(
        tenant_id="00000000-0000-0000-0000-000000000099",
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(),
    )
    yielded = [ev async for ev in gen]

    # Should emit: extracting (initial), extracted (terminal). Middle duplicate de-duped.
    assert len(yielded) == 2
    assert "signals_extracting" in yielded[0]["data"]
    assert "signals_extracted" in yielded[1]["data"]


# ---------------------------------------------------------------------------
# T2: client disconnect before first event
# ---------------------------------------------------------------------------


async def test_terminates_on_disconnect(monkeypatch):
    """Generator exits immediately when the client is already disconnected."""
    async def fake_get_job_status(db, job_id):
        return JobStatusEvent(
            job_id="00000000-0000-0000-0000-000000000001",
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        )

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    gen = job_status_event_generator(
        tenant_id="00000000-0000-0000-0000-000000000099",
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(disconnect_after=0),
    )
    yielded = [ev async for ev in gen]
    assert yielded == []  # disconnected before first yield


# ---------------------------------------------------------------------------
# T3: missing job terminates cleanly via None sentinel
# ---------------------------------------------------------------------------


async def test_missing_job_terminates_cleanly(monkeypatch):
    """If get_job_status returns None (job disappeared), the generator
    terminates without error, yielding nothing."""

    async def fake_get_job_status(db, job_id):
        return None

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    gen = job_status_event_generator(
        tenant_id="00000000-0000-0000-0000-000000000099",
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(),
    )

    try:
        yielded = await asyncio.wait_for(
            _collect_async(gen), timeout=3.0
        )
    except asyncio.TimeoutError:
        pytest.fail("Generator did not terminate within 3s after None from get_job_status")

    assert yielded == []


async def _collect_async(gen) -> list:
    """Drain an async generator into a list."""
    result = []
    async for item in gen:
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# T4: pub/sub fast path delivers events (real Redis)
# ---------------------------------------------------------------------------


async def test_sse_forwards_pubsub_events(monkeypatch):
    """An envelope published to job:{id} is yielded by the SSE generator
    within 3 seconds via the pub/sub fast path.

    Uses the real Redis transport. The backstop poll interval is set to 60s
    so it does not interfere within the test window.
    """
    job_id = "00000000-0000-0000-0000-000000000042"
    tenant_id = "00000000-0000-0000-0000-000000000099"

    # Slow the backstop so the fast path is the only realistic delivery path.
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 60.0)
    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)

    # Provide a get_job_status that never returns (backstop won't interfere).
    async def _sleeping_get_job_status(db, job_id_arg):
        await asyncio.sleep(60.0)
        return None

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", _sleeping_get_job_status)

    received: list[dict] = []

    async def consume():
        async for frame in job_status_event_generator(
            tenant_id=tenant_id,
            job_id=job_id,
            request=FakeRequest(),
        ):
            received.append(frame)
            if "signals_extracted" in frame.get("data", ""):
                break

    consumer = asyncio.create_task(consume())

    # Give the subscribe coroutine time to connect to Redis before publishing.
    await asyncio.sleep(0.2)

    await pubsub.publish(
        pubsub.job_channel(job_id),
        pubsub.Events.JD_STATUS_CHANGED,
        {
            "job_id": job_id,
            "status": "signals_extracted",
            "enrichment_status": "idle",
            "signal_snapshot_version": 1,
            "error": None,
            "is_confirmed": False,
        },
        correlation_id="test-jd-sse-fast-path",
    )

    try:
        await asyncio.wait_for(consumer, timeout=3.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail("SSE did not forward pub/sub event within 3s")
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any("signals_extracted" in f.get("data", "") for f in received), (
        f"signals_extracted not in received frames: {received}"
    )
    # Verify the frame shape: dict with 'event' + 'data' keys.
    assert all(f.get("event") == "status" for f in received), (
        f"Unexpected event key in frames: {received}"
    )


# ---------------------------------------------------------------------------
# T5: backstop emits when pub/sub is unavailable
# ---------------------------------------------------------------------------


async def test_sse_backstop_emits_when_pubsub_unavailable(monkeypatch):
    """With pub/sub replaced by an infinite sleeper, the backstop poll still
    delivers a status event when get_job_status returns a new state.

    POLL_INTERVAL_SECONDS is patched to 0.05s for speed (production: 5s).
    """
    job_id = "00000000-0000-0000-0000-000000000043"
    tenant_id = "00000000-0000-0000-0000-000000000099"

    # Replace fast path with an async generator that sleeps forever.
    monkeypatch.setattr(pubsub, "subscribe", _broken_subscribe)

    # Speed up the backstop for the test.
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)

    # Backstop will call get_job_status on each cycle. Sequence:
    # poll 0 → signals_extracting (emit)
    # poll 1 → signals_extracting (dedup, skip)
    # poll 2 → signals_extracted (terminal, emit + close)
    call_count = {"n": 0}
    states = [
        JobStatusEvent(
            job_id=job_id,
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        ),
        JobStatusEvent(
            job_id=job_id,
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        ),
        JobStatusEvent(
            job_id=job_id,
            status="signals_extracted",
            error=None,
            signal_snapshot_version=1,
        ),
    ]

    async def fake_get_job_status(db, job_id_arg):
        i = min(call_count["n"], len(states) - 1)
        call_count["n"] += 1
        return states[i]

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)

    received: list[dict] = []

    async def consume():
        async for frame in job_status_event_generator(
            tenant_id=tenant_id,
            job_id=job_id,
            request=FakeRequest(),
        ):
            received.append(frame)

    consumer = asyncio.create_task(consume())

    try:
        await asyncio.wait_for(consumer, timeout=3.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail("Backstop did not emit and close within 3s")
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert len(received) == 2, f"Expected 2 frames (dedup the middle), got: {received}"
    assert "signals_extracting" in received[0]["data"]
    assert "signals_extracted" in received[1]["data"]


# ---------------------------------------------------------------------------
# T6: End-to-end — HTTP POST /signals/confirm → publish → subscribe → SSE
# ---------------------------------------------------------------------------

_T6_BEARER = "test-jd-sse-e2e-token"

_T6_VALID_PROFILE = {
    "about": "We build real-time risk scoring for mid-market lenders at scale.",
    "industry": "fintech_financial_services",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}


async def test_e2e_confirm_signals_to_sse(db: AsyncSession, monkeypatch):
    """Real HTTP POST /signals/confirm → BackgroundTasks → publish → subscribe → SSE frame.

    No pubsub monkeypatching — exercises the real pub/sub transport end-to-end.
    POLL_INTERVAL_SECONDS is set to 60s to force fast-path delivery: a PASS
    here means pub/sub actually worked, not the backstop.
    """
    # ── Seed — job in signals_extracted state ─────────────────────────────────
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=_T6_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="E2E SSE Test Job",
        description_raw="A" * 200,
        description_enriched="Enriched job description for E2E SSE testing.",
        status="signals_extracted",
        enrichment_status="idle",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            {
                "value": "Python",
                "type": "competency",
                "priority": "required",
                "weight": 2,
                "knockout": False,
                "stage": "interview",
                "source": "ai_extracted",
                "inference_basis": None,
            },
            {
                "value": "5+ years backend",
                "type": "experience",
                "priority": "required",
                "weight": 2,
                "knockout": True,
                "stage": "screen",
                "source": "ai_extracted",
                "inference_basis": None,
            },
        ],
        seniority_level="senior",
        role_summary="A senior backend engineer at a fintech startup.",
        confirmed_by=None,
        confirmed_at=None,
    )
    db.add(snapshot)
    await db.flush()

    job_id = job.id
    tenant_id = tenant.id

    # ── Auth + DB overrides — same pattern as test_jd_events.py ──────────────
    fake_payload = TokenPayload(
        sub=str(user.auth_user_id),
        tenant_id=str(tenant_id),
        email=user.email,
        is_projectx_admin=False,
        exp=9999999999,
    )
    ctx = UserContext(
        user=user,
        is_super_admin=True,
        workspace_mode="enterprise",
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
        if token == _T6_BEARER:
            return fake_payload
        return None

    app.dependency_overrides[get_current_user_roles] = _user_override
    app.dependency_overrides[get_tenant_db] = _db_override
    verify_patch = patch(
        "app.middleware.auth.verify_access_token", side_effect=_fake_verify
    )
    verify_patch.start()

    # ── SSE generator configuration ───────────────────────────────────────────
    # Push the backstop interval to 60s so the only realistic delivery path
    # within the 3s test window is pub/sub fast path. A PASS = pub/sub worked.
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 60.0)

    # Route the backstop's DB access through the test session (belt-and-
    # suspenders: backstop won't fire within 60s, but avoid dangling sessions).
    @asynccontextmanager
    async def _fake_tenant_session(tid: str):
        yield db

    monkeypatch.setattr("app.modules.jd.sse.get_tenant_session", _fake_tenant_session)

    # ── Consume SSE frames ────────────────────────────────────────────────────
    received: list[dict] = []

    async def consume():
        async for frame in job_status_event_generator(
            tenant_id=str(tenant_id),
            job_id=job_id,
            request=FakeRequest(),
        ):
            received.append(frame)
            if "signals_confirmed" in frame.get("data", ""):
                break

    consumer = asyncio.create_task(consume())
    # Give the subscribe coroutine time to connect to Redis before POSTing.
    await asyncio.sleep(0.2)

    # ── Real HTTP POST — goes through handler, commits, fires BackgroundTask ──
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/jobs/{job_id}/signals/confirm",
                headers={"Authorization": f"Bearer {_T6_BEARER}"},
            )
        assert resp.status_code == 200, f"confirm-signals failed: {resp.status_code} {resp.text}"
    finally:
        verify_patch.stop()
        app.dependency_overrides.pop(get_current_user_roles, None)
        app.dependency_overrides.pop(get_tenant_db, None)

    # ── Wait for the SSE frame ────────────────────────────────────────────────
    try:
        await asyncio.wait_for(consumer, timeout=3.0)
    except asyncio.TimeoutError:
        consumer.cancel()
        pytest.fail(
            f"SSE did not receive confirm-signals event end-to-end within 3s. "
            f"Frames received so far: {received}"
        )
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)

    assert any("signals_confirmed" in f.get("data", "") for f in received), (
        f"signals_confirmed not in received SSE frames: {received}"
    )
    assert all(f.get("event") == "status" for f in received), (
        f"Unexpected event key in frames: {received}"
    )
