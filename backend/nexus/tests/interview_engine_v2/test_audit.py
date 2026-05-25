"""TurnDecisionRecord — the brain-side audit pairing for every Directive (doc 11/13)."""

import pytest
from pydantic import ValidationError

from app.modules.interview_engine_v2.audit import TurnDecisionRecord


def test_minimal_record():
    r = TurnDecisionRecord(
        turn_ref="t-42",
        candidate_quote="I built the billing sync end to end.",
        move="probe",
        reasoning="Concrete on ownership; one probe to test depth.",
        directive_id="d-7f3a",
    )
    assert r.attributed_signals == []
    assert r.coverage_delta == {}
    assert r.policy_checks == []
    assert r.grade is None


def test_full_record():
    r = TurnDecisionRecord(
        turn_ref="t-42",
        candidate_quote="...",
        attributed_signals=["workato_recipes", "api_integration"],
        grade="concrete",
        coverage_delta={"workato_recipes": "sufficient"},
        move="advance",
        reasoning="...",
        policy_checks=["no_leak_ok", "knockout_not_triggered"],
        directive_id="d-9",
    )
    assert r.grade == "concrete"
    assert r.coverage_delta["workato_recipes"] == "sufficient"


def test_grade_is_constrained():
    with pytest.raises(ValidationError):
        TurnDecisionRecord(turn_ref="t-1", candidate_quote="x", move="probe",
                           reasoning="y", directive_id="d", grade="amazing")


def test_active_question_id_defaults_to_none():
    """active_question_id must default to None for backward compatibility."""
    r = TurnDecisionRecord(
        turn_ref="t-1", candidate_quote="x", move="probe",
        reasoning="y", directive_id="d",
    )
    assert r.active_question_id is None


def test_active_question_id_round_trips():
    """active_question_id round-trips through model_dump (the collector uses model_dump)."""
    r = TurnDecisionRecord(
        turn_ref="t-42",
        candidate_quote="I built the billing sync end to end.",
        move="probe",
        reasoning="Concrete on ownership; one probe to test depth.",
        directive_id="d-7f3a",
        active_question_id="q-abc123",
    )
    assert r.active_question_id == "q-abc123"
    dumped = r.model_dump(mode="json")
    assert dumped["active_question_id"] == "q-abc123"


def test_active_question_id_none_serializes_as_none():
    """None active_question_id serializes correctly (not omitted or as string 'None')."""
    r = TurnDecisionRecord(
        turn_ref="t-1", candidate_quote="x", move="probe",
        reasoning="y", directive_id="d",
    )
    dumped = r.model_dump(mode="json")
    assert "active_question_id" in dumped
    assert dumped["active_question_id"] is None
