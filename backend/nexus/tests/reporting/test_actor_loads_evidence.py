from app.modules.reporting.actors import _build_report_inputs_from_session


def test_inputs_helper_requires_session_evidence_json():
    class _Sess:
        session_evidence_json = None
    assert _build_report_inputs_from_session(_Sess()) is None


def test_inputs_helper_parses_evidence():
    class _Sess:
        session_evidence_json = {
            "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                     "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                     "duration_s": 1.0, "time_budget_s": 1.0, "completion": "completed",
                     "questions_asked": 0, "questions_core_total": 0, "questions_overflow_asked": 0},
            "signals": [], "notes": [], "questions": [], "transcript": [], "knockout": None}
    ev = _build_report_inputs_from_session(_Sess())
    assert ev.meta.session_id == "s1"
