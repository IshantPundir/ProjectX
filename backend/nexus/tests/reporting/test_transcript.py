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


# ---------------------------------------------------------------------------
# Logged-id mode tests (PART B)
# ---------------------------------------------------------------------------


def _ev_td_with_aqid(t_ms: int, turn_ref: str, quote: str, active_question_id: str) -> dict:
    """Build a turn.decision event with the active_question_id ground-truth field."""
    return {
        "kind": "turn.decision",
        "t_ms": t_ms,
        "payload": {
            "turn_ref": turn_ref,
            "candidate_quote": quote,
            "active_question_id": active_question_id,
        },
    }


def test_logged_id_mode_out_of_order_advancement() -> None:
    """Core correctness test: when turn.decisions carry active_question_id, the report must
    map each answer to the LOGGED question — NOT to the pointer-walk position.

    Setup: 5 questions at positions 0-4 (all mandatory).  The brain answers them
    in a non-sequential order:
      answer 1 → q at position 4  (active_question_id="q4")
      answer 2 → q at position 0  (active_question_id="q0")

    Pointer mode would wrongly assign answer 1 → position-0 question and
    answer 2 → position-1 question.  Logged-id mode must use the brain's
    ground truth.
    """
    questions = _make_synthetic_questions(["q0", "q1", "q2", "q3", "q4"])

    envelope = {
        "events": [
            # ASK fires first (puts something on the pointer-walk floor)
            _ev_dir(0, "ASK", "t-0"),
            # Answer 1 → brain says this was for q4 (position 4)
            _ev_td_with_aqid(100, "t-1", "Answer for q4", "q4"),
            # ACK_ADVANCE in the log (pointer would advance to q1)
            _ev_dir(110, "ACK_ADVANCE", "t-1"),
            # Answer 2 → brain says this was for q0 (position 0)
            _ev_td_with_aqid(200, "t-2", "Answer for q0", "q0"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    # Must have exactly 2 units — one per answered question.
    assert len(units) == 2, (
        f"Expected 2 units; got {len(units)}: {[u.question_id for u in units]}"
    )

    # Units must be in first-answered order: q4 then q0.
    assert units[0].question_id == "q4", (
        f"First unit should be q4 (first answered); got {units[0].question_id!r}"
    )
    assert units[1].question_id == "q0", (
        f"Second unit should be q0 (second answered); got {units[1].question_id!r}"
    )

    # Content must match the logged id, not the pointer position.
    assert "Answer for q4" in units[0].candidate_answer, (
        f"units[0] should contain 'Answer for q4'; got {units[0].candidate_answer!r}"
    )
    assert "Answer for q0" in units[1].candidate_answer, (
        f"units[1] should contain 'Answer for q0'; got {units[1].candidate_answer!r}"
    )

    # Unanswered questions must produce no units.
    answered_ids = {u.question_id for u in units}
    assert answered_ids == {"q0", "q4"}, (
        f"Only q0 and q4 should appear; got {answered_ids}"
    )


def test_logged_id_mode_probes_attributed_to_logged_question() -> None:
    """PROBE directives must be counted against the question named by the turn_ref's
    active_question_id, not against the pointer-walk position."""
    questions = _make_synthetic_questions(["q0", "q1"])

    # The brain answers q1 (position 1) before q0 (position 0) — non-sequential.
    # Pointer walk would assign the probe to q0 (position 0, first in order).
    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            # t-1 graded q1
            _ev_td_with_aqid(100, "t-1", "Thin answer for q1", "q1"),
            # PROBE fired for turn t-1 → should be attributed to q1
            {"kind": "directive.delivered", "t_ms": 110,
             "payload": {"act": "PROBE", "turn_ref": "t-1"}},
            # t-2 graded q0
            _ev_td_with_aqid(200, "t-2", "Answer for q0", "q0"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 2
    by_id = {u.question_id: u for u in units}
    assert "q1" in by_id and "q0" in by_id

    # The probe must be on q1, not q0.
    assert by_id["q1"].probes_fired == 1, (
        f"q1 should have 1 probe_fired; got {by_id['q1'].probes_fired}"
    )
    assert by_id["q0"].probes_fired == 0, (
        f"q0 should have 0 probes_fired; got {by_id['q0'].probes_fired}"
    )


def test_logged_id_mode_clarify_attributed_to_logged_question() -> None:
    """CLARIFY directives must be counted against the question named by active_question_id."""
    questions = _make_synthetic_questions(["q0", "q1"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td_with_aqid(100, "t-1", "Unclear answer for q1", "q1"),
            {"kind": "directive.delivered", "t_ms": 110,
             "payload": {"act": "CLARIFY", "turn_ref": "t-1"}},
            _ev_td_with_aqid(200, "t-2", "Answer for q0", "q0"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    by_id = {u.question_id: u for u in units}

    assert by_id["q1"].clarifies == 1
    assert by_id["q0"].clarifies == 0


def test_logged_id_mode_engagement_via_triage() -> None:
    """Triage kind must be associated with the LOGGED question, not the pointer position."""
    questions = _make_synthetic_questions(["q0", "q1"])

    # The brain is on q1 (non-sequential); triage says no_experience for t-1.
    # Only q1 should be marked not-engaged.
    envelope = {
        "events": [
            _ev_triage(90, "t-1", "no_experience"),
            _ev_dir(0, "ASK", "t-0"),
            _ev_td_with_aqid(100, "t-1", "I have no experience", "q1"),
            _ev_triage(190, "t-2", "answering"),
            _ev_td_with_aqid(200, "t-2", "Answer for q0", "q0"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    by_id = {u.question_id: u for u in units}

    assert by_id["q1"].candidate_engaged is False
    assert by_id["q0"].candidate_engaged is True


def test_logged_id_mode_uses_question_kind_from_bank() -> None:
    """question_kind must be sourced from the bank question, not hardcoded."""
    questions = [
        {
            "id": "qa",
            "text": "Question A",
            "is_mandatory": True,
            "position": 0,
            "question_kind": "experience_check",
            "signal_values": [],
        },
        {
            "id": "qb",
            "text": "Question B",
            "is_mandatory": True,
            "position": 1,
            "question_kind": "technical_scenario",
            "signal_values": [],
        },
    ]

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td_with_aqid(100, "t-1", "Experience answer", "qa"),
            _ev_td_with_aqid(200, "t-2", "Technical answer", "qb"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)
    by_id = {u.question_id: u for u in units}

    assert by_id["qa"].question_kind == "experience_check"
    assert by_id["qb"].question_kind == "technical_scenario"


def test_logged_id_mode_unknown_aqid_is_skipped() -> None:
    """If active_question_id names a question not in the bank, that turn is skipped gracefully."""
    questions = _make_synthetic_questions(["q0"])

    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td_with_aqid(100, "t-1", "Answer for unknown", "does-not-exist"),
            _ev_td_with_aqid(200, "t-2", "Answer for q0", "q0"),
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    assert len(units) == 1
    assert units[0].question_id == "q0"
    assert "Answer for q0" in units[0].candidate_answer


def test_logged_id_mode_mode_detection_requires_nonnull_aqid() -> None:
    """Pointer mode is used when ALL turn.decision events have null/missing active_question_id.

    This is the backward-compat guarantee: the e4072361 real fixture (no active_question_id
    in any event) must continue to use the pointer walk, not the logged-id path.
    """
    questions = _make_synthetic_questions(["q0", "q1"])

    # No active_question_id → pointer mode
    envelope = {
        "events": [
            _ev_dir(0, "ASK", "t-0"),
            _ev_td(100, "t-1", "Answer for q0"),  # no active_question_id
            _ev_dir(110, "ACK_ADVANCE", "t-1"),
            _ev_td(200, "t-2", "Answer for q1"),  # no active_question_id
        ]
    }

    units = segment(envelope=envelope, questions=questions)

    # Pointer mode: q0 first (position 0), then q1 (position 1).
    assert len(units) == 2
    assert units[0].question_id == "q0"
    assert units[1].question_id == "q1"


def test_real_session_pointer_fallback_unchanged() -> None:
    """The e4072361 real session (no active_question_id) must continue to use pointer mode.

    This test is the backward-compat guard: adding logged-id mode must NOT break
    sessions recorded before the field was introduced.
    """
    envelope, questions = _load_real_fixtures()

    # Confirm no active_question_id in any turn.decision event.
    has_aqid = any(
        (e.get("payload") or {}).get("active_question_id") is not None
        for e in (envelope.get("events") or [])
        if e.get("kind") == "turn.decision"
    )
    assert not has_aqid, (
        "e4072361 fixture has active_question_id — it should be testing pointer fallback mode"
    )

    units = segment(envelope=envelope, questions=questions)
    # The pointer-walk invariants are the same as the existing real-fixture tests.
    assert len(units) >= 7, f"Expected ≥7 pointer-mode units; got {len(units)}"
