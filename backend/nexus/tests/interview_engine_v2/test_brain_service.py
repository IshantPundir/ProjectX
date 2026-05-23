import asyncio

import pytest

from app.modules.interview_engine_v2 import DirectiveAct
from app.modules.interview_engine_v2.brain import ControlPlane
from app.modules.interview_engine_v2.brain import service as brain_service
from app.modules.interview_engine_v2.brain.decision import (
    BrainDecision,
    BrainMove,
    CandidateIntent,
    CoverageDeltaItem,
)
from app.modules.interview_engine_v2.coverage import CoverageState, CoverageTracker
from app.modules.interview_runtime import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    StageConfig,
)

pytestmark = pytest.mark.asyncio


def _q(qid, primary, pos=0, mandatory=True):
    return QuestionConfig(
        id=qid, position=pos, text=f"Tell me about {primary}.", signal_values=[primary],
        estimated_minutes=3.0, is_mandatory=mandatory,
        follow_ups=["What did you own?", "Any tradeoffs?"],
        positive_evidence=["a", "b", "c"], red_flags=["x", "y"],
        rubric=QuestionRubric(excellent="e", meets_bar="m", below_bar="b"),
        evaluation_hint="listen for X", question_kind="behavioral",
        primary_signal=primary, difficulty="medium",
    )


def _config():
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Backend Engineer",
        hiring_company_name="Workato", role_summary="rs", jd_text="jd", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Asha"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="Screen",
                          duration_minutes=30, difficulty="medium",
                          questions=[_q("q1", "python", 0), _q("q2", "kafka", 1)]),
        signals=["python", "kafka"],
    )


def _plane():
    cfg = _config()
    cov = CoverageTracker(signals=["python", "kafka"], mandatory_signals=["python", "kafka"])
    return ControlPlane(config=cfg, coverage=cov), cov


def _patch_brain(monkeypatch, decision: BrainDecision):
    async def _fake(**kwargs):
        return decision
    monkeypatch.setattr(brain_service, "_call_brain", _fake)


def _cov(**kw) -> list[CoverageDeltaItem]:
    return [CoverageDeltaItem(signal=s, state=st) for s, st in kw.items()]


async def test_opener_is_intro_then_ask_first_question_verbatim():
    plane, _ = _plane()
    intro, ask = plane.opener()
    assert intro.act is DirectiveAct.INTRO
    assert ask.act is DirectiveAct.ASK and ask.say == "Tell me about python."   # verbatim bank text


async def test_advance_maps_to_ack_advance_with_verbatim_next_question(monkeypatch):
    plane, cov = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="strong; sufficient; advance", candidate_intent=CandidateIntent.answer,
        grade="strong", coverage_delta=_cov(python="sufficient"), move=BrainMove.advance,
        target_signal="python", bank_question_id="q2"))
    directive, record = await plane.decide(
        turn_ref="t-1", candidate_utterance="I built X in Python with tradeoffs.",
        transcript_window=[("candidate", "...")], active_question_id="q1")
    assert directive.act is DirectiveAct.ACK_ADVANCE and directive.say == "Tell me about kafka."
    assert directive.turn_ref == "t-1"
    assert cov.state("python") is CoverageState.sufficient
    assert record.move == "advance" and record.grade == "strong"
    assert record.directive_id == directive.id


async def test_probe_maps_to_probe_with_verbatim_follow_up(monkeypatch):
    plane, cov = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="thin; partial; probe", candidate_intent=CandidateIntent.answer, grade="thin",
        coverage_delta=_cov(python="partial"), move=BrainMove.probe, target_signal="python",
        bank_follow_up_index=0))
    directive, _ = await plane.decide(turn_ref="t-1", candidate_utterance="we did stuff",
                                      transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.PROBE and directive.say == "What did you own?"
    assert cov.probe_count("q1") == 1                  # probe recorded


async def test_composed_clarify_uses_sanitized_say(monkeypatch):
    plane, _ = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="misunderstood; clarify", candidate_intent=CandidateIntent.clarification_request,
        move=BrainMove.clarify, composed_say="Sure — have you built one yourself?"))
    directive, _ = await plane.decide(turn_ref="t-2", candidate_utterance="what do you mean?",
                                      transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.CLARIFY
    assert directive.say == "Sure — have you built one yourself?"


async def test_unverified_or_knockout_is_downgraded_to_probe(monkeypatch):
    """b99d8cc6: knockout on Java alone when req=Java OR Python OR Ruby must NOT close."""
    plane, _ = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="no java", candidate_intent=CandidateIntent.no_experience,
        move=BrainMove.knockout_close,
        is_knockout=True, or_alternatives=["java", "python", "ruby"], or_alternatives_checked=False,
        reflect_confirmed=True, bank_follow_up_index=1))
    directive, record = await plane.decide(turn_ref="t-3", candidate_utterance="no java",
                                           transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.PROBE                      # NOT a terminal close
    assert directive.is_terminal is False
    assert "knockout_or_unverified" in record.policy_checks


async def test_verified_knockout_closes(monkeypatch):
    plane, _ = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="confirmed absent across all alts", candidate_intent=CandidateIntent.indirect_no,
        move=BrainMove.knockout_close, is_knockout=True, or_alternatives=["python"],
        or_alternatives_checked=True, reflect_confirmed=True,
        coverage_delta=_cov(python="failed"), composed_say="No worries — thanks for your time."))
    directive, _ = await plane.decide(turn_ref="t-4", candidate_utterance="no, not really",
                                      transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.CLOSE and directive.is_terminal is True


async def test_brain_timeout_falls_back_to_safe_directive(monkeypatch):
    plane, _ = _plane()
    async def _hang(**kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(brain_service, "_call_brain", _hang)
    directive, record = await plane.decide(turn_ref="t-5", candidate_utterance="...",
                                           transcript_window=[], active_question_id="q1",
                                           budget_ms=50)
    assert directive.act in (DirectiveAct.ACK_ADVANCE, DirectiveAct.CLOSE)   # never stalls
    assert "fallback" in record.move


async def test_consecutive_fallbacks_advance_then_close(monkeypatch):
    """Defense-in-depth: under TOTAL brain failure, each fallback must walk FORWARD by position
    (never re-ask the same question), then CLOSE once the bank is exhausted — the infinite-Q1
    loop guard. No pointer yet => fallback#1 q1, fallback#2 q2, fallback#3 CLOSE."""
    plane, _ = _plane()                              # no opener() -> pointer is None

    async def _boom(**kwargs):
        raise RuntimeError("brain down")
    monkeypatch.setattr(brain_service, "_call_brain", _boom)

    d1, r1 = await plane.decide(turn_ref="t-1", candidate_utterance="...", transcript_window=[])
    d2, r2 = await plane.decide(turn_ref="t-2", candidate_utterance="...", transcript_window=[])
    assert d1.act is DirectiveAct.ACK_ADVANCE and d2.act is DirectiveAct.ACK_ADVANCE
    assert d1.say != d2.say                           # DIFFERENT questions — no infinite-Q1 loop
    assert d1.say == "Tell me about python." and d2.say == "Tell me about kafka."
    assert r1.move == "fallback_advance" and r2.move == "fallback_advance"
    assert plane.active_question_id == "q2"           # pointer walked forward each fallback

    d3, r3 = await plane.decide(turn_ref="t-3", candidate_utterance="...", transcript_window=[])
    assert d3.act is DirectiveAct.CLOSE and d3.is_terminal is True   # ran off the end -> close
    assert r3.move == "fallback_close"


async def test_fallback_advances_past_active_question(monkeypatch):
    """With the pointer ON q1, a fallback must NOT re-ask q1 — it advances strictly past it."""
    plane, _ = _plane()
    plane.opener()                                   # pointer -> q1 (position 0)

    async def _boom(**kwargs):
        raise RuntimeError("brain down")
    monkeypatch.setattr(brain_service, "_call_brain", _boom)

    d1, _ = await plane.decide(turn_ref="t-1", candidate_utterance="...", transcript_window=[])
    assert d1.act is DirectiveAct.ACK_ADVANCE
    assert d1.say == "Tell me about kafka."          # advanced PAST q1 (the infinite-Q1 bug)
    assert plane.active_question_id == "q2"


async def test_probe_invalid_index_degrades_to_advance_and_moves_pointer(monkeypatch):
    plane, _ = _plane()
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="probe but bad index", candidate_intent=CandidateIntent.answer, grade="thin",
        coverage_delta=_cov(python="partial"), move=BrainMove.probe, target_signal="python",
        bank_follow_up_index=99, bank_question_id="q2"))
    directive, _ = await plane.decide(turn_ref="t-1", candidate_utterance="...",
                                      transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.ACK_ADVANCE and directive.say == "Tell me about kafka."
    assert plane.active_question_id == "q2"          # pointer MUST move (the bug being fixed)


def _config3():
    """A 3-question bank so the repeat-guard has somewhere unasked to advance to."""
    return SessionConfig(
        session_id="s", job_id="j", candidate_id="c", job_title="Backend Engineer",
        hiring_company_name="Workato", role_summary="rs", jd_text="jd", seniority_level="mid",
        company=CompanyContext(about="a", industry="i", hiring_bar="h"),
        candidate=CandidateContext(name="Asha"),
        stage=StageConfig(stage_id="st", stage_type="ai_screening", name="Screen",
                          duration_minutes=30, difficulty="medium",
                          questions=[_q("q1", "python", 0), _q("q2", "kafka", 1),
                                     _q("q3", "redis", 2)]),
        signals=["python", "kafka", "redis"],
    )


async def test_advance_does_not_re_ask_an_already_asked_question(monkeypatch):
    """Repeat-guard (d9828b7b talk-test: turn [24] re-asked turn [7] verbatim). The brain picks the
    next question by id from a SIGNAL-based view + a bounded transcript window, so it can re-pick a
    question already physically asked (its signal stayed 'none' after an 'I don't know', and the
    earlier ask scrolled out of the window). The controller MUST NOT voice a verbatim repeat — it
    advances to the next UNASKED question instead."""
    cfg = _config3()
    cov = CoverageTracker(signals=["python", "kafka", "redis"],
                          mandatory_signals=["python", "kafka", "redis"])
    plane = ControlPlane(config=cfg, coverage=cov)
    plane.opener()                                            # asks q1

    _patch_brain(monkeypatch, BrainDecision(
        reasoning="advance", candidate_intent=CandidateIntent.no_experience,
        grade="thin", move=BrainMove.advance, bank_question_id="q2"))
    d2, _ = await plane.decide(turn_ref="t-1", candidate_utterance="I don't know.",
                               transcript_window=[])
    assert d2.say == "Tell me about kafka."                   # q2 asked

    # brain now (wrongly) re-picks q1 — already physically asked at the opener
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="re-picks q1", candidate_intent=CandidateIntent.no_experience,
        grade="thin", move=BrainMove.advance, bank_question_id="q1"))
    d3, _ = await plane.decide(turn_ref="t-2", candidate_utterance="I don't know.",
                               transcript_window=[])
    assert d3.act is DirectiveAct.ACK_ADVANCE
    assert d3.say == "Tell me about redis."                   # the UNASKED q3, NOT q1 again
    assert plane.active_question_id == "q3"


async def test_advance_to_already_asked_with_no_unasked_left_closes(monkeypatch):
    """When the brain re-picks an asked question and NOTHING unasked remains, advancing is
    impossible — close out warmly rather than repeating."""
    plane, _ = _plane()                                       # 2-question bank
    plane.opener()                                            # asks q1
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="advance to q2", candidate_intent=CandidateIntent.answer, grade="thin",
        move=BrainMove.advance, bank_question_id="q2"))
    await plane.decide(turn_ref="t-1", candidate_utterance="...", transcript_window=[])  # asks q2
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="re-picks q1, both asked", candidate_intent=CandidateIntent.answer, grade="thin",
        move=BrainMove.advance, bank_question_id="q1"))
    d, _ = await plane.decide(turn_ref="t-2", candidate_utterance="...", transcript_window=[])
    assert d.act is DirectiveAct.CLOSE and d.is_terminal is True


async def test_advance_unknown_question_id_falls_back_to_next_unasked(monkeypatch):
    """Issue-2 hardening (ec11e237): a brain 'advance' that names an unknown/garbled id should
    CONTINUE to the next unasked question (more robust than closing) — closing is reserved for an
    exhausted bank (test_advance_to_already_asked_with_no_unasked_left_closes covers that)."""
    plane, _ = _plane()                                  # 2-q bank, opener not called -> none asked
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="advance to nowhere", candidate_intent=CandidateIntent.answer, grade="thin",
        move=BrainMove.advance, bank_question_id="does-not-exist"))
    directive, _ = await plane.decide(turn_ref="t-1", candidate_utterance="...",
                                      transcript_window=[], active_question_id="q1")
    assert directive.act is DirectiveAct.ACK_ADVANCE     # continues, does NOT close
    assert directive.say in ("Tell me about python.", "Tell me about kafka.")


async def test_downgraded_knockout_continues_not_closes(monkeypatch):
    """Issue-2 (ec11e237): an UNVERIFIED knockout_close must not accidentally CLOSE the screen via
    the probe->advance degrade fall-through while unasked questions remain. The policy downgrades it
    (or_alternatives unverified -> probe); with no follow-up + no next-q named, the engine must
    CONTINUE to the next unasked question, never close on an unverified absence."""
    cfg = _config3()                                     # q1, q2, q3
    cov = CoverageTracker(signals=["python", "kafka", "redis"],
                          mandatory_signals=["python", "kafka", "redis"])
    plane = ControlPlane(config=cfg, coverage=cov)
    plane.opener()                                       # asks q1
    _patch_brain(monkeypatch, BrainDecision(
        reasoning="thinks mandatory absent, OR-group unverified", grade="thin",
        candidate_intent=CandidateIntent.no_experience, move=BrainMove.knockout_close,
        is_knockout=True, or_alternatives=["python", "kafka"], or_alternatives_checked=False,
        reflect_confirmed=False, bank_follow_up_index=None))
    directive, record = await plane.decide(
        turn_ref="t-1", candidate_utterance="I don't know any of that", transcript_window=[])
    assert "knockout_or_unverified" in record.policy_checks   # the downgrade fired
    assert directive.is_terminal is False                     # did NOT close (unverified absence)
    assert directive.act is DirectiveAct.ACK_ADVANCE
    assert directive.say in ("Tell me about kafka.", "Tell me about redis.")  # an UNASKED question


async def test_generic_brain_error_falls_back(monkeypatch):
    plane, _ = _plane()
    async def _boom(**kwargs):
        raise RuntimeError("brain exploded")
    monkeypatch.setattr(brain_service, "_call_brain", _boom)
    directive, record = await plane.decide(turn_ref="t-1", candidate_utterance="...",
                                           transcript_window=[], active_question_id="q1")
    assert directive.act in (DirectiveAct.ACK_ADVANCE, DirectiveAct.CLOSE)
    assert "fallback" in record.move


# ---------------------------------------------------------------------------
# Task 7 — pure build_speculative_directive (Option C non-voiced pre-stage, D3)
# ---------------------------------------------------------------------------
build_speculative_directive = brain_service.build_speculative_directive


def test_speculative_directive_is_speculative_and_non_voiced_shape():
    plane, cov = _plane()
    spec = build_speculative_directive(plane, anticipated_turn_ref="t-2")
    assert spec.speculative is True
    assert spec.turn_ref == "t-2"
    assert spec.is_terminal is False                       # a pre-stage is never terminal
    # points at the next uncovered question (deterministic guess; never voiced — superseded later)
    assert spec.act.value in ("ACK_ADVANCE", "HOLD")


def test_speculative_directive_holds_when_all_covered():
    plane, cov = _plane()
    cov.apply_delta({"python": "sufficient"})
    cov.apply_delta({"kafka": "sufficient"})
    spec = build_speculative_directive(plane, anticipated_turn_ref="t-9")
    assert spec.act.value == "HOLD"                        # nothing left to advance to -> hold


def test_speculative_directive_is_side_effect_free():
    """D3: a pre-stage NEVER mutates the single source of truth (coverage) or the brain pointer."""
    plane, cov = _plane()
    before = cov.summary_for_result()
    before_pointer = plane.active_question_id
    build_speculative_directive(plane, anticipated_turn_ref="t-2")
    assert cov.summary_for_result() == before               # coverage untouched
    assert plane.active_question_id == before_pointer        # pointer untouched
