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

from app.modules.reporting.schemas import AnswerRating, CommunicationVerdict
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

    async def fake_comm(*, transcript_text, correlation_id):
        return CommunicationVerdict(
            evidence_quotes=[], justification="ok", level="adequate"
        )

    with patch(
        "app.modules.reporting.service.grade_answer_consistent", side_effect=fake_grade
    ), patch(
        "app.modules.reporting.service.grade_communication", side_effect=fake_comm
    ):
        report = await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=bank["questions"],
            signal_metadata=bank["signal_metadata"],
            correlation_id="c1",
            n_samples=1,
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
    """not_assessed signals are excluded from the dimension weighted mean —
    only assessed signals' scores count.

    Segmentation is envelope-driven: q1 is delivered via ASK + turn.decision;
    q2 has no directive events → never delivered → not_assessed.
    """
    # Transcript is still passed (used for the communication dimension),
    # but question_id values on turns are ignored for segmentation.
    transcript = [
        {
            "role": "agent",
            "text": "What is your experience?",
            "timestamp_ms": 1000,
            "question_id": None,
        },
        {
            "role": "candidate",
            "text": "I have worked for several years in this field doing various things.",
            "timestamp_ms": 2000,
            "question_id": None,
        },
    ]

    # Envelope delivers q1 (mandatory, position=0) only.
    # q2 (mandatory, position=1) is never reached — no ASK/ACK_ADVANCE for it.
    envelope = {
        "events": [
            {
                "kind": "directive.delivered",
                "t_ms": 1000,
                "payload": {"act": "ASK", "turn_ref": "t-0"},
            },
            {
                "kind": "turn.decision",
                "t_ms": 2000,
                "payload": {
                    "turn_ref": "t-1",
                    "candidate_quote": (
                        "I have worked for several years"
                        " in this field doing various things."
                    ),
                },
            },
            # Session ends — q2 never delivered.
        ]
    }

    # Two questions, each covering a different signal.
    # q1 covers signal-a (behavioral), q2 covers signal-b (behavioral).
    # Both are mandatory so ordering is position-based: q1(pos=0) first, q2(pos=1) second.
    questions = [
        {
            "id": "q1",
            "text": "What is your experience?",
            "is_mandatory": True,
            "position": 0,
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
            "is_mandatory": True,
            "position": 1,
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

    async def fake_comm(*, transcript_text, correlation_id):
        return CommunicationVerdict(
            evidence_quotes=[], justification="ok", level="adequate"
        )

    with patch(
        "app.modules.reporting.service.grade_answer_consistent", side_effect=fake_grade
    ), patch(
        "app.modules.reporting.service.grade_communication", side_effect=fake_comm
    ):
        report = await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=questions,
            signal_metadata=signal_metadata,
            correlation_id="c2",
            n_samples=1,
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


# ---------------------------------------------------------------------------
# Communication dimension — separate from Overall (Task 15b)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_communication_is_separate_and_not_in_overall():
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

    async def fake_comm(*, transcript_text, correlation_id):
        return CommunicationVerdict(
            evidence_quotes=[], justification="ok", level="adequate"
        )

    with patch(
        "app.modules.reporting.service.grade_answer_consistent", side_effect=fake_grade
    ), patch(
        "app.modules.reporting.service.grade_communication", side_effect=fake_comm
    ):
        report = await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=bank["questions"],
            signal_metadata=bank["signal_metadata"],
            correlation_id="c1",
            n_samples=1,
        )

    # communication dimension present, "adequate" -> 70
    assert report.dimension_scores["communication"].score == 70
    # communication is content-only and NOT folded into Overall:
    # Overall is computed only from JD signals (all below_bar here -> low). Communication=70
    # must NOT raise the Overall. Assert Overall reflects only JD signals (<= below_bar band).
    assert report.overall_score is None or report.overall_score <= 30


# ---------------------------------------------------------------------------
# Selective self-consistency wiring test (I1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selective_self_consistency_routing():
    """Knockout-signal questions are called with n_samples=max_samples;
    non-knockout questions are called with n_samples=1.

    Setup:
    - q-ko  covers 'signal-ko'  (knockout=True)
    - q-ok  covers 'signal-ok'  (knockout=False)
    Both questions are delivered via envelope directive events.
    build_report is called with n_samples=3 (max_samples=3).
    The fake grade_answer_consistent records the n_samples kwarg per call.
    We assert:
      - q-ko  call used n_samples=3
      - q-ok  call used n_samples=1
    """
    # Transcript is still passed (used for the communication dimension);
    # question_id on turns is ignored for segmentation.
    transcript = [
        {
            "role": "candidate",
            "text": "I have used Python for five years in production systems.",
            "timestamp_ms": 2000,
            "question_id": None,
        },
        {
            "role": "candidate",
            "text": "We collaborated daily across time zones on a shared codebase.",
            "timestamp_ms": 4000,
            "question_id": None,
        },
    ]
    # Envelope delivers both q-ko (mandatory, position=0) and q-ok (mandatory, position=1).
    # ASK → q-ko; turn.decision → attributed to q-ko; ACK_ADVANCE → q-ok;
    # turn.decision → attributed to q-ok.
    envelope = {
        "events": [
            {
                "kind": "directive.delivered",
                "t_ms": 1000,
                "payload": {"act": "ASK", "turn_ref": "t-0"},
            },
            {
                "kind": "turn.decision",
                "t_ms": 2000,
                "payload": {
                    "turn_ref": "t-1",
                    "candidate_quote": "I have used Python for five years in production systems.",
                },
            },
            {
                "kind": "directive.delivered",
                "t_ms": 2010,
                "payload": {"act": "ACK_ADVANCE", "turn_ref": "t-1"},
            },
            {
                "kind": "turn.decision",
                "t_ms": 4000,
                "payload": {
                    "turn_ref": "t-2",
                    "candidate_quote": (
                        "We collaborated daily across time zones"
                        " on a shared codebase."
                    ),
                },
            },
        ]
    }

    questions = [
        {
            "id": "q-ko",
            "text": "Tell me about your Python depth.",
            "is_mandatory": True,
            "position": 0,
            "signal_values": ["signal-ko"],
            "rubric": {"below_bar": "none", "meets_bar": "some", "excellent": "deep"},
            "positive_evidence": [],
            "red_flags": [],
        },
        {
            "id": "q-ok",
            "text": "Describe a teamwork situation.",
            "is_mandatory": True,
            "position": 1,
            "signal_values": ["signal-ok"],
            "rubric": {"below_bar": "none", "meets_bar": "some", "excellent": "great"},
            "positive_evidence": [],
            "red_flags": [],
        },
    ]

    signal_metadata = [
        {
            "value": "signal-ko",
            "type": "experience",
            "weight": 2,
            "knockout": True,
            "priority": "required",
        },
        {
            "value": "signal-ok",
            "type": "behavioral",
            "weight": 1,
            "knockout": False,
            "priority": "preferred",
        },
    ]

    # Record (question_id, n_samples) for every grade_answer_consistent call.
    calls: list[tuple[str, int]] = []

    async def fake_grade_consistent(*, question, transcript_excerpt, correlation_id, n_samples=1):
        calls.append((question["id"], n_samples))
        return AnswerRating(
            question_id=question["id"],
            level="meets_bar",
            evidence_quotes=[],
            red_flags_hit=[],
            justification="ok",
            grounded=True,
        )

    async def fake_comm(*, transcript_text, correlation_id):
        return CommunicationVerdict(
            evidence_quotes=[], justification="ok", level="adequate"
        )

    with patch(
        "app.modules.reporting.service.grade_answer_consistent",
        side_effect=fake_grade_consistent,
    ), patch(
        "app.modules.reporting.service.grade_communication", side_effect=fake_comm
    ):
        await build_report(
            transcript=transcript,
            envelope=envelope,
            questions=questions,
            signal_metadata=signal_metadata,
            correlation_id="c-sel",
            n_samples=3,
        )

    # Exactly two grading calls (one per delivered question).
    assert len(calls) == 2, f"Expected 2 grading calls; got {len(calls)}: {calls}"

    calls_by_qid = dict(calls)
    assert calls_by_qid["q-ko"] == 3, (
        f"Knockout question should use n_samples=3; got {calls_by_qid['q-ko']}"
    )
    assert calls_by_qid["q-ok"] == 1, (
        f"Non-knockout question should use n_samples=1; got {calls_by_qid['q-ok']}"
    )


# ---------------------------------------------------------------------------
# persist_report tests (Task 16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_report_creates_ready_row(db):
    from sqlalchemy import select

    from app.modules.reporting.models import SessionReport
    from app.modules.reporting.schemas import ReportRead, SummaryOut
    from app.modules.reporting.service import persist_report
    from app.modules.session.models import Session as SessionRow
    from tests.conftest import create_test_client, create_test_user, make_assignment_with_stage

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state="completed",
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()

    report = ReportRead(
        verdict="reject",
        verdict_reason="failed must-have: x",
        overall_score=42,
        overall_coverage=0.8,
        overall_confidence="high",
        dimension_scores={},
        knockout_results=[],
        signal_scorecards=[],
        question_scorecards=[],
        summary=SummaryOut(headline="h"),
    )

    await persist_report(
        db,
        session_id=sess.id,
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        report=report,
    )

    row = (
        await db.execute(
            select(SessionReport).where(SessionReport.session_id == sess.id)
        )
    ).scalar_one()

    assert row.status == "ready"
    assert row.verdict == "reject"
    assert row.version == 1


@pytest.mark.asyncio
async def test_persist_report_idempotent_and_force_bumps_version(db):
    """Two no-force calls leave version=1; force=True bumps to version=2."""
    from sqlalchemy import select

    from app.modules.reporting.models import SessionReport
    from app.modules.reporting.schemas import ReportRead, SummaryOut
    from app.modules.reporting.service import persist_report
    from app.modules.session.models import Session as SessionRow
    from tests.conftest import create_test_client, create_test_user, make_assignment_with_stage

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        state="completed",
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()

    report_v1 = ReportRead(
        verdict="advance",
        verdict_reason="strong across all signals",
        overall_score=88,
        overall_coverage=0.9,
        overall_confidence="high",
        dimension_scores={},
        knockout_results=[],
        signal_scorecards=[],
        question_scorecards=[],
        summary=SummaryOut(headline="strong candidate"),
    )

    # First call — creates the row
    await persist_report(
        db,
        session_id=sess.id,
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        report=report_v1,
    )

    # Second call without force — must be a no-op (still version=1)
    await persist_report(
        db,
        session_id=sess.id,
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        report=report_v1,
        force=False,
    )

    row_after_two = (
        await db.execute(
            select(SessionReport).where(SessionReport.session_id == sess.id)
        )
    ).scalar_one()

    assert row_after_two.version == 1
    assert row_after_two.verdict == "advance"

    # Third call with force=True — must bump version and update verdict
    report_v2 = ReportRead(
        verdict="borderline",
        verdict_reason="re-scored after rubric correction",
        overall_score=61,
        overall_coverage=0.75,
        overall_confidence="medium",
        dimension_scores={},
        knockout_results=[],
        signal_scorecards=[],
        question_scorecards=[],
        summary=SummaryOut(headline="borderline — needs human review"),
    )

    await persist_report(
        db,
        session_id=sess.id,
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        report=report_v2,
        force=True,
    )

    row_after_force = (
        await db.execute(
            select(SessionReport).where(SessionReport.session_id == sess.id)
        )
    ).scalar_one()

    assert row_after_force.version == 2
    assert row_after_force.verdict == "borderline"
    assert row_after_force.overall_score == 61
