import pytest

from app.modules.interview_engine_v2 import DirectiveAct
from app.modules.interview_engine_v2.brain import ControlPlane
from app.modules.interview_engine_v2.brain import service as brain_service
from app.modules.interview_engine_v2.brain.decision import (
    BrainDecision,
    BrainMove,
    CandidateIntent,
)
from app.modules.interview_engine_v2.coverage import CoverageTracker
from tests.interview_engine_v2.prompt_evals.test_brain_evals import _config, _q  # type: ignore

pytestmark = pytest.mark.asyncio


def _plane(questions, mandatory):
    cfg = _config(questions)
    cov = CoverageTracker(signals=list(cfg.signals), mandatory_signals=mandatory, soft_probe_cap=2)
    return ControlPlane(config=cfg, coverage=cov)


async def test_setup_attached_when_pick_matches_resolved_target(monkeypatch):
    plane = _plane([_q("q1", "a", "Question one, please answer?", pos=0),
                    _q("q2", "b", "Q2 scenario question?", pos=1)],
                   mandatory=[])  # no mandatory -> brain's pick is honored
    plane.opener()  # active=q1, asked={q1}

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.advance, grade="concrete", bank_question_id="q2",
                             spoken_setup="Say you have a typical ticket queue.")
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    directive, _ = await plane.decide(turn_ref="t-1", active_question_id="q1",
                                      transcript_window=[], candidate_utterance="done")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert directive.spoken_setup == "Say you have a typical ticket queue."


async def test_setup_dropped_on_mandatory_first_override(monkeypatch):
    # q2 is the brain's pick, but q1 (mandatory) is still unasked -> resolver forces q1
    # -> drop setup (it would describe the wrong question)
    plane = _plane([_q("q1", "a", "Mandatory Q1 question here?", pos=0),
                    _q("q2", "b", "Q2 scenario question?", pos=1)],
                   mandatory=["a"])

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.advance, grade="concrete", bank_question_id="q2",
                             spoken_setup="Say you have a typical ticket queue.")
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    directive, _ = await plane.decide(turn_ref="t-1", active_question_id=None,
                                      transcript_window=[], candidate_utterance="hi")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert "Mandatory Q1 question here" in (directive.say or "")
    assert directive.spoken_setup is None
