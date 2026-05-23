"""v2 builds SessionResult without the v1 snapshot fields, with a coverage_summary."""
from app.modules.interview_runtime import SessionResult, TranscriptEntry


def _v2_result(**over):
    base = dict(
        session_id="11111111-1111-1111-1111-111111111111",
        job_title="Backend Engineer", stage_id="s1", stage_type="ai_screening",
        candidate_name="Asha", duration_seconds=600.0,
        questions_asked=4, questions_skipped=1, total_probes_fired=3,
        full_transcript=[TranscriptEntry(role="agent", text="Hi", timestamp_ms=0)],
        completed_at="2026-05-23T00:00:00+00:00",
        coverage_summary={"python": "sufficient", "kafka": "failed"},
    )
    base.update(over)
    return SessionResult(**base)


def test_session_result_v2_omits_v1_snapshots():
    r = _v2_result()
    assert r.signal_ledger is None
    assert r.question_queue is None
    assert r.claims_pool is None
    assert r.coverage_summary == {"python": "sufficient", "kafka": "failed"}
    assert r.push_back_total == 0 and r.quality_distribution == {}
    # round-trips to JSON for raw_result_json
    dumped = r.model_dump(mode="json")
    assert dumped["signal_ledger"] is None and dumped["coverage_summary"]["kafka"] == "failed"


def test_session_result_v1_still_accepts_snapshots():
    from app.modules.interview_runtime import SignalLedgerSnapshot
    r = _v2_result(signal_ledger=SignalLedgerSnapshot(), coverage_summary=None)
    assert r.signal_ledger is not None and r.coverage_summary is None
