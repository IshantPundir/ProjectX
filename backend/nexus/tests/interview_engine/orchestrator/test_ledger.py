"""Tests for SignalLedger — invariants, evidence appending, coverage
transitions, knockout disclaim path, sequence-number monotonicity."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.interview_engine.orchestrator import (
    EvidenceQuote,
    LedgerInvariantError,
    SignalLedger,
)
from app.modules.interview_runtime import SignalMetadata


def _meta(
    value: str,
    *,
    weight: int = 2,
    knockout: bool = False,
    priority: str = "preferred",
    type_: str = "competency",
    stage: str = "screen",
    evaluation_method: str = "verbal_response",
) -> SignalMetadata:
    return SignalMetadata(
        value=value,
        type=type_,
        priority=priority,
        weight=weight,
        knockout=knockout,
        stage=stage,
        evaluation_method=evaluation_method,
    )


def _quote(
    *,
    text: str = "I built it with Django and PostgreSQL.",
    turn_id: str = "turn_001",
    question_id: str = "q1",
    strength: str = "strong",
) -> EvidenceQuote:
    return EvidenceQuote(
        quote=text,
        turn_id=turn_id,
        source_question_id=question_id,
        strength=strength,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_from_metadata_initializes_all_signals_at_none():
    ledger = SignalLedger.from_metadata([
        _meta("Python", knockout=False, priority="preferred", weight=2),
        _meta("UK shift", knockout=True, priority="required", weight=3),
    ])
    assert set(ledger.signals.keys()) == {"Python", "UK shift"}
    assert all(s.coverage == "none" for s in ledger.signals.values())
    assert all(s.evidence_quotes == [] for s in ledger.signals.values())
    assert ledger.sequence_number == 0


def test_from_metadata_preserves_order():
    ledger = SignalLedger.from_metadata([
        _meta("A"), _meta("B"), _meta("C"),
    ])
    assert list(ledger.signals.keys()) == ["A", "B", "C"]


def test_from_metadata_carries_weight_knockout_priority():
    ledger = SignalLedger.from_metadata([
        _meta("UK shift", knockout=True, priority="required", weight=3),
    ])
    s = ledger.signals["UK shift"]
    assert s.weight == 3
    assert s.is_knockout is True
    assert s.priority == "required"


# ---------------------------------------------------------------------------
# append_evidence — happy paths
# ---------------------------------------------------------------------------


def test_append_evidence_advances_none_to_partial():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python",
        evidence=_quote(strength="weak"),
        new_coverage="partial",
        new_confidence=0.4,
    )
    s = ledger.signals["Python"]
    assert s.coverage == "partial"
    assert s.confidence == 0.4
    assert len(s.evidence_quotes) == 1
    assert s.last_updated_turn == "turn_001"


def test_append_evidence_advances_partial_to_sufficient():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t2"), new_coverage="sufficient",
    )
    assert ledger.signals["Python"].coverage == "sufficient"
    assert len(ledger.signals["Python"].evidence_quotes) == 2


def test_append_evidence_without_coverage_change_still_appends():
    """Sufficiency Checker can return 'partial' twice in a row — the
    second call adds evidence and bumps confidence but coverage stays."""
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python",
        evidence=_quote(turn_id="t1"),
        new_coverage="partial",
        new_confidence=0.4,
    )
    ledger.append_evidence(
        "Python",
        evidence=_quote(turn_id="t2"),
        new_confidence=0.6,
    )
    s = ledger.signals["Python"]
    assert s.coverage == "partial"
    assert s.confidence == 0.6
    assert len(s.evidence_quotes) == 2


def test_append_evidence_appends_note_when_provided():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python",
        evidence=_quote(),
        new_coverage="partial",
        note="Mentioned ORM but no concurrency reasoning.",
    )
    assert ledger.signals["Python"].notes == [
        "Mentioned ORM but no concurrency reasoning."
    ]


# ---------------------------------------------------------------------------
# append_evidence — invariant violations
# ---------------------------------------------------------------------------


def test_append_evidence_rejects_unknown_signal():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    with pytest.raises(LedgerInvariantError, match="Unknown signal_value"):
        ledger.append_evidence("Java", evidence=_quote())


def test_append_evidence_rejects_backward_coverage_sufficient_to_partial():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="sufficient",
    )
    with pytest.raises(LedgerInvariantError, match="Illegal coverage transition"):
        ledger.append_evidence(
            "Python", evidence=_quote(turn_id="t2"), new_coverage="partial",
        )


def test_append_evidence_rejects_backward_coverage_partial_to_none():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    with pytest.raises(LedgerInvariantError, match="Illegal coverage transition"):
        ledger.append_evidence(
            "Python", evidence=_quote(turn_id="t2"), new_coverage="none",
        )


def test_append_evidence_rejects_failed_via_normal_path():
    """`failed` must go through `mark_failed`, not `append_evidence`."""
    ledger = SignalLedger.from_metadata([_meta("Python")])
    with pytest.raises(LedgerInvariantError, match="Illegal coverage transition"):
        ledger.append_evidence(
            "Python", evidence=_quote(), new_coverage="failed",
        )


# ---------------------------------------------------------------------------
# mark_failed — disclaim path
# ---------------------------------------------------------------------------


def test_mark_failed_terminal_from_none():
    ledger = SignalLedger.from_metadata([
        _meta("UK shift", knockout=True, priority="required", weight=3),
    ])
    disclaim = _quote(text="I haven't worked the UK shift.", turn_id="t9")
    ledger.mark_failed("UK shift", evidence=disclaim)
    s = ledger.signals["UK shift"]
    assert s.coverage == "failed"
    assert s.confidence == 1.0
    assert s.evidence_quotes == [disclaim]


def test_mark_failed_terminal_from_partial():
    """Knockouts can occur after some evidence has been collected
    (candidate hedged then explicitly disclaimed) — `failed` is still
    reachable."""
    ledger = SignalLedger.from_metadata([_meta("UK shift")])
    ledger.append_evidence(
        "UK shift", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    disclaim = _quote(text="Actually no, I can't work that shift.", turn_id="t2")
    ledger.mark_failed("UK shift", evidence=disclaim)
    s = ledger.signals["UK shift"]
    assert s.coverage == "failed"
    assert len(s.evidence_quotes) == 2  # prior partial evidence kept (append-only)


def test_mark_failed_is_terminal_no_further_mutations():
    ledger = SignalLedger.from_metadata([_meta("UK shift")])
    ledger.mark_failed("UK shift", evidence=_quote(turn_id="t1"))
    with pytest.raises(LedgerInvariantError, match="already failed"):
        ledger.mark_failed("UK shift", evidence=_quote(turn_id="t2"))
    with pytest.raises(LedgerInvariantError, match="failed"):
        ledger.append_evidence(
            "UK shift", evidence=_quote(turn_id="t3"), new_coverage="partial",
        )
    with pytest.raises(LedgerInvariantError, match="failed"):
        ledger.add_note("UK shift", "trying", turn_id="t4")


# ---------------------------------------------------------------------------
# add_note — annotate without changing coverage
# ---------------------------------------------------------------------------


def test_add_note_appends_without_advancing_coverage():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.add_note("Python", "weak signal hint", turn_id="t1")
    s = ledger.signals["Python"]
    assert s.coverage == "none"
    assert s.notes == ["weak signal hint"]
    assert s.evidence_quotes == []
    assert s.last_updated_turn == "t1"


def test_add_note_rejects_unknown_signal():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    with pytest.raises(LedgerInvariantError, match="Unknown signal_value"):
        ledger.add_note("Java", "weak", turn_id="t1")


# ---------------------------------------------------------------------------
# Sequence number monotonicity
# ---------------------------------------------------------------------------


def test_sequence_number_increments_on_every_mutation():
    ledger = SignalLedger.from_metadata([_meta("Python"), _meta("UK shift")])
    assert ledger.sequence_number == 0

    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    assert ledger.sequence_number == 1

    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t2"), new_confidence=0.5,
    )
    assert ledger.sequence_number == 2

    ledger.add_note("Python", "n", turn_id="t3")
    assert ledger.sequence_number == 3

    ledger.mark_failed("UK shift", evidence=_quote(turn_id="t4"))
    assert ledger.sequence_number == 4


def test_sequence_number_does_not_increment_on_failed_mutation():
    """Invariant violations must not bump the counter (they didn't write)."""
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="sufficient",
    )
    seq_before = ledger.sequence_number
    with pytest.raises(LedgerInvariantError):
        ledger.append_evidence(
            "Python", evidence=_quote(turn_id="t2"), new_coverage="partial",
        )
    assert ledger.sequence_number == seq_before


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def test_signals_by_coverage_partitions_correctly():
    ledger = SignalLedger.from_metadata([
        _meta("A"), _meta("B"), _meta("C"), _meta("D"),
    ])
    ledger.append_evidence(
        "A", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    ledger.append_evidence(
        "B", evidence=_quote(turn_id="t2"), new_coverage="sufficient",
    )
    ledger.mark_failed("C", evidence=_quote(turn_id="t3"))
    # D stays at "none"

    assert {s.signal_value for s in ledger.signals_by_coverage("none")} == {"D"}
    assert {s.signal_value for s in ledger.signals_by_coverage("partial")} == {"A"}
    assert {s.signal_value for s in ledger.signals_by_coverage("sufficient")} == {"B"}
    assert {s.signal_value for s in ledger.signals_by_coverage("failed")} == {"C"}


def test_all_mandatory_sufficient_true_when_all_required_at_sufficient():
    ledger = SignalLedger.from_metadata([
        _meta("R1", priority="required"),
        _meta("R2", priority="required"),
        _meta("P1", priority="preferred"),
    ])
    assert ledger.all_mandatory_sufficient() is False
    ledger.append_evidence(
        "R1", evidence=_quote(turn_id="t1"), new_coverage="sufficient",
    )
    assert ledger.all_mandatory_sufficient() is False
    ledger.append_evidence(
        "R2", evidence=_quote(turn_id="t2"), new_coverage="sufficient",
    )
    # Preferred P1 is still 'none' — but mandatory rule is satisfied.
    assert ledger.all_mandatory_sufficient() is True


def test_all_mandatory_sufficient_false_when_required_failed():
    """Failed knockout signals do NOT satisfy the mandatory rule.

    (In production, a knockout-failed required signal triggers early
    exit before this query — but if it ever doesn't, the query must
    not report the session as evaluable.)
    """
    ledger = SignalLedger.from_metadata([
        _meta("R1", priority="required", knockout=True),
    ])
    ledger.mark_failed("R1", evidence=_quote())
    assert ledger.all_mandatory_sufficient() is False


def test_coverage_of_returns_none_for_unknown_signal():
    ledger = SignalLedger.from_metadata([_meta("Python")])
    assert ledger.coverage_of("Python") == "none"
    assert ledger.coverage_of("Java") is None


# ---------------------------------------------------------------------------
# Append-only evidence invariant
# ---------------------------------------------------------------------------


def test_evidence_append_only_via_normal_mutations():
    """Evidence list grows monotonically — there is no public API to
    remove a quote."""
    ledger = SignalLedger.from_metadata([_meta("Python")])
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t1"), new_coverage="partial",
    )
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t2"), new_confidence=0.5,
    )
    ledger.append_evidence(
        "Python", evidence=_quote(turn_id="t3"), new_coverage="sufficient",
    )
    s = ledger.signals["Python"]
    assert [e.turn_id for e in s.evidence_quotes] == ["t1", "t2", "t3"]
