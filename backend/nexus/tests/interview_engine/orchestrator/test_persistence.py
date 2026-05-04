"""Tests for LedgerPersistence — Redis writeback semantics.

Uses ``unittest.mock.AsyncMock`` to simulate the redis.asyncio.Redis
client — no real Redis required for unit tests. Live-Redis integration
coverage lands in Phase B's structured-agent integration test.

Covered:
- Best-effort writes: success → returns True + sets last-persisted seq.
- Best-effort writes: Redis exception → returns False, no raise, last
  seq unchanged.
- Reads: miss returns None; bytes return decoded; failure returns None.
- Schema-drift on rehydration is logged + returns None (does not crash).
- Gap detection at session close.
- Tenant/session mismatch on write_state raises ValueError.
- TTL is passed through as `ex=` on every SET.
- Key layout: tenant:{t}:session:{s}:{kind}.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.orchestrator import (
    EvidenceQuote,
    InterviewPhase,
    InterviewState,
    LedgerPersistence,
    SignalLedger,
)
from app.modules.interview_runtime import SignalMetadata

TENANT_ID = "tenant-abc"
SESSION_ID = "sess-xyz"


def _make_state(*, sequence_number: int = 0) -> InterviewState:
    state = InterviewState(
        session_id=SESSION_ID,
        tenant_id=TENANT_ID,
        job_id="job-1",
        candidate_id="cand-1",
        started_at=datetime.now(UTC),
        target_duration_seconds=900,
    )
    # Drive sequence_number deterministically via legal transitions.
    legal_chain = [
        InterviewPhase.CONSENT,
        InterviewPhase.INTRO,
        InterviewPhase.MAIN_LOOP,
        InterviewPhase.NORMAL_WRAP,
        InterviewPhase.CLOSED,
    ]
    for i in range(min(sequence_number, len(legal_chain))):
        state.transition(legal_chain[i])
    assert state.sequence_number == min(sequence_number, len(legal_chain))
    return state


def _make_ledger(*, with_one_update: bool = False) -> SignalLedger:
    ledger = SignalLedger.from_metadata([
        SignalMetadata(
            value="Python", type="competency", priority="preferred",
            weight=2, knockout=False, stage="screen",
            evaluation_method="verbal_response",
        ),
    ])
    if with_one_update:
        ledger.append_evidence(
            "Python",
            evidence=EvidenceQuote(
                quote="I built Django apps.",
                turn_id="t1",
                source_question_id="q1",
                strength="strong",
                timestamp=datetime.now(UTC),
            ),
            new_coverage="partial",
        )
    return ledger


def _make_persistence(*, client: AsyncMock | None = None) -> LedgerPersistence:
    return LedgerPersistence(
        client or AsyncMock(),
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
    )


# ---------------------------------------------------------------------------
# Writes — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_state_success_returns_true_and_records_seq():
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    p = _make_persistence(client=client)

    state = _make_state(sequence_number=2)
    ok = await p.write_state(state)

    assert ok is True
    client.set.assert_awaited_once()
    # Inspect the call: key, body, ex=ttl.
    call_args = client.set.await_args
    key = call_args.args[0]
    body = call_args.args[1]
    assert key == f"tenant:{TENANT_ID}:session:{SESSION_ID}:state"
    # body is JSON — phase encoded inline.
    assert "intro" in body or "consent" in body or "main_loop" in body
    assert call_args.kwargs.get("ex") == 6 * 3600

    # Last-persisted seq updated to the state's sequence_number (2 here).
    assert p._last_state_seq_persisted == 2


@pytest.mark.asyncio
async def test_write_ledger_success_returns_true_and_records_seq():
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    p = _make_persistence(client=client)

    ledger = _make_ledger(with_one_update=True)
    ok = await p.write_ledger(ledger)

    assert ok is True
    client.set.assert_awaited_once()
    key = client.set.await_args.args[0]
    assert key == f"tenant:{TENANT_ID}:session:{SESSION_ID}:ledger"
    assert p._last_ledger_seq_persisted == 1


@pytest.mark.asyncio
async def test_write_state_uses_custom_ttl():
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    p = LedgerPersistence(
        client, tenant_id=TENANT_ID, session_id=SESSION_ID, ttl_seconds=120,
    )
    await p.write_state(_make_state())
    assert client.set.await_args.kwargs.get("ex") == 120


# ---------------------------------------------------------------------------
# Writes — failure (best-effort, no raise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_state_returns_false_on_redis_exception():
    client = AsyncMock()
    client.set = AsyncMock(side_effect=ConnectionError("redis down"))
    p = _make_persistence(client=client)

    state = _make_state(sequence_number=2)
    # Must not raise — best-effort.
    ok = await p.write_state(state)

    assert ok is False
    assert p._last_state_seq_persisted is None


@pytest.mark.asyncio
async def test_write_ledger_returns_false_on_redis_exception():
    client = AsyncMock()
    client.set = AsyncMock(side_effect=TimeoutError("redis slow"))
    p = _make_persistence(client=client)

    ok = await p.write_ledger(_make_ledger())

    assert ok is False
    assert p._last_ledger_seq_persisted is None


@pytest.mark.asyncio
async def test_write_state_failure_does_not_corrupt_subsequent_success():
    """A failed write doesn't poison the next successful write — last_seq
    advances correctly."""
    client = AsyncMock()
    client.set = AsyncMock(side_effect=[
        ConnectionError("transient"),  # first call fails
        True,                          # second call succeeds
    ])
    p = _make_persistence(client=client)

    state1 = _make_state(sequence_number=1)
    assert await p.write_state(state1) is False
    assert p._last_state_seq_persisted is None

    state2 = _make_state(sequence_number=2)
    assert await p.write_state(state2) is True
    assert p._last_state_seq_persisted == 2


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_state_miss_returns_none():
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    p = _make_persistence(client=client)
    assert await p.load_state() is None


@pytest.mark.asyncio
async def test_load_state_decodes_bytes_and_rehydrates():
    state = _make_state(sequence_number=3)
    body_bytes = state.model_dump_json().encode("utf-8")
    client = AsyncMock()
    client.get = AsyncMock(return_value=body_bytes)
    p = _make_persistence(client=client)

    rehydrated = await p.load_state()

    assert rehydrated is not None
    assert rehydrated.session_id == SESSION_ID
    assert rehydrated.tenant_id == TENANT_ID
    assert rehydrated.sequence_number == 3


@pytest.mark.asyncio
async def test_load_state_redis_failure_returns_none():
    client = AsyncMock()
    client.get = AsyncMock(side_effect=ConnectionError("offline"))
    p = _make_persistence(client=client)
    assert await p.load_state() is None


@pytest.mark.asyncio
async def test_load_state_schema_drift_returns_none_no_raise():
    """An old / corrupt JSON in Redis must not crash a fresh agent."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=b'{"this_is": "not a valid InterviewState"}')
    p = _make_persistence(client=client)
    assert await p.load_state() is None


@pytest.mark.asyncio
async def test_load_ledger_round_trip():
    ledger = _make_ledger(with_one_update=True)
    body = ledger.model_dump_json()
    client = AsyncMock()
    client.get = AsyncMock(return_value=body)
    p = _make_persistence(client=client)

    rehydrated = await p.load_ledger()

    assert rehydrated is not None
    assert "Python" in rehydrated.signals
    assert rehydrated.signals["Python"].coverage == "partial"
    assert rehydrated.sequence_number == 1


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def test_detect_gaps_zero_when_everything_persisted():
    p = _make_persistence()
    p._last_state_seq_persisted = 5
    p._last_ledger_seq_persisted = 12
    gaps = p.detect_gaps(current_state_seq=5, current_ledger_seq=12)
    assert gaps == {"state_gap": 0, "ledger_gap": 0}


def test_detect_gaps_reports_delta():
    p = _make_persistence()
    p._last_state_seq_persisted = 3
    p._last_ledger_seq_persisted = 7
    gaps = p.detect_gaps(current_state_seq=5, current_ledger_seq=10)
    assert gaps == {"state_gap": 2, "ledger_gap": 3}


def test_detect_gaps_full_loss_when_nothing_persisted():
    """If no write ever succeeded, the gap is the entire current seq."""
    p = _make_persistence()
    assert p._last_state_seq_persisted is None
    assert p._last_ledger_seq_persisted is None
    gaps = p.detect_gaps(current_state_seq=4, current_ledger_seq=8)
    assert gaps == {"state_gap": 4, "ledger_gap": 8}


# ---------------------------------------------------------------------------
# Cross-tenant fence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_state_rejects_mismatched_tenant():
    p = _make_persistence()
    rogue_state = InterviewState(
        session_id="other-sess",
        tenant_id="other-tenant",
        job_id="job",
        candidate_id="cand",
        started_at=datetime.now(UTC),
        target_duration_seconds=600,
    )
    with pytest.raises(ValueError, match="cannot persist"):
        await p.write_state(rogue_state)


@pytest.mark.asyncio
async def test_write_state_rejects_mismatched_session():
    p = _make_persistence()
    rogue_state = InterviewState(
        session_id="other-sess",
        tenant_id=TENANT_ID,
        job_id="job",
        candidate_id="cand",
        started_at=datetime.now(UTC),
        target_duration_seconds=600,
    )
    with pytest.raises(ValueError, match="cannot persist"):
        await p.write_state(rogue_state)


# ---------------------------------------------------------------------------
# Asyncio.shield protects in-flight writes from caller cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_uses_asyncio_shield_so_cancelled_caller_doesnt_truncate():
    """If the orchestrator turn-loop is cancelled mid-write, the SET still
    completes (asyncio.shield)."""
    import asyncio

    set_completed = False

    async def slow_set(*args, **kwargs):
        # Simulate a slow Redis: yield control, then complete.
        nonlocal set_completed
        await asyncio.sleep(0.01)
        set_completed = True
        return True

    client = MagicMock()
    client.set = slow_set
    p = _make_persistence(client=client)

    async def cancelled_caller():
        await p.write_state(_make_state(sequence_number=1))

    import contextlib

    task = asyncio.create_task(cancelled_caller())
    await asyncio.sleep(0)  # let the task start
    task.cancel()
    # Whether the task was cancelled before or after the await is racy in
    # tests; just confirm the SET still ran (shield protected it).
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # Give the shielded SET a moment to drain.
    await asyncio.sleep(0.05)
    assert set_completed is True
