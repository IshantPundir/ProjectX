"""Cluster 3 — report evidence plumbing + question-anchored grading.

Covers:
  - QuestionGradeOut.needs_verification / verification_note (explanatory human-verify flag).
  - grade_question is called per-ASKED-question with the question's full card (so a shared
    signal is graded against the question that actually elicited the answer, never a
    never-reached sibling — the D5 shared-signal defeat is structural now).
  - _scorecard_evidence prefers the dedicated grade's quotes, falls back to engine
    supporting notes when the grade has none.
  - build_report threads per-signal quotes + transcript + human_verify into the narrative
    payload, and per-question cards stay QUESTION-anchored.
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.schemas import (
    CommunicationVerdict, DecisionOut, HolisticAdjustmentOut, MethodologyOut,
    NarrativeOut, QuestionGradeOut, WhyColumn,
)


# ---------------------------------------------------------------------------
# A4 — schema: explanatory human-verify flag
# ---------------------------------------------------------------------------

def test_question_grade_out_defaults_needs_verification_false():
    g = QuestionGradeOut(level="solid")
    assert g.needs_verification is False
    assert g.verification_note is None


def test_question_grade_out_accepts_verification_note():
    g = QuestionGradeOut(
        level="thin", needs_verification=True,
        verification_note="missing platform/employer/scale")
    assert g.needs_verification is True
    assert g.verification_note == "missing platform/employer/scale"


# ---------------------------------------------------------------------------
# A2 — pure helper (scorecard evidence prefer-grade / fallback-notes)
# ---------------------------------------------------------------------------

from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan  # noqa: E402
from app.modules.reporting.service import (  # noqa: E402
    _narrative_notes, _scorecard_evidence,
)


def _note(signal, qid, stance="supports", texture="concrete", quote="q", seq=1, via_probe=False):
    return EvidenceNote(seq=seq, turn_ref=f"t-{seq}", signal=signal, stance=stance, texture=texture,
                        quote=quote, span=TimeSpan(start_ms=0, end_ms=1),
                        from_question_id=qid, via_probe=via_probe)


def test_scorecard_evidence_prefers_grade_quotes():
    grade_by_sig = {"sig": QuestionGradeOut(evidence_quotes=["graded quote"], level="solid")}
    notes = {"sig": [_note("sig", "q", quote="note quote")]}
    assert _scorecard_evidence("sig", grade_by_sig, notes) == ["graded quote"]


def test_scorecard_evidence_falls_back_to_supporting_notes():
    grade_by_sig = {}  # no dedicated grade for this signal
    notes = {"sig": [_note("sig", "q", stance="supports", quote="real supporting quote")]}
    assert _scorecard_evidence("sig", grade_by_sig, notes) == ["real supporting quote"]


def test_scorecard_evidence_excludes_contradicting_notes():
    grade_by_sig = {"sig": QuestionGradeOut(evidence_quotes=[], level="thin")}
    notes = {"sig": [_note("sig", "q", stance="contradicts", quote="disclaim")]}
    assert _scorecard_evidence("sig", grade_by_sig, notes) == []


def test_narrative_notes_shape_and_bounded():
    notes = {"sig": [_note("sig", "q", seq=i + 1, quote=f"q{i}") for i in range(20)]}
    out = _narrative_notes("sig", notes)
    assert len(out) <= 6  # NARRATIVE_NOTES_PER_SIGNAL
    assert set(out[0].keys()) == {"quote", "texture", "stance", "via_probe"}


# ---------------------------------------------------------------------------
# A1/A3 — build_report integration: shared-signal + narrative grounding
# ---------------------------------------------------------------------------

def _shared_signal_evidence_dict():
    """Experience must-have shared between an experience_check (answered) and a
    technical_scenario (never reached) — the exact D5 shape from session f2fd4b03."""
    return {
        "meta": {"session_id": "s1", "job_id": "j1", "candidate_id": "c1", "stage_id": "st1",
                 "started_at": "2026-06-08T10:00:00Z", "ended_at": "2026-06-08T10:20:00Z",
                 "duration_s": 1200.0, "time_budget_s": 1200.0, "completion": "completed",
                 "questions_asked": 1, "questions_core_total": 2, "questions_overflow_asked": 0},
        "signals": [{"signal": "years", "signal_type": "experience", "weight": 3,
                     "priority": "required", "knockout": True, "provenance": "asked_directly"}],
        "notes": [{"seq": 1, "turn_ref": "t-1", "signal": "years", "stance": "supports",
                   "texture": "thin", "quote": "Eight, nine years",
                   "span": {"start_ms": 0, "end_ms": 1}, "from_question_id": "q_exp",
                   "via_probe": False}],
        "questions": [
            {"question_id": "q_exp", "primary_signal": "years", "tier": "core",
             "outcome": "asked", "closure": "tapped_out", "probes_used": [], "probes_available": 3},
            {"question_id": "q_tech", "primary_signal": "years", "tier": "core",
             "outcome": "not_reached", "closure": None, "probes_used": [], "probes_available": 3},
        ],
        "transcript": [{"turn_ref": "t-1", "speaker": "candidate", "text": "Eight, nine years",
                        "span": {"start_ms": 0, "end_ms": 1}, "pre_turn_gap_ms": 0}],
    }


def _bank_questions_q_tech_first():
    # q_tech listed FIRST (never reached); q_exp is the answered experience_check.
    return [
        {"id": "q_tech", "text": "design X", "signal_values": ["years"], "rubric": {},
         "question_kind": "technical_scenario", "primary_signal": "years"},
        {"id": "q_exp", "text": "how many years?", "signal_values": ["years"],
         "rubric": {"meets_bar": "years + platform/employer"}, "question_kind": "experience_check",
         "primary_signal": "years"},
    ]


@pytest.mark.asyncio
async def test_build_report_grades_only_asked_question_with_its_card():
    from app.modules.reporting.service import build_report

    evidence = SessionEvidence.model_validate(_shared_signal_evidence_dict())
    questions = _bank_questions_q_tech_first()
    signal_metadata = [{"value": "years", "type": "experience", "weight": 3,
                        "knockout": True, "priority": "required"}]

    captured = {}

    async def _capture_narrative(*, ground_truth_json, correlation_id):
        captured["gt"] = json.loads(ground_truth_json)
        return NarrativeOut(
            decision=DecisionOut(headline="", why_positive=WhyColumn(title="", body=""),
                                 why_negative=WhyColumn(title="", body="")),
            quick_summary="", strengths=[], concerns=[], questions=[],
            methodology=MethodologyOut(note="", charity_flags=[]))

    grade_mock = AsyncMock(return_value=QuestionGradeOut(
        evidence_quotes=[], level="thin",
        needs_verification=True, verification_note="missing platform/employer/scale"))

    with patch("app.modules.reporting.service.grade_question", new=grade_mock), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", side_effect=_capture_narrative):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    # (1) Only the ASKED question (q_exp) is graded — q_tech (not_reached) is skipped ...
    assert grade_mock.await_count == 1
    # ... against ITS OWN full bank card (experience_check, not the technical_scenario sibling)
    assert grade_mock.await_args.kwargs["question"]["question_kind"] == "experience_check"
    assert grade_mock.await_args.kwargs["question"]["id"] == "q_exp"

    gt = captured["gt"]
    # (2) narrative now receives candidate transcript + per-signal quotes + notes
    assert "Eight, nine years" in gt["transcript"]
    years_sig = next(s for s in gt["signals"] if s["signal"] == "years")
    assert any("Eight, nine years" in q for q in years_sig["evidence_quotes"])
    assert years_sig["notes"] and years_sig["notes"][0]["quote"] == "Eight, nine years"
    # (3) the human-verify charity flag is threaded through (explanatory, not a silent penalty)
    assert {"signal": "years", "note": "missing platform/employer/scale"} in gt["human_verify"]

    # (4) scorecard evidence falls back to the engine's supporting notes (grade returned none)
    yrs = next(s for s in report.signal_assessments if s.signal == "years")
    assert yrs.evidence == ["Eight, nine years"]


@pytest.mark.asyncio
async def test_not_reached_question_card_is_not_passed_and_has_no_sibling_quote():
    """Per-question cards must be QUESTION-anchored. q_tech is not_reached and shares its
    primary_signal with the answered q_exp; its card must NOT inherit q_exp's 'passed'
    badge or its quote ('Eight, nine years'). Reported bug on session f2fd4b03."""
    from app.modules.reporting.service import build_report

    evidence = SessionEvidence.model_validate(_shared_signal_evidence_dict())
    questions = _bank_questions_q_tech_first()
    signal_metadata = [{"value": "years", "type": "experience", "weight": 3,
                        "knockout": True, "priority": "required"}]

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(evidence_quotes=[], level="solid"))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", new=AsyncMock(
            return_value=NarrativeOut(
                decision=DecisionOut(headline="", why_positive=WhyColumn(title="", body=""),
                                     why_negative=WhyColumn(title="", body="")),
                quick_summary="", strengths=[], concerns=[], questions=[],
                methodology=MethodologyOut(note="", charity_flags=[])))):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    card_by_id = {q.question_id: q for q in report.questions}
    # the answered experience question owns its quote + a "passed" badge
    assert card_by_id["q_exp"].candidate_quote == "Eight, nine years"
    assert card_by_id["q_exp"].status_badge == "passed"
    # the NEVER-REACHED question must not inherit the sibling's quote or badge
    assert card_by_id["q_tech"].candidate_quote == ""
    assert card_by_id["q_tech"].status_badge == "not_attempted"
    assert card_by_id["q_tech"].status_tone == "neutral"


@pytest.mark.asyncio
async def test_thin_must_have_is_borderline_not_advance():
    """#3 contract: an unconfirmed must-have (graded `thin` against its bank card) is held
    at BORDERLINE for human review — never silently advanced — with the verify flag
    explaining what to confirm."""
    from app.modules.reporting.service import build_report

    evidence = SessionEvidence.model_validate(_shared_signal_evidence_dict())
    questions = _bank_questions_q_tech_first()
    signal_metadata = [{"value": "years", "type": "experience", "weight": 3,
                        "knockout": True, "priority": "required"}]

    captured = {}

    async def _cap(*, ground_truth_json, correlation_id):
        captured["gt"] = json.loads(ground_truth_json)
        return NarrativeOut(
            decision=DecisionOut(headline="", why_positive=WhyColumn(title="", body=""),
                                 why_negative=WhyColumn(title="", body="")),
            quick_summary="", strengths=[], concerns=[], questions=[],
            methodology=MethodologyOut(note="", charity_flags=[]))

    with patch("app.modules.reporting.service.grade_question", new=AsyncMock(
            return_value=QuestionGradeOut(
                evidence_quotes=[], level="thin",
                needs_verification=True, verification_note="confirm platform/employer/scale"))), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", side_effect=_cap):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    assert report.verdict == "borderline"          # unconfirmed must-have → human review, not advance
    assert report.overall_score is not None and report.overall_score < 65
    assert {"signal": "years", "note": "confirm platform/employer/scale"} in captured["gt"]["human_verify"]
