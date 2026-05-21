"""Generation phase ↔ question_kind partition (decision D3)."""

from app.modules.question_bank.actors import PHASE_QUESTION_KINDS


def test_partition_is_total_and_disjoint():
    behavioral = PHASE_QUESTION_KINDS["behavioral"]
    technical = PHASE_QUESTION_KINDS["technical"]
    assert behavioral == {"experience_check", "behavioral", "compliance_binary"}
    assert technical == {"technical_scenario"}
    assert behavioral.isdisjoint(technical)


def test_event_constant_present():
    from app import pubsub
    assert pubsub.Events.BANK_QUESTION_ADDED == "bank.question_added"
