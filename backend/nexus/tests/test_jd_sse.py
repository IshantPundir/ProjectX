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
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from app import pubsub
from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.sse import job_status_event_generator

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
