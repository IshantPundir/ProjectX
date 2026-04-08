"""Tests for job_status_event_generator — de-dup, terminal close, disconnect."""

import pytest

from app.modules.jd.schemas import JobStatusEvent
from app.modules.jd.sse import job_status_event_generator


class FakeRequest:
    """Mimics fastapi.Request for the is_disconnected() check."""

    def __init__(self, disconnect_after: int = 999) -> None:
        self.calls = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > self.disconnect_after


@pytest.mark.asyncio
async def test_emits_initial_then_terminal(monkeypatch):
    """Initial status emits once; terminal state emits and closes.
    A middle duplicate is de-duplicated."""
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
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.01)

    gen = job_status_event_generator(
        db=None,
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(),
    )
    yielded = [ev async for ev in gen]

    # Should emit: extracting (initial), extracted (terminal). Middle duplicate de-duped.
    assert len(yielded) == 2
    assert "signals_extracting" in yielded[0]["data"]
    assert "signals_extracted" in yielded[1]["data"]


@pytest.mark.asyncio
async def test_terminates_on_disconnect(monkeypatch):
    async def fake_get_job_status(db, job_id):
        return JobStatusEvent(
            job_id="00000000-0000-0000-0000-000000000001",
            status="signals_extracting",
            error=None,
            signal_snapshot_version=None,
        )

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.01)

    gen = job_status_event_generator(
        db=None,
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(disconnect_after=0),
    )
    yielded = [ev async for ev in gen]
    assert yielded == []  # disconnected before first yield


@pytest.mark.asyncio
async def test_missing_job_terminates_cleanly(monkeypatch):
    """If get_job_status returns None (job disappeared), the generator
    should terminate without error."""

    async def fake_get_job_status(db, job_id):
        return None

    monkeypatch.setattr("app.modules.jd.sse.get_job_status", fake_get_job_status)
    monkeypatch.setattr("app.modules.jd.sse.POLL_INTERVAL_SECONDS", 0.01)

    gen = job_status_event_generator(
        db=None,
        job_id="00000000-0000-0000-0000-000000000001",
        request=FakeRequest(),
    )
    yielded = [ev async for ev in gen]
    assert yielded == []
