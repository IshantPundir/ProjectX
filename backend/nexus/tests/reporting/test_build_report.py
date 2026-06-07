import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    CommunicationVerdict, HolisticAdjustmentOut, NarrativeOut, SignalRecheckOut,
    DecisionOut, MethodologyOut, WhyColumn,
)


def _evidence_dict():
    return {
        "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                 "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                 "duration_s": 1200.0, "time_budget_s": 1200.0, "completion": "completed",
                 "questions_asked": 1, "questions_core_total": 1, "questions_overflow_asked": 0},
        "signals": [{"signal": "python", "signal_type": "competency", "weight": 3,
                     "priority": "required", "knockout": True, "provenance": "asked_directly"}],
        "notes": [{"seq": 1, "turn_ref": "t-1", "signal": "python", "stance": "supports",
                   "texture": "concrete", "quote": "built an ETL in Python",
                   "span": {"start_ms": 0, "end_ms": 1}, "from_question_id": "q1", "via_probe": False}],
        "questions": [{"question_id": "q1", "primary_signal": "python", "tier": "core",
                       "outcome": "asked", "closure": "satisfied", "probes_used": [],
                       "probes_available": 2}],
        "transcript": [{"turn_ref": "t-1", "speaker": "candidate", "text": "built an ETL in Python",
                        "span": {"start_ms": 0, "end_ms": 1}, "pre_turn_gap_ms": 0}],
        "knockout": None,
    }


@pytest.mark.asyncio
async def test_build_report_advances_a_strong_must_have():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(evidence_quotes=["built an ETL in Python"],
                justification="real", level="solid", overridden=False, override_reason=None))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification="ok"))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="ok", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="ok", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    assert report.verdict == "advance"
    assert report.engine_version == "v3"
    assert report.scores["overall"].score is not None
    py = next(s for s in report.signal_assessments if s.signal == "python")
    assert py.provenance == "asked_directly"


@pytest.mark.asyncio
async def test_must_have_identity_recovered_from_engine_when_metadata_missing():
    # A must-have primary signal that is ABSENT (probed_absent) and is MISSING from
    # signal_metadata. Its knockout identity must be recovered from evidence.signals[]
    # so the must-have gate still fires (reject), NOT silently treated as non-knockout.
    ev_dict = {
        "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                 "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                 "duration_s": 1.0, "time_budget_s": 1.0, "completion": "completed",
                 "questions_asked": 1, "questions_core_total": 1, "questions_overflow_asked": 0},
        "signals": [{"signal": "python", "signal_type": "competency", "weight": 3,
                     "priority": "required", "knockout": True, "provenance": "probed_absent"}],
        "notes": [],
        "questions": [{"question_id": "q1", "primary_signal": "python", "tier": "core",
                       "outcome": "asked", "closure": "absent", "probes_used": [], "probes_available": 2}],
        "transcript": [],
        "knockout": None,
    }
    evidence = SessionEvidence.model_validate(ev_dict)
    questions = [{"id": "q1", "text": "Python?", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = []  # deliberately empty — identity must come from evidence.signals[]

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(evidence_quotes=[], justification="none",
                level="absent", overridden=False, override_reason=None))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="weak"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    assert report.verdict == "reject"  # must-have absent → reject (identity recovered from engine)
    py = next(s for s in report.signal_assessments if s.signal == "python")
    assert py.knockout is True


@pytest.mark.asyncio
async def test_build_report_populates_question_cards():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(evidence_quotes=["built an ETL in Python"],
                justification="real", level="solid", overridden=False, override_reason=None))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification="ok"))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="ok", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="ok", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    assert len(report.questions) == len(evidence.questions)
    assert all(q.status_badge for q in report.questions)
