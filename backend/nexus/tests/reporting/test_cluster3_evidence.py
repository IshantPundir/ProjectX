"""Cluster 3 — report evidence plumbing + D5 (eliciting-question rubric).

Covers:
  - SignalRecheckOut.needs_verification / verification_note (explanatory human-verify flag).
  - recheck_signal passes question_kind into the prompt.
  - _eliciting_question keys on the question that produced the evidence (from_question_id),
    not the first signal_values match (the D5 shared-signal defeat).
  - _scorecard_evidence falls back to engine supporting notes when recheck has no quotes.
  - build_report threads per-signal quotes + transcript + human_verify into the narrative payload,
    and a shared-signal experience must-have is still re-checked (old factual-gate skip is gone).
"""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, patch

from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.schemas import (
    CommunicationVerdict, DecisionOut, HolisticAdjustmentOut, MethodologyOut,
    NarrativeOut, SignalRecheckOut, WhyColumn,
)


# ---------------------------------------------------------------------------
# A4 — schema: explanatory human-verify flag
# ---------------------------------------------------------------------------

def test_signal_recheck_out_defaults_needs_verification_false():
    rc = SignalRecheckOut(justification="x", level="solid")
    assert rc.needs_verification is False
    assert rc.verification_note is None


def test_signal_recheck_out_accepts_verification_note():
    rc = SignalRecheckOut(
        justification="bare value only", level="thin",
        needs_verification=True, verification_note="missing platform/employer/scale")
    assert rc.needs_verification is True
    assert rc.verification_note == "missing platform/employer/scale"


# ---------------------------------------------------------------------------
# A3/A2 — pure helpers (D5 eliciting-question + scorecard evidence fallback)
# ---------------------------------------------------------------------------

from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan  # noqa: E402
from app.modules.reporting.service import (  # noqa: E402
    _eliciting_question, _narrative_notes, _scorecard_evidence,
)


def _note(signal, qid, stance="supports", texture="concrete", quote="q", seq=1, via_probe=False):
    return EvidenceNote(seq=seq, turn_ref=f"t-{seq}", signal=signal, stance=stance, texture=texture,
                        quote=quote, span=TimeSpan(start_ms=0, end_ms=1),
                        from_question_id=qid, via_probe=via_probe)


def test_eliciting_question_uses_from_question_id_not_first_signal_match():
    # The signal is shared by a technical_scenario (first in signal_values order, NEVER reached)
    # and an experience_check (which actually elicited the supporting note). The D5 bug was that
    # q_by_signal handed the technical_scenario rubric to recheck. The eliciting note must win.
    q_exp = {"id": "q_exp", "question_kind": "experience_check", "text": "how many years?"}
    q_tech = {"id": "q_tech", "question_kind": "technical_scenario", "text": "design X"}
    q_by_id = {"q_exp": q_exp, "q_tech": q_tech}
    notes_by_signal = {"years": [_note("years", "q_exp", quote="Eight, nine years")]}
    q_by_signal = {"years": q_tech}  # the WRONG (first signal_values) match

    elic = _eliciting_question("years", notes_by_signal, q_by_id, q_by_signal)
    assert elic is q_exp


def test_eliciting_question_falls_back_to_signal_match_when_no_note():
    q_tech = {"id": "q_tech", "question_kind": "technical_scenario"}
    elic = _eliciting_question("sig", {}, {"q_tech": q_tech}, {"sig": q_tech})
    assert elic is q_tech


def test_scorecard_evidence_prefers_recheck_quotes():
    rc = {"sig": SignalRecheckOut(evidence_quotes=["graded quote"], justification="x", level="solid")}
    notes = {"sig": [_note("sig", "q", quote="note quote")]}
    assert _scorecard_evidence("sig", rc, notes) == ["graded quote"]


def test_scorecard_evidence_falls_back_to_supporting_notes():
    rc = {}  # recheck did not run for this signal
    notes = {"sig": [_note("sig", "q", stance="supports", quote="real supporting quote")]}
    assert _scorecard_evidence("sig", rc, notes) == ["real supporting quote"]


def test_scorecard_evidence_excludes_contradicting_notes():
    rc = {"sig": SignalRecheckOut(evidence_quotes=[], justification="x", level="thin")}
    notes = {"sig": [_note("sig", "q", stance="contradicts", quote="disclaim")]}
    assert _scorecard_evidence("sig", rc, notes) == []


def test_narrative_notes_shape_and_bounded():
    notes = {"sig": [_note("sig", "q", seq=i + 1, quote=f"q{i}") for i in range(20)]}
    out = _narrative_notes("sig", notes)
    assert len(out) <= 6  # NARRATIVE_NOTES_PER_SIGNAL
    assert set(out[0].keys()) == {"quote", "texture", "stance", "via_probe"}


# ---------------------------------------------------------------------------
# A1/A3 — build_report integration: D5 shared-signal + narrative grounding
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
        "knockout": None,
    }


def _bank_questions_q_tech_first():
    # q_tech listed FIRST → q_by_signal["years"] = q_tech (the wrong, never-reached rubric).
    return [
        {"id": "q_tech", "text": "design X", "signal_values": ["years"], "rubric": {},
         "question_kind": "technical_scenario", "primary_signal": "years"},
        {"id": "q_exp", "text": "how many years?", "signal_values": ["years"],
         "rubric": {"meets_bar": "years + platform/employer"}, "question_kind": "experience_check",
         "primary_signal": "years"},
    ]


@pytest.mark.asyncio
async def test_build_report_threads_evidence_and_rechecks_shared_signal():
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

    recheck_mock = AsyncMock(return_value=SignalRecheckOut(
        evidence_quotes=[], justification="bare value", level="thin",
        needs_verification=True, verification_note="missing platform/employer/scale"))

    with patch("app.modules.reporting.service.recheck_signal", new=recheck_mock), \
         patch("app.modules.reporting.service.score_holistic", new=AsyncMock(
            return_value=HolisticAdjustmentOut(delta=0, justification=""))), \
         patch("app.modules.reporting.service.grade_communication", new=AsyncMock(
            return_value=CommunicationVerdict(evidence_quotes=[], justification="", level="adequate"))), \
         patch("app.modules.reporting.service.write_narrative", side_effect=_capture_narrative):
        report = await build_report(evidence=evidence, questions=questions,
                                    signal_metadata=signal_metadata, correlation_id="cid")

    # (1) the shared signal is STILL re-checked (old all-factual skip is gone) ...
    assert recheck_mock.await_count == 1
    # ... and graded against the ELICITING question's kind (experience_check, not technical_scenario)
    assert recheck_mock.await_args.kwargs["question_kind"] == "experience_check"

    gt = captured["gt"]
    # (2) narrative now receives candidate transcript + per-signal quotes + notes
    assert "Eight, nine years" in gt["transcript"]
    years_sig = next(s for s in gt["signals"] if s["signal"] == "years")
    assert any("Eight, nine years" in q for q in years_sig["evidence_quotes"])
    assert years_sig["notes"] and years_sig["notes"][0]["quote"] == "Eight, nine years"
    # (3) the human-verify charity flag is threaded through (explanatory, not a silent penalty)
    assert {"signal": "years", "note": "missing platform/employer/scale"} in gt["human_verify"]

    # (4) scorecard evidence falls back to the engine's supporting notes (recheck returned none)
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

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(evidence_quotes=[], justification="ok", level="solid"))), \
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
    """#3 contract: an unconfirmed must-have (re-check grades it `thin` against the bank rubric)
    is held at BORDERLINE for human review — never silently advanced — with the verify flag
    explaining what to confirm. This is what the rubric-tier re-check produces on real data."""
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

    with patch("app.modules.reporting.service.recheck_signal", new=AsyncMock(
            return_value=SignalRecheckOut(
                evidence_quotes=[], justification="bare value, below meets_bar", level="thin",
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
