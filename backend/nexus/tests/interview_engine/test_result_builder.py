from app.modules.interview_engine.coverage import CoverageTracker
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope, EventLogEvent
from app.modules.interview_engine.result_builder import build_v2_session_result
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
    TranscriptEntry,
)


def _config():
    q = QuestionConfig(
        id="q1",
        position=0,
        text="Tell me about Python.",
        signal_values=["python"],
        estimated_minutes=3.0,
        is_mandatory=True,
        follow_ups=["own?"],
        positive_evidence=["a", "b", "c"],
        red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="listen carefully",
        question_kind="behavioral",
        primary_signal="python",
        difficulty="medium",
    )
    return SessionConfig(
        session_id="sess-1",
        job_id="j",
        candidate_id="c",
        job_title="Backend Engineer",
        hiring_company_name="Workato",
        role_summary="r",
        jd_text="jd",
        seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Asha"),
        stage=StageConfig(
            stage_id="st1",
            stage_type="ai_screening",
            name="Screen",
            duration_minutes=30,
            difficulty="medium",
            questions=[q],
        ),
        signals=["python"],
    )


def _envelope(events):
    return EventLogEnvelope(
        session_id="sess-1",
        tenant_id="t",
        correlation_id="corr",
        started_at="2026-05-23T00:00:00+00:00",
        events=events,
    )


def test_build_result_populates_coverage_summary_and_nulls_v1_fields():
    cov = CoverageTracker(signals=["python"], mandatory_signals=["python"])
    cov.apply_delta({"python": "sufficient"})
    env = _envelope([
        EventLogEvent(t_ms=0, wall_ms=0, kind="directive.delivered", payload={"act": "ASK"}),
        EventLogEvent(t_ms=10, wall_ms=10, kind="directive.delivered", payload={"act": "PROBE"}),
        EventLogEvent(
            t_ms=20, wall_ms=20, kind="directive.delivered", payload={"act": "ACK_ADVANCE"}
        ),
        EventLogEvent(t_ms=30, wall_ms=30, kind="directive.delivered", payload={"act": "CLOSE"}),
    ])
    result = build_v2_session_result(
        config=_config(),
        coverage=cov,
        transcript=[
            TranscriptEntry(role="agent", text="Hi", timestamp_ms=0),
            TranscriptEntry(role="candidate", text="I built X", timestamp_ms=5),
        ],
        envelope=env,
        audio_summary={"perceived": {"perceived_response_ms": {"p50": 900}}},
        knockout_failures=[],
        duration_seconds=42.0,
        completed_at="2026-05-23T00:01:00+00:00",
        audit_envelope_ref="/tmp/engine-events/sess-1.json",
    )
    assert result.signal_ledger is None
    assert result.question_queue is None
    assert result.claims_pool is None
    assert result.coverage_summary == {"python": "sufficient"}
    assert result.questions_asked == 2          # ASK + ACK_ADVANCE
    assert result.total_probes_fired == 1       # PROBE
    assert result.questions_skipped == 0        # bank size 1, 1 asked; max(0, 1-2)=0
    assert result.audit_envelope_ref.endswith("sess-1.json")
    assert result.candidate_name == "Asha" and result.stage_type == "ai_screening"
    assert result.audio_tuning_summary["perceived"]["perceived_response_ms"]["p50"] == 900
    # round-trips for raw_result_json
    assert result.model_dump(mode="json")["coverage_summary"]["python"] == "sufficient"
