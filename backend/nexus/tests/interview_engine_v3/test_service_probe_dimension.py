import pytest

from app.modules.interview_engine.brain.input_builder import CoverageProjection, active_question_rubric, build_turn_input
from app.modules.interview_engine.brain.resolver import ResolverQuestion
from app.modules.interview_engine.brain.service import ControlPlane
from app.modules.interview_engine.contracts import (
    BrainMove, BrainSessionContext, BrainTurnOutput, DirectiveAct, SignalSpec,
)
from app.modules.interview_runtime.evidence import SignalPriority, SignalType
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q():
    return QuestionConfig(
        id="q1", position=0, text="Assess a messy tenant?", signal_values=["s"],
        follow_ups=[
            {"dimension": "validate_impact", "intent": "verify impact", "seed_probe": "seed A", "listen_for": []},
            {"dimension": "stage_safely", "intent": "stage safely", "seed_probe": "seed B", "listen_for": []},
        ],
        positive_evidence=["a", "b", "c"], red_flags=["r1", "r2"],
        rubric=QuestionRubric(excellent="e" * 20, meets_bar="m" * 20, below_bar="b" * 20),
        evaluation_hint="h" * 12, question_kind="technical_scenario", primary_signal="s", difficulty="medium",
    )


def _cp(output):
    ctx = BrainSessionContext(job_title="t", seniority_level="mid", role_summary="r", hiring_bar="hb",
                              signals=[SignalSpec(signal="s", signal_type=SignalType.competency, weight=2,
                                                  priority=SignalPriority.required, knockout=False)],
                              bank_index=[])

    async def fake_llm(_messages):
        return output

    return ControlPlane(
        session_context=ctx, system_prompt="sys", projection=CoverageProjection(),
        resolver_questions=[ResolverQuestion(question_id="q1", primary_signal="s", position=0)],
        all_specs=ctx.signals, llm_call=fake_llm,
    )


def _turn(fired):
    r = active_question_rubric(_q(), fired_dimensions=fired)
    return build_turn_input(turn_ref="t1", active_question=r, on_the_floor="Assess a messy tenant?",
                            candidate_utterance="we made some changes", thread_turn_count=1,
                            projection=CoverageProjection(), all_specs=[], transcript_window=[])


@pytest.mark.asyncio
async def test_probe_records_served_dimension():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="validate_impact",
                          composed_say="So concretely, what did you check first?")
    cp = _cp(out)
    decision = await cp.decide(_turn(fired=[]), asked_ids={"q1"})
    assert decision.directive.act == DirectiveAct.probe
    assert decision.probe_dimension == "validate_impact"


@pytest.mark.asyncio
async def test_probe_at_cap_advances_to_ask():
    out = BrainTurnOutput(reasoning="r", move=BrainMove.probe, probe_dimension="stage_safely",
                          composed_say="and how would you roll it out safely?")
    cp = _cp(out)
    decision = await cp.decide(_turn(fired=["validate_impact", "stage_safely"]),
                               asked_ids={"q1"})
    assert decision.directive.act in (DirectiveAct.ask, DirectiveAct.close)
    assert decision.probe_dimension is None
