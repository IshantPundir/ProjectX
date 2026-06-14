"""
Tests for app.modules.interview_engine.brain.service — ControlPlane.decide (D3).

Coverage:
 1. probe → act=probe, verbatim follow-up
 2. ask → resolver-picked next question text
 3. coverage projection updated after decide
 4. leak-scrub on composed_say (clarify with rubric text → fallback)
 5. candidate end-request → terminal close (the kept end_requested bypass)
 6. probe with exhausted index → falls back to ask
 7. close → say None + is_terminal
 8. LLM is mocked — injected llm_call was awaited; no real network call

The injected fake `llm_call` is an async callable that returns a canned
BrainTurnOutput. No real OpenAI API call is made in any test.
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.brain.input_builder import (
    CoverageProjection,
    active_question_rubric,
    build_session_context,
)
from app.modules.interview_engine.brain.resolver import (
    ResolverQuestion,
)
from app.modules.interview_engine.brain.service import ControlPlane
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric,
    BrainMove,
    BrainSessionContext,
    BrainTurnInput,
    BrainTurnOutput,
    DirectiveAct,
    SignalObservation,
)
from app.modules.interview_runtime.evidence import (
    CoverageState,
    EvidenceStance,
    EvidenceTexture,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SignalMetadata,
    StageConfig,
)

# ============================================================================
# Fixtures
# ============================================================================

_EXCELLENT_TEXT = "Excellent_rubric_string_that_is_long_enough_for_scrub"
_MEETS_BAR_TEXT = "Meets_bar_rubric_text_for_test"
_FOLLOW_UP_DIM_0 = {"dimension": "concrete_example", "intent": "elicit specifics", "seed_probe": "Can you give a concrete example of that system?", "listen_for": []}
_FOLLOW_UP_DIM_1 = {"dimension": "measure_impact", "intent": "verify measurable outcome", "seed_probe": "How did you measure the impact?", "listen_for": []}
_FOLLOW_UP_SEED_0 = "Can you give a concrete example of that system?"
_FOLLOW_UP_SEED_1 = "How did you measure the impact?"

_Q1_TEXT = "Tell me about your distributed systems experience."
_Q2_TEXT = "Describe an incident you led and resolved."


def _make_question(
    qid: str,
    text: str,
    signal: str,
    *,
    position: int = 0,
    excellent: str = _EXCELLENT_TEXT,
    meets_bar: str = _MEETS_BAR_TEXT,
) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=position,
        text=text,
        signal_values=[signal],
        follow_ups=[_FOLLOW_UP_DIM_0, _FOLLOW_UP_DIM_1],
        positive_evidence=["positive A", "positive B", "positive C"],
        red_flags=["red flag 1", "red flag 2"],
        rubric=QuestionRubric(
            excellent=excellent,
            meets_bar=meets_bar,
            below_bar="below_bar_default",
        ),
        evaluation_hint="Evaluate based on concrete examples.",
        question_kind="technical_scenario",
        primary_signal=signal,
        difficulty="medium",
    )


def _make_session_config() -> SessionConfig:
    return SessionConfig(
        session_id="sess-test-001",
        job_id="job-001",
        candidate_id="cand-001",
        job_title="Senior Backend Engineer",
        role_summary="Build distributed systems at scale.",
        seniority_level="senior",
        company=CompanyContext(
            about="A fast-growing fintech.",
            industry="fintech",
            hiring_bar="high",
        ),
        candidate=CandidateContext(name="Priya"),
        stage=StageConfig(
            stage_id="stage-001",
            stage_type="ai_screening",
            name="AI Screen",
            duration_minutes=30,
            difficulty="medium",
            questions=[
                _make_question("q-001", _Q1_TEXT, "distributed_systems", position=1),
                _make_question("q-002", _Q2_TEXT, "incident_response", position=2),
            ],
        ),
        signals=["distributed_systems", "incident_response"],
        signal_metadata=[
            SignalMetadata(
                value="distributed_systems",
                type="competency",
                priority="required",
                weight=3,
                knockout=False,
                stage="screen",
                evaluation_method="verbal_response",
            ),
            SignalMetadata(
                value="incident_response",
                type="competency",
                priority="preferred",
                weight=2,
                knockout=False,
                stage="screen",
                evaluation_method="verbal_response",
            ),
        ],
    )


def _make_active_rubric(qid: str = "q-001", fired_dimensions: list[str] | None = None) -> ActiveQuestionRubric:
    """Build an ActiveQuestionRubric matching q-001 in the fixture config."""
    from app.modules.interview_engine.contracts import FollowUpDimension
    return ActiveQuestionRubric(
        question_id=qid,
        text=_Q1_TEXT,
        excellent=_EXCELLENT_TEXT,
        meets_bar=_MEETS_BAR_TEXT,
        below_bar="below_bar_default",
        positive_evidence=["positive A", "positive B", "positive C"],
        red_flags=["red flag 1", "red flag 2"],
        evaluation_hint="Evaluate based on concrete examples.",
        follow_ups=[
            FollowUpDimension(dimension="concrete_example", intent="elicit specifics", seed_probe=_FOLLOW_UP_SEED_0, listen_for=[]),
            FollowUpDimension(dimension="measure_impact", intent="verify measurable outcome", seed_probe=_FOLLOW_UP_SEED_1, listen_for=[]),
        ],
        fired_dimensions=fired_dimensions or [],
    )


def _make_turn_input(
    active_rubric: ActiveQuestionRubric | None = None,
    candidate_utterance: str = "I worked on Kafka-based pipelines for three years.",
    on_the_floor: str = _Q1_TEXT,
) -> BrainTurnInput:
    rubric = active_rubric or _make_active_rubric()
    return BrainTurnInput(
        turn_ref="t-01",
        active_question=rubric,
        on_the_floor=on_the_floor,
        candidate_utterance=candidate_utterance,
        thread_turn_count=1,
        evidence_so_far=[],
        transcript_window=[],
        uncovered_signals=["distributed_systems", "incident_response"],
    )


def _make_resolver_questions() -> list[ResolverQuestion]:
    return [
        ResolverQuestion(
            question_id="q-001",
            primary_signal="distributed_systems",
            position=1,
        ),
        ResolverQuestion(
            question_id="q-002",
            primary_signal="incident_response",
            position=2,
        ),
    ]


def _make_control_plane(
    *,
    llm_call=None,
    projection: CoverageProjection | None = None,
    resolver_questions: list[ResolverQuestion] | None = None,
) -> ControlPlane:
    config = _make_session_config()
    session_context = build_session_context(config)
    return ControlPlane(
        session_context=session_context,
        system_prompt="You are a helpful interview brain.",
        projection=projection or CoverageProjection(),
        resolver_questions=resolver_questions or _make_resolver_questions(),
        llm_call=llm_call,
    )


# ============================================================================
# Async fixture helpers
# ============================================================================

def _fake_llm(output: BrainTurnOutput):
    """Return an async callable that records calls and always returns `output`."""
    calls = []

    async def _call(messages: list[dict]) -> BrainTurnOutput:
        calls.append(messages)
        return output

    _call.calls = calls  # type: ignore[attr-defined]
    return _call


# ============================================================================
# Test 1: probe → act=probe, verbatim follow-up text
# ============================================================================

@pytest.mark.asyncio
async def test_probe_falls_back_to_verbatim_followup_when_not_composed():
    """move=probe, probe_dimension="concrete_example", composed_say=None → seed_probe fallback.

    Composition is primary; the dimension's seed_probe is the safety net when
    the brain does not compose a targeted probe.
    """
    canned = BrainTurnOutput(
        reasoning="Good answer, but needs specifics.",
        observations=[],
        move=BrainMove.probe,
        probe_dimension="concrete_example",
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.probe
    assert decision.directive.say == _FOLLOW_UP_SEED_0
    assert decision.directive.is_terminal is False
    # The served dimension slug is tracked for fire-once / coverage.
    assert decision.probe_dimension == "concrete_example"


@pytest.mark.asyncio
async def test_probe_uses_composed_targeted_text_when_provided():
    """move=probe with composed_say → directive.say is the COMPOSED probe (not seed_probe),
    and the served dimension slug is carried on the decision."""
    composed = "You said it was one startup — was that the whole five years, or split across a few?"
    canned = BrainTurnOutput(
        reasoning="Adapt the tenure follow-up to what they actually said; stay in experience scope.",
        observations=[],
        move=BrainMove.probe,
        probe_dimension="measure_impact",
        preferred_next_signal=None,
        composed_say=composed,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.probe
    assert decision.directive.say == composed  # composed text, NOT seed_probe
    assert decision.directive.say != _FOLLOW_UP_SEED_1
    assert decision.probe_dimension == "measure_impact"
    assert decision.directive.is_terminal is False


@pytest.mark.asyncio
async def test_composed_probe_is_leak_scrubbed():
    """A composed probe that echoes a rubric secret is scrubbed to the safe fallback.

    The same no-leak gate that guards clarify/redirect now guards composed probes.
    """
    from app.modules.interview_engine.brain.policy import SAFE_FALLBACK

    canned = BrainTurnOutput(
        reasoning="Composing a probe but accidentally echoing the rubric.",
        observations=[],
        move=BrainMove.probe,
        probe_dimension="concrete_example",
        preferred_next_signal=None,
        composed_say=f"Well, {_EXCELLENT_TEXT} — can you speak to that?",
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.probe
    assert decision.directive.say == SAFE_FALLBACK
    assert _EXCELLENT_TEXT not in (decision.directive.say or "")


# ============================================================================
# Test 2: ask → resolver picks next question, say == bank text
# ============================================================================

@pytest.mark.asyncio
async def test_ask_returns_resolver_next_question_text():
    """move=ask → directive.act==ask, say == the bank question text for the next question."""
    canned = BrainTurnOutput(
        reasoning="Good answer, advance.",
        observations=[],
        move=BrainMove.ask,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    # q-001 is already asked → resolver should pick q-002
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.ask
    # The say should be the bank text for q-002
    assert decision.directive.say == _Q2_TEXT
    assert decision.directive.is_terminal is False


# ============================================================================
# Test 3: coverage projection updated after decide
# ============================================================================

@pytest.mark.asyncio
async def test_coverage_projection_updated():
    """After decide, projection.signal_reads() reflects the canned observation."""
    obs = SignalObservation(
        signal="distributed_systems",
        stance=EvidenceStance.supports,
        texture=EvidenceTexture.concrete,
        coverage_after=CoverageState.partial,
    )
    canned = BrainTurnOutput(
        reasoning="Some coverage.",
        observations=[obs],
        move=BrainMove.probe,
        probe_dimension="concrete_example",
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    projection = CoverageProjection()
    cp = _make_control_plane(llm_call=llm, projection=projection)
    turn = _make_turn_input()

    await cp.decide(turn, asked_ids={"q-001"})

    reads = projection.signal_reads()
    assert len(reads) == 1
    assert reads[0].signal == "distributed_systems"
    assert reads[0].coverage == CoverageState.partial
    assert reads[0].last_stance == EvidenceStance.supports
    # established_quote should be the candidate utterance
    assert reads[0].established_quote == turn.candidate_utterance


# ============================================================================
# Test 4: leak-scrub on composed_say (clarify with rubric text → fallback)
# ============================================================================

@pytest.mark.asyncio
async def test_clarify_with_leaked_rubric_gets_scrubbed():
    """move=clarify with composed_say echoing rubric excellent → say is fallback, not the leak."""
    # The excellent text is embedded in composed_say — this is a leak
    leaky_say = f"I see — {_EXCELLENT_TEXT} is what we're looking for, could you clarify?"
    canned = BrainTurnOutput(
        reasoning="Candidate misunderstood.",
        observations=[],
        move=BrainMove.clarify,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=leaky_say,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.clarify
    # The composed_say contains the rubric text — must be scrubbed
    assert decision.directive.say != leaky_say
    # The fallback should not be None
    assert decision.directive.say is not None


# ============================================================================
# Test 5: candidate end-request → terminal close (the kept end_requested bypass)
# ============================================================================

@pytest.mark.asyncio
async def test_candidate_end_request_yields_terminal_close():
    """move=close + end_requested=True → terminal close.

    A candidate may always end the screen — the end_requested path yields an
    immediate terminal close.
    """
    canned = BrainTurnOutput(
        reasoning="Candidate asked to end the interview now.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
        end_requested=True,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    # Candidate-initiated end is honored.
    assert decision.directive.is_terminal is True
    assert decision.directive.act == DirectiveAct.close


@pytest.mark.asyncio
async def test_hold_returns_nonterminal_directive():
    """move=hold (candidate is thinking) → non-terminal hold directive; no advance, no probe."""
    canned = BrainTurnOutput(
        reasoning="Candidate said 'let me think about it' — they need a moment.",
        observations=[],
        move=BrainMove.hold,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say="Take your time, no rush.",
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.hold
    assert decision.directive.is_terminal is False
    assert decision.next_question_id is None


@pytest.mark.asyncio
async def test_confirm_returns_nonterminal_directive():
    """move=confirm (possible STT mishearing) → non-terminal confirm directive (reflect back)."""
    canned = BrainTurnOutput(
        reasoning="Heard 'Vocatto' — likely 'Workato' misheard; reflect back before grading.",
        observations=[],
        move=BrainMove.confirm,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say="Just to check — did you say Workato?",
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001"})

    assert decision.directive.act == DirectiveAct.confirm
    assert decision.directive.is_terminal is False
    assert decision.next_question_id is None


# ============================================================================
# Test 6: probe with exhausted index → falls back to ask
# ============================================================================

@pytest.mark.asyncio
async def test_probe_exhausted_falls_back_to_ask():
    """All dimensions fired → probe falls back to ask."""
    canned = BrainTurnOutput(
        reasoning="Want to probe but no probes left.",
        observations=[],
        move=BrainMove.probe,
        probe_dimension="concrete_example",  # brain wants this but both dims are fired
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    # Make an active rubric with all dimensions fired
    exhausted_rubric = _make_active_rubric(fired_dimensions=["concrete_example", "measure_impact"])
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input(active_rubric=exhausted_rubric)

    decision = await cp.decide(turn, asked_ids={"q-001"})

    # Probe exhausted → falls back to ask
    assert decision.directive.act == DirectiveAct.ask
    # say should be the next unasked bank question text
    assert decision.directive.say == _Q2_TEXT


# ============================================================================
# Test 7: close → say None + is_terminal
# ============================================================================

@pytest.mark.asyncio
async def test_close_say_none_and_terminal():
    """move=close → directive.act==close, say is None, is_terminal True."""
    canned = BrainTurnOutput(
        reasoning="All done.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    decision = await cp.decide(turn, asked_ids={"q-001", "q-002"})

    assert decision.directive.act == DirectiveAct.close
    assert decision.directive.say is None
    assert decision.directive.is_terminal is True
    assert decision.is_terminal is True


# ============================================================================
# Test 8: LLM is mocked — injected llm_call was awaited with built messages
# ============================================================================

@pytest.mark.asyncio
async def test_llm_mocked_and_called_with_messages():
    """Assert the injected llm_call was awaited exactly once with the messages list."""
    canned = BrainTurnOutput(
        reasoning="Simple probe.",
        observations=[],
        move=BrainMove.probe,
        probe_dimension="concrete_example",
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    cp = _make_control_plane(llm_call=llm)
    turn = _make_turn_input()

    await cp.decide(turn, asked_ids={"q-001"})

    # Exactly one call was made
    assert len(llm.calls) == 1
    # The messages list is non-empty and is a list of dicts
    msgs = llm.calls[0]
    assert isinstance(msgs, list)
    assert len(msgs) > 0
    for msg in msgs:
        assert isinstance(msg, dict)
        assert "role" in msg
        assert "content" in msg


