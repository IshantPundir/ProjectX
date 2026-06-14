from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric, BankQuestionIndex, BrainTurnOutput, BrainMove, FollowUpDimension,
)


def _dim(slug="d1"):
    return FollowUpDimension(dimension=slug, intent="i", seed_probe="p", listen_for=["x"])


def test_bank_index_holds_dimensions():
    idx = BankQuestionIndex(
        question_id="q1", primary_signal="s", signals=["s"], kind="technical_scenario",
        difficulty="medium", text="t", follow_ups=[_dim()],
    )
    assert idx.follow_ups[0].dimension == "d1"


def test_active_rubric_has_fired_dimensions_not_probes_used():
    r = ActiveQuestionRubric(
        question_id="q1", text="t", excellent="e", meets_bar="m", below_bar="b",
        positive_evidence=["a"], red_flags=["r"], evaluation_hint="h",
        follow_ups=[_dim()], fired_dimensions=["d0"],
    )
    assert r.fired_dimensions == ["d0"]
    assert not hasattr(r, "probes_used")


def test_brain_output_uses_probe_dimension():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="validate_impact")
    assert out.probe_dimension == "validate_impact"
    assert not hasattr(out, "probe_index")
