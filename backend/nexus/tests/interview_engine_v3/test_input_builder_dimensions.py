from app.modules.interview_engine.brain.input_builder import (
    active_question_rubric, render_suffix, build_turn_input, CoverageProjection,
)
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q():
    return QuestionConfig(
        id="q1", position=0, text="How would you assess a messy tenant?",
        signal_values=["intune_admin"], estimated_minutes=3.0, is_mandatory=True,
        follow_ups=[{
            "dimension": "validate_impact", "intent": "verify impact",
            "seed_probe": "How would you validate impact?",
            "listen_for": ["pilot group", "rollback"],
        }],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="e" * 20, meets_bar="m" * 20, below_bar="b" * 20),
        evaluation_hint="h" * 12, question_kind="technical_scenario",
        primary_signal="intune_admin", difficulty="medium",
    )


def test_active_rubric_carries_dimensions_and_fired():
    r = active_question_rubric(_q(), fired_dimensions=["validate_impact"])
    assert r.follow_ups[0].dimension == "validate_impact"
    assert r.fired_dimensions == ["validate_impact"]


def test_render_suffix_shows_dimension_intent_listen_for():
    r = active_question_rubric(_q(), fired_dimensions=[])
    ti = build_turn_input(
        turn_ref="t1", active_question=r, on_the_floor="...", candidate_utterance="hi",
        thread_turn_count=1, projection=CoverageProjection(), all_specs=[],
        transcript_window=[],
    )
    content = render_suffix(ti)[0]["content"]
    assert "validate_impact" in content     # dimension slug
    assert "verify impact" in content        # intent
    assert "pilot group" in content          # listen_for
    assert "fired_dimensions" in content
