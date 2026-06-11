from app.modules.interview_engine.contracts import (
    BrainTurnOutput, SignalObservation, BrainMove, MouthTurnInput, Directive, DirectiveAct,
)
from app.modules.interview_runtime.evidence import EvidenceStance, EvidenceTexture, CoverageState


def test_brain_output_probe_then_advance_with_preference():
    out = BrainTurnOutput(
        reasoning="thin answer, push once",
        observations=[SignalObservation(signal="Workato", stance=EvidenceStance.supports,
                                        texture=EvidenceTexture.thin, coverage_after=CoverageState.partial)],
        move=BrainMove.probe,
        probe_dimension="workato_depth",
    )
    assert out.move == BrainMove.probe
    assert out.probe_dimension == "workato_depth"
    # BrainTurnOutput must NOT carry a target_question_id (the deterministic resolver owns next-question).
    assert not hasattr(out, "target_question_id")

    out2 = BrainTurnOutput(reasoning="done", move=BrainMove.ask, preferred_next_signal="REST APIs")
    assert out2.preferred_next_signal == "REST APIs"
    assert out2.probe_dimension is None


def test_mouth_turn_input_continues_from_bridge():
    mti = MouthTurnInput(
        directive=Directive(act=DirectiveAct.probe, say="and how did you handle retries?"),
        just_said="Mm, retries, okay...",
        recent_openers=["so", "okay"],
    )
    assert mti.just_said == "Mm, retries, okay..."
    assert mti.directive.act == DirectiveAct.probe
    # The shared enums are the SAME objects from evidence.py (single source).
    assert SignalObservation.model_fields["stance"].annotation is EvidenceStance
