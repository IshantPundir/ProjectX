import pytest

from app.modules.interview_engine_v2.brain import ControlPlane
from app.modules.interview_engine_v2.brain import service as brain_service
from app.modules.interview_engine_v2.brain.decision import BrainDecision, BrainMove, CandidateIntent
from app.modules.interview_engine_v2.brain.input_builder import build_brain_messages
from app.modules.interview_engine_v2.coverage import CoverageTracker

# Reuse the SessionConfig builders (`_config`, `_q`) defined in the brain eval module:
from tests.interview_engine_v2.prompt_evals.test_brain_evals import _config, _q  # type: ignore

pytestmark = pytest.mark.asyncio


def _plane():
    cfg = _config([
        _q("q1", "rest", "How would you design a connector to a rate-limited REST API?",
           follow_ups=["How would you page through large result sets?"], pos=0),
        _q("q2", "json", "How would you transform and validate a JSON payload?", pos=1),
    ])
    cov = CoverageTracker(signals=list(cfg.signals),
                          mandatory_signals=[q.primary_signal for q in cfg.stage.questions],
                          soft_probe_cap=2)
    return ControlPlane(config=cfg, coverage=cov)


async def test_opener_seeds_floor_to_first_question(monkeypatch):
    plane = _plane()
    plane.opener()
    assert plane._floor is not None
    assert plane._floor.kind == "main"
    assert "REST" in plane._floor.canonical_text


async def test_probe_updates_floor_to_the_followup_text(monkeypatch):
    plane = _plane()
    plane.opener()

    async def fake(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.probe, grade="thin", bank_follow_up_index=0)
    monkeypatch.setattr(brain_service, "_call_brain", fake)
    await plane.decide(turn_ref="t-1", active_question_id="q1", transcript_window=[],
                       candidate_utterance="we throttle calls")
    assert plane._floor.kind == "probe"
    assert "page through large result sets" in plane._floor.canonical_text


def test_on_the_floor_block_renders_the_probe_line():
    from app.modules.interview_engine_v2.brain.service import FloorRef
    msgs = build_brain_messages(
        stable_prefix="PREFIX", transcript_window=[], coverage_summary="cov",
        active_question=None, candidate_utterance="what do you mean by large result sets?",
        floor=FloorRef(canonical_text="How would you page through large result sets?",
                       kind="probe", thread_question_id="q1"))
    suffix = msgs[-1]["content"]
    assert "ON THE FLOOR" in suffix
    assert "page through large result sets" in suffix
