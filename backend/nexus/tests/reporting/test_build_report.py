import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    CommunicationVerdict, HolisticAdjustmentOut, NarrativeOut, QuestionGradeOut,
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
    }


@pytest.mark.asyncio
async def test_build_report_advances_a_strong_must_have():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=["built an ETL in Python"],
                level="solid", overridden=False, override_reason=None))), \
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
    }
    evidence = SessionEvidence.model_validate(ev_dict)
    questions = [{"id": "q1", "text": "Python?", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = []  # deliberately empty — identity must come from evidence.signals[]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=[],
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
async def test_manifest_carries_template_provenance():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=["built an ETL in Python"],
                level="solid", overridden=False, override_reason=None))), \
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
        report = await build_report(
            evidence=evidence, questions=questions,
            signal_metadata=signal_metadata, correlation_id="cid-prov",
            bank_id="bank-123", signal_snapshot_id="snap-456",
        )
    m = report.scoring_manifest
    assert m.bank_id == "bank-123"
    assert m.signal_snapshot_id == "snap-456"
    assert m.scorer_code_version == "qa-1"


@pytest.mark.asyncio
async def test_unquestioned_eligibility_signal_is_not_scored_or_gap_flagged():
    """AI-screening banks now test SKILLS only — eligibility signals (years/degree/
    cert, purpose=eligibility) are filtered out of generation, so they get NO
    question. Such a signal must NOT appear as a gap, must NOT lower the score, and
    must NOT show up in the report at all (it's pre-screened, outside the AI screen's
    remit). The scored denominator is the PRIMARY set (= signals owned by an asked
    question); an un-questioned signal is never in it.
    """
    # One asked skill question (python). The bank ALSO carries an eligibility signal
    # ("years_experience", a must-have) that was deliberately NOT generated into any
    # question — it exists only in signal_metadata, never in evidence.questions.
    evidence = SessionEvidence.model_validate(_evidence_dict())  # questions = [python] only
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [
        {"value": "python", "type": "competency", "weight": 3,
         "knockout": True, "priority": "required"},
        # Un-questioned eligibility must-have — pre-screened, no question asked.
        {"value": "years_experience", "type": "experience", "weight": 3,
         "knockout": True, "priority": "required", "purpose": "eligibility"},
    ]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=["built an ETL in Python"],
                level="solid", overridden=False, override_reason=None))), \
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

    assessed = {s.signal for s in report.signal_assessments}
    # The un-questioned eligibility signal is absent from the report entirely.
    assert "years_experience" not in assessed
    assert assessed == {"python"}
    # It did NOT drag the candidate down: strong asked skill → advance.
    assert report.verdict == "advance"
    assert report.scores["overall"].score is not None
    # It is not in the scored level_map (the audit denominator) either.
    level_map = report.scoring_manifest.evidence_grounding_summary["level_map"]
    assert "years_experience" not in level_map
    assert set(level_map) == {"python"}


@pytest.mark.asyncio
async def test_zero_knockout_bank_produces_a_normal_verdict():
    """AI-screening banks now typically carry ZERO is_mandatory/knockout questions
    (skill must-haves are scenario-graded; eligibility knockouts are pre-screened).
    A session with no knockout/must-have signals must still produce a normal,
    score-driven verdict — no error, no spurious auto-fail.
    """
    # python is a NON-knockout signal; no signal in the bank is a must-have.
    evidence = SessionEvidence.model_validate(_evidence_dict())
    # Flip the engine-side identity to non-knockout too, so identity recovery can't
    # reintroduce a must-have from evidence.signals[].
    ev = evidence.model_copy(deep=True)
    object.__setattr__(ev.signals[0], "knockout", False)
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": False, "priority": "preferred"}]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=["built an ETL in Python"],
                level="solid", overridden=False, override_reason=None))), \
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
        report = await build_report(evidence=ev, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    # No must-have anywhere → no knockout gate fires → score-driven verdict.
    assert report.verdict in {"advance", "borderline", "reject"}
    assert report.verdict == "advance"  # strong solo skill, no ceiling cap
    assert all(s.knockout is False for s in report.signal_assessments)


@pytest.mark.asyncio
async def test_build_report_populates_question_cards():
    evidence = SessionEvidence.model_validate(_evidence_dict())
    questions = [{"id": "q1", "text": "Tell me about Python", "signal_values": ["python"],
                  "rubric": {}, "question_kind": "technical_depth", "primary_signal": "python"}]
    signal_metadata = [{"value": "python", "type": "competency", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=["built an ETL in Python"],
                level="solid", overridden=False, override_reason=None))), \
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
