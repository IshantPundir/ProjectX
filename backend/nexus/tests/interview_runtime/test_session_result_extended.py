"""Wire-contract tests for SessionResult after the engine-snapshot extension.

The structured engine emits three typed snapshots (signal ledger, question
queue, claims pool) plus an optional pointer to the persisted audit
envelope. These are the post-Phase-7.2 SessionResult fields; the legacy
`question_results` list was removed in the same change.
"""
from __future__ import annotations

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import (
    CoverageState,
    SignalLedgerSnapshot,
    SignalSnapshot,
)
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_runtime.schemas import SessionResult


def test_session_result_has_new_fields():
    fields = SessionResult.model_fields
    for name in ("signal_ledger", "question_queue", "claims_pool", "audit_envelope_ref"):
        assert name in fields, f"{name} missing from SessionResult"


def test_session_result_question_results_removed():
    fields = SessionResult.model_fields
    assert "question_results" not in fields


def test_session_result_construction():
    r = SessionResult(
        session_id="s",
        job_title="j",
        stage_id="stg-1",
        stage_type="ai_screening",
        candidate_name="c",
        duration_seconds=10.0,
        questions_asked=1,
        questions_skipped=0,
        total_probes_fired=0,
        full_transcript=[],
        completed_at="2026-05-07T00:00:00Z",
        knockout_failures=[],
        audio_tuning_summary=None,
        signal_ledger=SignalLedgerSnapshot(
            entries=[],
            snapshots={"S1": SignalSnapshot(signal_value="S1", coverage=CoverageState.none)},
            next_seq=1,
        ),
        question_queue=QuestionQueueSnapshot(),
        claims_pool=ClaimsPoolSnapshot(),
        audit_envelope_ref="/tmp/engine-events/s.json",
    )
    assert r.signal_ledger.next_seq == 1
    assert r.audit_envelope_ref.endswith("s.json")
