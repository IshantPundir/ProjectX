"""Tests for envelope-driven segmentation.

The v2 engine never populates ``question_id`` on transcript turns; segmentation
must therefore come exclusively from the audit envelope.  These tests cover:

* Real-fixture integration tests (e4072361 session + job_bank_slice).
* Synthetic-envelope unit tests for the pointer logic.
"""
from __future__ import annotations

import json
import pathlib

from app.modules.reporting.scoring.transcript import segment

FIX = pathlib.Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_real_fixtures() -> tuple[dict, list[dict]]:
    """Load the e4072361 envelope and the bank's questions list."""
    envelope = json.loads((FIX / "e4072361_envelope.json").read_text())
    bank = json.loads((FIX / "job_bank_slice.json").read_text())
    return envelope, bank["questions"]


def _make_synthetic_questions(
    ids: list[str],
    mandatory_flags: list[bool] | None = None,
) -> list[dict]:
    """Build minimal question dicts with sequential positions."""
    mflags = mandatory_flags or [True] * len(ids)
    return [
        {
            "id": qid,
            "text": f"Question {qid}",
            "is_mandatory": mflags[i],
            "position": i,
            "signal_values": [],
            "rubric": {},
            "positive_evidence": [],
            "red_flags": [],
        }
        for i, qid in enumerate(ids)
    ]


def _ev_dir(t_ms: int, act: str, turn_ref: str) -> dict:
    """Build a minimal directive.delivered event."""
    return {
        "kind": "directive.delivered",
        "t_ms": t_ms,
        "payload": {"act": act, "turn_ref": turn_ref},
    }


def _ev_td(t_ms: int, turn_ref: str, quote: str) -> dict:
    """Build a minimal turn.decision event."""
    return {
        "kind": "turn.decision",
        "t_ms": t_ms,
        "payload": {"turn_ref": turn_ref, "candidate_quote": quote},
    }


def _ev_triage(t_ms: int, turn_ref: str, kind: str) -> dict:
    """Build a minimal engine.v2.triage.decision event."""
    return {
        "kind": "engine.v2.triage.decision",
        "t_ms": t_ms,
        "payload": {"turn_ref": turn_ref, "kind": kind},
    }


# ---------------------------------------------------------------------------
# Real-fixture integration tests
# ---------------------------------------------------------------------------


def test_real_session_produces_multiple_units() -> None:
    """Envelope-driven segmentation must produce ≥ 7 units for e4072361.

    The session walked through all 7 mandatory questions; the 1 non-mandatory
    behavioral question (position=2, id starts with 07b4442d) was never reached.
    """
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)
    qids = [u.question_id[:8] for u in units]
    assert len(units) >= 7, (
        f"Expected ≥7 units from real session; got {len(units)}: {qids}"
    )


def test_real_session_programming_question_has_nonempty_answer() -> None:
    """The Java/JSON/programming question (4f648441...) must have a real answer.

    The candidate said 'Already given you the answer' (among other things);
    that text must be present in the concatenated candidate_answer.
    """
    programming_q_id_prefix = "4f648441"  # Java/Python/Ruby question
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)

    prog = next(
        (u for u in units if u.question_id.startswith(programming_q_id_prefix)),
        None,
    )
    assert prog is not None, (
        f"Programming question {programming_q_id_prefix}... not found in units. "
        f"Got: {[u.question_id[:8] for u in units]}"
    )
    assert "already given you" in prog.candidate_answer.lower(), (
        f"Expected 'already given you' in candidate_answer; "
        f"got: {prog.candidate_answer[:200]!r}"
    )
    assert prog.probes_fired >= 0  # sanity — no negative counts
    assert prog.word_count > 0, "word_count must be > 0 for a question with answers"


def test_real_session_units_in_ask_order() -> None:
    """Units must be emitted in mandatory-first, then position order.

    The first unit must be the years-of-experience question; the programming
    question must appear after it (it has position=6 vs position=0).
    """
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)

    assert len(units) >= 2, "Need at least 2 units to check ordering"
    # First mandatory question: position=0 (years of experience)
    assert "years" in units[0].question_text.lower(), (
        f"First unit should be the years-of-experience question; "
        f"got: {units[0].question_text[:60]!r}"
    )
    # Programming question (position=6) must come after index 0
    prog_idx = next(
        (i for i, u in enumerate(units) if u.question_id.startswith("4f648441")),
        None,
    )
    assert prog_idx is not None, "Programming question not found in units"
    assert prog_idx > 0, (
        f"Programming question should come after index 0; got prog_idx={prog_idx}"
    )


def test_real_session_nonmandatory_question_produces_no_unit() -> None:
    """The non-mandatory behavioral question (07b4442d...) must produce NO unit.

    It has position=2 but is_mandatory=False.  The engine sorts mandatory
    questions first (positions 0,1,3,4,5,6,7) and asks all of them before
    returning to non-mandatory ones.  In e4072361 the session ended before
    that question was reached.
    """
    non_mandatory_id_prefix = "07b4442d"
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)

    non_mandatory_units = [
        u for u in units if u.question_id.startswith(non_mandatory_id_prefix)
    ]
    assert non_mandatory_units == [], (
        f"Non-mandatory question {non_mandatory_id_prefix}... should produce no unit; "
        f"got: {non_mandatory_units}"
    )


def test_real_session_c99c92ca_has_probes() -> None:
    """The Workato/RAG question (c99c92ca...) received multiple PROBE directives.

    The fixture has 2 PROBEs for this question → probes_fired must be > 0.
    """
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)

    heavy = next(
        (u for u in units if u.question_id == "c99c92ca-a2fe-4814-8932-edaba1e61a5a"),
        None,
    )
    assert heavy is not None, "c99c92ca question not found in segmented units"
    assert heavy.probes_fired > 0, (
        f"Expected probes_fired > 0 for the Workato/RAG question; "
        f"got {heavy.probes_fired}"
    )


# ---------------------------------------------------------------------------
# Synthetic-envelope pointer-logic tests
# ---------------------------------------------------------------------------


def test_synthetic_ask_then_ack_advance_maps_answers_correctly() -> None:
    """ASK → answer (turn.decision) → ACK_ADVANCE → Q1.

    Ordering invariant: ASK delivers Q0; candidate answers; ACK_ADVANCE fires
    AFTER the answer and advances to Q1.  Answers must be attributed to the
    question that was on the floor when the candidate spoke.
    """
    questions = _make_synthetic_questions(["q0", "q1", "q2"])

    envelope = {
        "events": [
            # INTRO (ignored)
            _ev_dir(0, "INTRO", "t-0"),
            # ASK Q0
            _ev_dir(100, "ASK", "t-0"),
            # Candidate answers Q0
            _ev_td(200, "t-1", "Answer for Q0"),
            # ACK_ADVANCE → advance to Q1
            _ev_dir(210, "ACK_ADVANCE", "t-1"),
            # Candidate answers Q1
            _ev_td(400, "t-2", "Answer for Q1"),
            # PROBE stays on Q1
            _ev_dir(410, "PROBE", "t-2"),
            # Candidate answers Q1 again (probe follow-up)
            _ev_td(600, "t-3", "More for Q1"),
            # ACK_ADVANCE → advance to Q2
            _ev_dir(610, "ACK_ADVANCE", "t-3"),
            # Session ends before Q2 is answered
            _ev_dir(700, "CLOSE", "t-4"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 2, (
        f"Expected 2 units (Q0 + Q1); got {len(units)}: "
        f"{[u.question_id for u in units]}"
    )

    u0, u1 = units
    assert u0.question_id == "q0"
    assert "Answer for Q0" in u0.candidate_answer
    assert u0.probes_fired == 0
    assert u0.clarifies == 0

    assert u1.question_id == "q1"
    assert "Answer for Q1" in u1.candidate_answer
    assert "More for Q1" in u1.candidate_answer
    assert u1.probes_fired == 1
    assert u1.clarifies == 0

    # Q2 had no answer → no unit.
    q2_units = [u for u in units if u.question_id == "q2"]
    assert q2_units == []


def test_synthetic_probe_stays_on_current_question() -> None:
    """PROBE directive must NOT advance the question index."""
    questions = _make_synthetic_questions(["q0", "q1"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "First answer"),
            _ev_dir(110, "PROBE", "t-1"),
            _ev_td(200, "t-2", "Second answer after probe"),
            # Both answers should belong to q0 — never advanced.
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 1
    u = units[0]
    assert u.question_id == "q0"
    assert u.probes_fired == 1
    assert "First answer" in u.candidate_answer
    assert "Second answer after probe" in u.candidate_answer


def test_synthetic_clarify_and_repeat_increment_clarifies() -> None:
    """CLARIFY and REPEAT directives must increment clarifies, not probes_fired."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "Unclear answer"),
            _ev_dir(110, "CLARIFY", "t-1"),
            _ev_td(200, "t-2", "Still unclear"),
            _ev_dir(210, "REPEAT", "t-2"),
            _ev_td(300, "t-3", "Final answer"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 1
    u = units[0]
    assert u.probes_fired == 0
    assert u.clarifies == 2  # one CLARIFY + one REPEAT
    assert "Final answer" in u.candidate_answer


def test_synthetic_no_experience_turn_marks_not_engaged() -> None:
    """A question where every answer is no_experience → candidate_engaged=False."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            # Triage decision: no_experience for t-1
            _ev_triage(90, "t-1", "no_experience"),
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "I have no experience"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 1
    assert units[0].candidate_engaged is False


def test_synthetic_mixed_engagement_any_engaged_wins() -> None:
    """If at least one turn is engaging, candidate_engaged=True."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_triage(90, "t-1", "no_experience"),
            _ev_triage(190, "t-2", "answering"),
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "I don't know"),
            _ev_td(200, "t-2", "Actually I do"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 1
    assert units[0].candidate_engaged is True


def test_synthetic_empty_questions_returns_empty() -> None:
    """Empty question list → empty result."""
    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "Answer"),
        ]
    }
    units = segment(envelope=envelope, questions=[])
    assert units == []


def test_synthetic_empty_envelope_returns_empty() -> None:
    """Empty envelope → no events → no units."""
    questions = _make_synthetic_questions(["q0"])
    units = segment(envelope={"events": []}, questions=questions)
    assert units == []


def test_synthetic_answer_start_ms_is_first_turn_decision_t_ms() -> None:
    """answer_start_ms is taken from t_ms of the first turn.decision event."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(1500, "t-1", "Answer"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    assert len(units) == 1
    assert units[0].answer_start_ms == 1500


def test_synthetic_word_count_sums_all_answer_parts() -> None:
    """word_count counts words across all turn.decision quotes for the question."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "one two three"),
            _ev_dir(110, "PROBE", "t-1"),
            _ev_td(200, "t-2", "four five"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    assert len(units) == 1
    assert units[0].word_count == 5


def test_synthetic_mandatory_questions_ordered_before_non_mandatory() -> None:
    """Mandatory questions must appear before non-mandatory ones regardless of position."""
    # q-nm has position=0 but is_mandatory=False; q-m has position=1 but is_mandatory=True.
    # The engine asks q-m first (mandatory-first).
    questions = [
        {
            "id": "q-nm",
            "text": "Non-mandatory Q",
            "is_mandatory": False,
            "position": 0,
            "signal_values": [],
            "rubric": {},
            "positive_evidence": [],
            "red_flags": [],
        },
        {
            "id": "q-m",
            "text": "Mandatory Q",
            "is_mandatory": True,
            "position": 1,
            "signal_values": [],
            "rubric": {},
            "positive_evidence": [],
            "red_flags": [],
        },
    ]

    # Engine asks q-m first (mandatory), then q-nm.
    # ASK → q_idx=0 → q-m; ACK_ADVANCE → q_idx=1 → q-nm.
    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "Mandatory answer"),
            _ev_dir(110, "ACK_ADVANCE", "t-1"),
            _ev_td(200, "t-2", "Non-mandatory answer"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 2
    assert units[0].question_id == "q-m", (
        f"Expected q-m first; got {units[0].question_id}"
    )
    assert units[1].question_id == "q-nm", (
        f"Expected q-nm second; got {units[1].question_id}"
    )
    assert "Mandatory answer" in units[0].candidate_answer
    assert "Non-mandatory answer" in units[1].candidate_answer


# ---------------------------------------------------------------------------
# Backward-compatibility: transcript kwarg is still accepted (but ignored for
# question mapping — used externally for the communication dimension only).
# ---------------------------------------------------------------------------


def test_real_session_question_kind_propagated_from_bank() -> None:
    """ScoredUnit.question_kind must match the bank's question_kind field.

    The two experience_check questions in the fixture (0ab73bfa and 2fe68aad)
    must surface as question_kind='experience_check' in the emitted units.
    The technical_scenario questions must surface as 'technical_scenario'.
    """
    envelope, questions = _load_real_fixtures()
    units = segment(envelope=envelope, questions=questions)

    # Build a lookup from question_id prefix → emitted unit
    unit_by_prefix = {u.question_id[:8]: u for u in units}

    # The years-of-experience question (position=0, is_mandatory=True)
    years_unit = unit_by_prefix.get("0ab73bfa")
    assert years_unit is not None, "Years-of-experience unit not found"
    assert years_unit.question_kind == "experience_check", (
        f"Expected 'experience_check'; got {years_unit.question_kind!r}"
    )

    # The Workato-years question (position=1, is_mandatory=True)
    workato_unit = unit_by_prefix.get("2fe68aad")
    assert workato_unit is not None, "Workato-years unit not found"
    assert workato_unit.question_kind == "experience_check", (
        f"Expected 'experience_check'; got {workato_unit.question_kind!r}"
    )

    # A technical_scenario question (programming, 4f648441)
    prog_unit = unit_by_prefix.get("4f648441")
    assert prog_unit is not None, "Programming question unit not found"
    assert prog_unit.question_kind == "technical_scenario", (
        f"Expected 'technical_scenario'; got {prog_unit.question_kind!r}"
    )


def test_synthetic_question_kind_none_when_missing_from_bank() -> None:
    """When a bank question has no question_kind key, ScoredUnit.question_kind
    must be None (not raise, not default to a non-None string)."""
    # _make_synthetic_questions does NOT include question_kind — legacy shape.
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "My answer"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    assert len(units) == 1
    assert units[0].question_kind is None


def test_transcript_kwarg_accepted_but_not_used_for_mapping() -> None:
    """Passing transcript= should not break the call; units still come from envelope."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "Envelope answer"),
        ]
    }

    # transcript has a different (old) shape — entirely different question_id;
    # should be ignored for question mapping.
    old_transcript = [
        {
            "role": "agent",
            "text": "Some agent turn",
            "timestamp_ms": 0,
            "question_id": "old-q",
        },
        {
            "role": "candidate",
            "text": "Some old answer",
            "timestamp_ms": 100,
            "question_id": "old-q",
        },
    ]

    units = segment(envelope=envelope, questions=questions, transcript=old_transcript)

    # Must produce 1 unit from the envelope, not from the old transcript's question_id.
    assert len(units) == 1
    assert units[0].question_id == "q0"
    assert "Envelope answer" in units[0].candidate_answer
