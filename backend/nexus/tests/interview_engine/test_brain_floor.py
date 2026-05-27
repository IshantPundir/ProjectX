import pytest

from app.modules.interview_engine.brain import ControlPlane
from app.modules.interview_engine.brain import service as brain_service
from app.modules.interview_engine.brain.decision import BrainDecision, BrainMove, CandidateIntent
from app.modules.interview_engine.brain.input_builder import build_brain_messages
from app.modules.interview_engine.coverage import CoverageTracker
from app.modules.interview_engine.directive import DirectiveAct

# Reuse the SessionConfig builders (`_config`, `_q`) defined in the brain eval module:
from tests.interview_engine.prompt_evals.test_brain_evals import _config, _q  # type: ignore

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
    from app.modules.interview_engine.brain.service import FloorRef
    msgs = build_brain_messages(
        stable_prefix="PREFIX", transcript_window=[], coverage_summary="cov",
        active_question=None, candidate_utterance="what do you mean by large result sets?",
        floor=FloorRef(canonical_text="How would you page through large result sets?",
                       kind="probe", thread_question_id="q1"))
    suffix = msgs[-1]["content"]
    assert "ON THE FLOOR" in suffix
    assert "page through large result sets" in suffix


async def test_fallback_advance_updates_floor_to_new_question(monkeypatch):
    """A brain timeout falls back to ACK_ADVANCE the next question — the floor MUST move with it,
    or a 'what do you mean?' next turn would clarify the stale prior question."""
    plane = _plane()
    plane.opener()  # floor -> q1

    async def boom(*, messages, correlation_id):
        raise TimeoutError("brain too slow")
    monkeypatch.setattr(brain_service, "_call_brain", boom)
    directive, record = await plane.decide(turn_ref="t-1", active_question_id="q1",
                                           transcript_window=[], candidate_utterance="ok")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert "fallback" in record.move
    # floor moved to the newly-asked question (q2), matching the question voiced
    assert plane._floor is not None
    assert plane._floor.thread_question_id == plane._active_question_id
    assert plane._floor.canonical_text == directive.say


async def test_non_question_act_leaves_floor_unchanged(monkeypatch):
    """A HOLD (non-question) must NOT change the floor — the question on the floor persists."""
    plane = _plane()
    plane.opener()
    floor_before = plane._floor

    async def hold(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.nervous,
                             move=BrainMove.hold, composed_say="Take your time.")
    monkeypatch.setattr(brain_service, "_call_brain", hold)
    await plane.decide(turn_ref="t-1", active_question_id="q1", transcript_window=[],
                       candidate_utterance="um")
    assert plane._floor == floor_before  # unchanged


async def test_ack_advance_records_new_thread_id(monkeypatch):
    """On advance, the floor's thread_question_id must be the NEW active question."""
    plane = _plane()
    plane.opener()  # active=q1

    async def adv(*, messages, correlation_id):
        return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                             move=BrainMove.advance, grade="concrete", bank_question_id="q2")
    monkeypatch.setattr(brain_service, "_call_brain", adv)
    directive, _ = await plane.decide(turn_ref="t-1", active_question_id="q1",
                                      transcript_window=[], candidate_utterance="done")
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert plane._floor.kind == "main"
    assert plane._floor.thread_question_id == "q2"
