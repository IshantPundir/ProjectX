"""Golden end-to-end fixture for the report scorer spine.

Validates that build_report(SessionEvidence) honours the contract:
  - Only PRIMARY signals (= {q.primary_signal for q in ev.questions}) are graded.
  - Secondary cross-credited signals (e.g. 'docker') are excluded from signal_assessments.
  - Uncovered role signals with provenance 'not_reached' that are not any question's
    primary are likewise excluded from the graded denominator.
  - A not_reached primary ('testing') scores at the floor and lowers coverage < 1.0.
  - engine_version == "v3".
"""

import json
import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report
from app.modules.reporting.schemas import (
    CommunicationVerdict,
    HolisticAdjustmentOut,
    NarrativeOut,
    SignalRecheckOut,
    DecisionOut,
    MethodologyOut,
    WhyColumn,
)


@pytest.mark.asyncio
async def test_golden_report_spine():
    ev = SessionEvidence.model_validate(
        json.load(open("tests/reporting/fixtures/session_evidence_golden.json")))

    primary = {q.primary_signal for q in ev.questions}
    # Sanity: the fixture must define exactly 4 primaries.
    assert primary == {"python", "system_design", "collaboration", "testing"}

    questions = [
        {
            "id": q.question_id,
            "text": "Q",
            "signal_values": [q.primary_signal],
            "rubric": {},
            "question_kind": "technical_depth",
            "primary_signal": q.primary_signal,
        }
        for q in ev.questions
    ]
    signal_metadata = [
        {
            "value": s.signal,
            "type": s.signal_type.value,
            "weight": s.weight,
            "knockout": s.knockout,
            "priority": s.priority.value,
        }
        for s in ev.signals
        if s.signal in primary
    ]

    async def _rc(*, signal_def, notes, question_context, engine_level, correlation_id,
                  question_kind=None):
        return SignalRecheckOut(
            evidence_quotes=[],
            justification="keep",
            level=engine_level,
            overridden=False,
            override_reason=None,
        )

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(side_effect=_rc)), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
             return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
             return_value=CommunicationVerdict(
                 evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
             return_value=NarrativeOut(
                 decision=DecisionOut(
                     headline="",
                     why_positive=WhyColumn(title="", body=""),
                     why_negative=WhyColumn(title="", body=""),
                 ),
                 quick_summary="",
                 strengths=[],
                 concerns=[],
                 questions=[],
                 methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(
            evidence=ev,
            questions=questions,
            signal_metadata=signal_metadata,
            correlation_id="cid",
        )

    # Only PRIMARY signals are graded — secondary 'docker' and uncovered 'kubernetes'
    # (neither is any question's primary_signal) must be excluded from signal_assessments.
    graded_signals = {s.signal for s in report.signal_assessments}
    assert graded_signals == primary, (
        f"Expected graded signals == {primary}, got {graded_signals}. "
        "Check that 'docker' and 'kubernetes' are NOT any question's primary_signal in the fixture."
    )

    # A not_reached primary ('testing') has no notes → scores at the floor and
    # counts as uncovered, so aggregate coverage must be strictly below 1.0.
    assert report.scores["overall"].coverage < 1.0, (
        f"Expected coverage < 1.0 (not_reached primary 'testing' should lower it), "
        f"got {report.scores['overall'].coverage}"
    )

    assert report.engine_version == "v3"
