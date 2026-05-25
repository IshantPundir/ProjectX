"""Tests for build_report orchestration (Task 15).

Calibration case: e4072361 — a weak bluffer who hits buzzwords but delivers
no substantive answers.  With grade_answer uniformly returning below_bar with
red_flags, the knockout signals must fail and the verdict must be reject.

The second test uses a minimal synthetic fixture to verify that questions never
delivered to the candidate produce not_assessed signals that are excluded from
the dimension score mean (score → None).
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from app.modules.reporting.schemas import AnswerRating
from app.modules.reporting.service import build_report

FIX = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Calibration test — e4072361
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weak_bluffer_is_confident_reject():
    """All answers graded below_bar with red_flags → knockout(s) fail → reject."""
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    transcript = json.loads((FIX / "e4072361_transcript.json").read_text())
    bank = json.loads((FIX / "job_bank_slice.json").read_text())

    async def fake_grade(*, question, transcript_excerpt, correlation_id, n_samples=1):
        return AnswerRating(
            question_id=question["id"],
            level="below_bar",
            evidence_quotes=[],
            red_flags_hit=["buzzwords"],
            justification="thin",
            grounded=True,
        )

    with patch("app.modules.reporting.service.grade_answer", side_effect=fake_grade):
        report = await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=bank["questions"],
            signal_metadata=bank["signal_metadata"],
            correlation_id="c1",
        )

    assert report.verdict == "reject"
    assert any(k.status == "failed" for k in report.knockout_results), (
        f"Expected at least one failed knockout; got: {report.knockout_results}"
    )
    # Technical dimension must have been assessed (questions were delivered)
    assert report.dimension_scores["technical"].score is not None, (
        "Technical dimension score should not be None when questions were answered"
    )


# ---------------------------------------------------------------------------
# Synthetic test — not_assessed signals excluded from dimension score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_assessed_signals_excluded():
    """A question never delivered → its signal stays not_assessed → excluded
    from the dimension weighted mean → dimension score is None."""

    # Minimal transcript: only one question delivered (q1) out of two in bank.
    # q2 has no agent turn with its question_id → never delivered → not_assessed.
    transcript = [
        {
            "role": "agent",
            "text": "What is your experience?",
            "timestamp_ms": 1000,
            "question_id": "q1",
        },
        {
            "role": "candidate",
            "text": "I have worked for several years in this field doing various things.",
            "timestamp_ms": 2000,
            "question_id": "q1",
        },
        # q2 is never delivered (no agent turn with question_id=q2)
    ]

    # Minimal envelope — no events needed for this test
    envelope = {"events": []}

    # Two questions, each covering a different signal.
    # q1 covers signal-a (behavioral), q2 covers signal-b (behavioral).
    questions = [
        {
            "id": "q1",
            "text": "What is your experience?",
            "signal_values": ["signal-a"],
            "rubric": {
                "below_bar": "vague",
                "meets_bar": "some detail",
                "excellent": "strong detail",
            },
            "positive_evidence": [],
            "red_flags": [],
        },
        {
            "id": "q2",
            "text": "Describe a challenge.",
            "signal_values": ["signal-b"],
            "rubric": {
                "below_bar": "vague",
                "meets_bar": "some detail",
                "excellent": "strong detail",
            },
            "positive_evidence": [],
            "red_flags": [],
        },
    ]

    # Both signals are behavioral so they go into the behavioral dimension.
    # signal-a will be assessed (meets_bar from q1), signal-b will be not_assessed.
    signal_metadata = [
        {
            "value": "signal-a",
            "type": "behavioral",
            "weight": 2,
            "knockout": False,
            "priority": "required",
        },
        {
            "value": "signal-b",
            "type": "behavioral",
            "weight": 2,
            "knockout": False,
            "priority": "required",
        },
    ]

    async def fake_grade(*, question, transcript_excerpt, correlation_id, n_samples=1):
        return AnswerRating(
            question_id=question["id"],
            level="meets_bar",
            evidence_quotes=[],
            red_flags_hit=[],
            justification="decent answer",
            grounded=True,
        )

    with patch("app.modules.reporting.service.grade_answer", side_effect=fake_grade):
        report = await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=questions,
            signal_metadata=signal_metadata,
            correlation_id="c2",
        )

    # signal-b was never delivered → not_assessed → excluded from behavioral mean.
    # signal-a was delivered and graded meets_bar → behavioral score = 70.
    # The dimension should have a score (only assessed signal-a contributes).
    beh = report.dimension_scores.get("behavioral")
    assert beh is not None, "behavioral dimension must be present"
    # Only signal-a (weight=2, score=70) is assessed; coverage = 2/4 = 0.5
    assert beh.score == 70, f"Expected 70 for meets_bar; got {beh.score}"
    assert beh.coverage < 1.0, (
        f"Coverage should be < 1.0 (signal-b not assessed); got {beh.coverage}"
    )

    # Verify signal-b is in the scorecards with state not_assessed
    not_assessed = [s for s in report.signal_scorecards if s.value == "signal-b"]
    assert not_assessed, "signal-b should appear in signal_scorecards"
    assert not_assessed[0].state == "not_assessed"
    assert not_assessed[0].score is None
