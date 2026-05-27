"""Integration tests for build_report orchestration (Task 8).

These mock the two LLM layers (recheck_signal + write_narrative) plus
grade_communication, and assert the report is complete (non-null
technical/overall) and that a knockout_close → reject.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.modules.reporting.schemas import (
    CommunicationVerdict,
    DecisionOut,
    HolisticAdjustmentOut,
    MethodologyOut,
    NarrativeOut,
    SignalRecheckOut,
    WhyColumn,
)
from app.modules.reporting.service import build_report


def _signal_metadata():
    return [
        {"value": "4+ years total professional experience", "type": "experience",
         "weight": 3, "knockout": True, "priority": "required"},
        {"value": "Designing and implementing AI-driven workflows", "type": "competency",
         "weight": 3, "knockout": False, "priority": "required"},
    ]


def _questions():
    return [
        {"id": "q1", "position": 0, "text": "Years of experience?",
         "signal_values": ["4+ years total professional experience"], "estimated_minutes": 1.0,
         "is_mandatory": True, "follow_ups": [], "positive_evidence": [], "red_flags": [],
         "rubric": {"excellent": "", "meets_bar": "", "below_bar": ""},
         "evaluation_hint": "", "question_kind": "experience_check", "difficulty": "easy",
         "primary_signal": "4+ years total professional experience"},
        {"id": "q2", "position": 1, "text": "Design an AI workflow.",
         "signal_values": ["Designing and implementing AI-driven workflows"],
         "estimated_minutes": 3.0, "is_mandatory": True, "follow_ups": [],
         "positive_evidence": [], "red_flags": [], "rubric": {"excellent": "", "meets_bar": "",
         "below_bar": ""}, "evaluation_hint": "", "question_kind": "technical_scenario",
         "difficulty": "medium", "primary_signal": "Designing and implementing AI-driven workflows"},
    ]


def _envelope():
    return {"events": [
        {"kind": "directive.delivered", "t_ms": 1000, "payload": {"act": "ASK", "turn_ref": "t1"}},
        {"kind": "turn.decision", "t_ms": 2000, "payload": {
            "turn_ref": "t1", "active_question_id": "q1", "candidate_quote": "About six years.",
            "attributed_signals": ["4+ years total professional experience"], "grade": "concrete",
            "coverage_delta": {"4+ years total professional experience": "sufficient"},
            "move": "advance"}},
        {"kind": "directive.delivered", "t_ms": 3000, "payload": {"act": "ACK_ADVANCE", "turn_ref": "t2"}},
        {"kind": "turn.decision", "t_ms": 4000, "payload": {
            "turn_ref": "t2", "active_question_id": "q2",
            "candidate_quote": "A recipe triggered on a ticket, an extraction layer...",
            "attributed_signals": ["Designing and implementing AI-driven workflows"],
            "grade": "thin", "coverage_delta": {"Designing and implementing AI-driven workflows": "partial"},
            "move": "probe"}},
    ]}


@pytest.mark.asyncio
async def test_build_report_uses_engine_map_and_is_complete():
    coverage = {"4+ years total professional experience": "sufficient",
                "Designing and implementing AI-driven workflows": "partial"}

    async def fake_recheck(*, signal_def, engine_state, **kw):
        return SignalRecheckOut(evidence_quotes=[], justification="", grade="thin",
                                state=engine_state, overridden=False, override_reason=None)

    narrative = NarrativeOut(
        decision=DecisionOut(headline="Borderline.",
                             why_positive=WhyColumn(title="A", body="b"),
                             why_negative=WhyColumn(title="C", body="d")),
        quick_summary="s", strengths=[], concerns=[],
        questions=[], methodology=MethodologyOut(note="n", charity_flags=[]))

    with patch("app.modules.reporting.service.recheck_signal", side_effect=fake_recheck), \
         patch("app.modules.reporting.service.write_narrative", AsyncMock(return_value=narrative)), \
         patch("app.modules.reporting.service.grade_communication",
               AsyncMock(return_value=CommunicationVerdict(evidence_quotes=[], justification="",
                                                           level="adequate"))), \
         patch("app.modules.reporting.service.score_holistic",
               AsyncMock(return_value=HolisticAdjustmentOut(delta=2, justification="solid depth"))):
        report = await build_report(
            transcript=[{"role": "candidate", "text": "About six years."}],
            envelope=_envelope(), coverage_summary=coverage,
            questions=_questions(), signal_metadata=_signal_metadata(), correlation_id="c1")

    assert report.scores["overall"].score is not None      # NOT incomplete
    assert report.scores["technical"].score is not None
    assert report.verdict == "borderline"                  # knockout 4+yrs sufficient, AI partial
    assert len(report.questions) == 2
    assert report.scores["communication"].score == 70      # adequate
    assert any(sa.signal == "4+ years total professional experience"
               for sa in report.signal_assessments)
    # Task 7: new ScoreOut fields
    overall_out = report.scores["overall"]
    assert overall_out.session_score is not None
    assert overall_out.holistic_delta == 2
    expected_overall = min(100, overall_out.session_score + 2)
    assert overall_out.score == expected_overall


@pytest.mark.asyncio
async def test_build_report_knockout_close_is_reject():
    env = _envelope()
    env["events"].append({"kind": "turn.decision", "t_ms": 5000, "payload": {
        "turn_ref": "t3", "move": "knockout_close", "attributed_signals": [],
        "coverage_delta": {"Designing and implementing AI-driven workflows": "failed"},
        "candidate_quote": "I've never done that."}})
    coverage = {"4+ years total professional experience": "sufficient",
                "Designing and implementing AI-driven workflows": "failed"}

    async def fake_recheck(*, signal_def, engine_state, **kw):
        return SignalRecheckOut(evidence_quotes=[], justification="", grade="null",
                                state=engine_state, overridden=False, override_reason=None)

    narrative = NarrativeOut(
        decision=DecisionOut(headline="Not recommended.",
                             why_positive=WhyColumn(title="", body=""),
                             why_negative=WhyColumn(title="", body="")),
        quick_summary="", strengths=[], concerns=[], questions=[],
        methodology=MethodologyOut(note="", charity_flags=[]))

    with patch("app.modules.reporting.service.recheck_signal", side_effect=fake_recheck), \
         patch("app.modules.reporting.service.write_narrative", AsyncMock(return_value=narrative)), \
         patch("app.modules.reporting.service.grade_communication",
               AsyncMock(return_value=CommunicationVerdict(evidence_quotes=[], justification="",
                                                           level="weak"))), \
         patch("app.modules.reporting.service.score_holistic",
               AsyncMock(return_value=HolisticAdjustmentOut(delta=0, justification="no signal"))):
        report = await build_report(
            transcript=[], envelope=env, coverage_summary=coverage,
            questions=_questions(), signal_metadata=_signal_metadata(), correlation_id="c1")

    assert report.verdict == "reject"


@pytest.mark.asyncio
async def test_factual_gate_signal_is_not_rechecked():
    # The 4+ years experience signal is covered by an experience_check question.
    # Even if the re-check WOULD downgrade it, build_report must keep the engine's sufficient.
    coverage = {"4+ years total professional experience": "sufficient",
                "Designing and implementing AI-driven workflows": "partial"}

    downgrade_calls = []

    async def fake_recheck(*, signal_def, engine_state, **kw):
        downgrade_calls.append(signal_def.value)
        # pretend the model wants to downgrade everything to 'partial'
        return SignalRecheckOut(evidence_quotes=[], justification="", grade="thin",
                                state="partial", overridden=True, override_reason="x")

    narrative = NarrativeOut(
        decision=DecisionOut(headline="h", why_positive=WhyColumn(title="", body=""),
                             why_negative=WhyColumn(title="", body="")),
        quick_summary="", strengths=[], concerns=[], questions=[],
        methodology=MethodologyOut(note="", charity_flags=[]))

    with patch("app.modules.reporting.service.recheck_signal", side_effect=fake_recheck), \
         patch("app.modules.reporting.service.write_narrative", AsyncMock(return_value=narrative)), \
         patch("app.modules.reporting.service.grade_communication",
               AsyncMock(return_value=CommunicationVerdict(evidence_quotes=[], justification="",
                                                           level="adequate"))), \
         patch("app.modules.reporting.service.score_holistic",
               AsyncMock(return_value=HolisticAdjustmentOut(delta=0, justification="no signal"))):
        report = await build_report(
            transcript=[], envelope=_envelope(), coverage_summary=coverage,
            questions=_questions(), signal_metadata=_signal_metadata(), correlation_id="c1")

    # the factual experience gate was NOT passed to recheck...
    assert "4+ years total professional experience" not in downgrade_calls
    # ...and its final state stayed the engine's 'sufficient'
    exp = next(sa for sa in report.signal_assessments
               if sa.signal == "4+ years total professional experience")
    assert exp.final_state == "sufficient"
    assert exp.overridden is False
    # the substantive AI-workflow signal WAS re-checked (it's a technical_scenario)
    assert "Designing and implementing AI-driven workflows" in downgrade_calls
