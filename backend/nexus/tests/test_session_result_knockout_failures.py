"""Round-trip test for SessionResult.knockout_failures."""
from __future__ import annotations

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_runtime import KnockoutFailure, SessionResult


def _make_minimal_result(**overrides) -> SessionResult:
    base = dict(
        session_id="00000000-0000-0000-0000-000000000001",
        job_title="Customer Support Specialist",
        stage_id="00000000-0000-0000-0000-000000000002",
        stage_type="phone_screen",
        candidate_name="Test Candidate",
        duration_seconds=600.0,
        questions_asked=4,
        questions_skipped=0,
        total_probes_fired=2,
        full_transcript=[],
        completed_at="2026-05-03T12:00:00Z",
        signal_ledger=SignalLedgerSnapshot(entries=[], snapshots={}, next_seq=1),
        question_queue=QuestionQueueSnapshot(),
        claims_pool=ClaimsPoolSnapshot(),
        audit_envelope_ref=None,
    )
    base.update(overrides)
    return SessionResult(**base)


def test_default_empty_list() -> None:
    r = _make_minimal_result()
    assert r.knockout_failures == []


def test_round_trip_with_failures() -> None:
    failures = [
        KnockoutFailure(
            question_id="q3",
            reason="Cannot work UK shift hours.",
            signal_values=["uk_shift"],
            occurred_at_ms=120_000,
        ),
        KnockoutFailure(
            question_id="q4",
            reason="No driver's license.",
            signal_values=["drivers_license"],
            occurred_at_ms=180_000,
        ),
    ]
    r = _make_minimal_result(knockout_failures=failures)
    dumped = r.model_dump(mode="json")
    r2 = SessionResult.model_validate(dumped)
    assert r2.knockout_failures == failures


def test_independent_per_instance_default() -> None:
    """default_factory=list, not a shared mutable default."""
    a = _make_minimal_result()
    b = _make_minimal_result()
    a.knockout_failures.append(
        KnockoutFailure(
            question_id="q1",
            reason="x",
            signal_values=["sig"],
            occurred_at_ms=0,
        )
    )
    assert b.knockout_failures == []
