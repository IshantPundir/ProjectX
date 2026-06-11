"""
Tests for app.modules.interview_engine.brain.service — ControlPlane.decide (D3).

Coverage:
 1. probe → act=probe, verbatim follow-up
 2. ask → resolver-picked next question text
 3. coverage projection updated after decide
 4. leak-scrub on composed_say (clarify with rubric text → fallback)
 5. knockout blocks premature close → non-terminal directive
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
from app.modules.interview_engine.brain.policy import (
    KnockoutTracker,
)
from app.modules.interview_engine.brain.resolver import (
    BudgetConfig,
    ResolverQuestion,
)
from app.modules.interview_engine.brain.service import ControlPlane
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric,
    BrainMove,
    BrainSessionContext,
    BrainTurnInput,
    BrainTurnOutput,
    BudgetPhase,
    DirectiveAct,
    SignalObservation,
    SignalSpec,
)
from app.modules.interview_runtime.evidence import (
    CoverageState,
    EvidenceStance,
    EvidenceTexture,
    SignalPriority,
    SignalType,
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
    is_mandatory: bool = False,
    position: int = 0,
    excellent: str = _EXCELLENT_TEXT,
    meets_bar: str = _MEETS_BAR_TEXT,
) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=position,
        text=text,
        signal_values=[signal],
        estimated_minutes=5.0,
        is_mandatory=is_mandatory,
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
                _make_question("q-001", _Q1_TEXT, "distributed_systems", is_mandatory=True, position=1),
                _make_question("q-002", _Q2_TEXT, "incident_response", is_mandatory=False, position=2),
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
    knockout_pending: list[str] | None = None,
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
        budget_phase=BudgetPhase.on_track,
        uncovered_signals=["distributed_systems", "incident_response"],
        knockout_pending=knockout_pending or [],
    )


def _make_resolver_questions() -> list[ResolverQuestion]:
    return [
        ResolverQuestion(
            question_id="q-001",
            primary_signal="distributed_systems",
            tier="core",
            is_mandatory=True,
            position=1,
            weight=3,
            estimated_minutes=5.0,
        ),
        ResolverQuestion(
            question_id="q-002",
            primary_signal="incident_response",
            tier="core",
            is_mandatory=False,
            position=2,
            weight=2,
            estimated_minutes=5.0,
        ),
    ]


def _make_all_specs() -> list[SignalSpec]:
    return [
        SignalSpec(signal="distributed_systems", signal_type=SignalType.competency, weight=3, priority=SignalPriority.required, knockout=False),
        SignalSpec(signal="incident_response", signal_type=SignalType.competency, weight=2, priority=SignalPriority.preferred, knockout=False),
    ]


def _make_control_plane(
    *,
    llm_call=None,
    projection: CoverageProjection | None = None,
    resolver_questions: list[ResolverQuestion] | None = None,
    all_specs: list[SignalSpec] | None = None,
    knockout_tracker: KnockoutTracker | None = None,
) -> ControlPlane:
    config = _make_session_config()
    session_context = build_session_context(config)
    return ControlPlane(
        session_context=session_context,
        system_prompt="You are a helpful interview brain.",
        projection=projection or CoverageProjection(),
        resolver_questions=resolver_questions or _make_resolver_questions(),
        all_specs=all_specs or _make_all_specs(),
        budget_cfg=BudgetConfig(close_reserve_s=45.0, winding_down_s=90.0),
        knockout_tracker=knockout_tracker,
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


def _fake_llm_seq(outputs: list[BrainTurnOutput]):
    """Return an async callable that yields `outputs` in order, one per decide() call."""
    seq = iter(outputs)

    async def _call(messages: list[dict]) -> BrainTurnOutput:
        return next(seq)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    assert decision.directive.act == DirectiveAct.clarify
    # The composed_say contains the rubric text — must be scrubbed
    assert decision.directive.say != leaky_say
    # The fallback should not be None
    assert decision.directive.say is not None


# ============================================================================
# Test 5: knockout blocks premature close → non-terminal directive
# ============================================================================

@pytest.mark.asyncio
async def test_knockout_blocks_premature_close():
    """move=close with knockout_pending=['distributed_systems'] → directive is NOT terminal close."""
    canned = BrainTurnOutput(
        reasoning="Looks done to me.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    # Use a knockout spec so the tracker finds a pending signal
    all_specs = [
        SignalSpec(
            signal="distributed_systems",
            signal_type=SignalType.competency,
            weight=3,
            priority=SignalPriority.required,
            knockout=True,  # this is a knockout signal
        ),
    ]
    cp = _make_control_plane(llm_call=llm, all_specs=all_specs)
    # Inject knockout_pending directly in the turn input
    turn = _make_turn_input(knockout_pending=["distributed_systems"])

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    # The premature close must be blocked
    assert decision.directive.is_terminal is False
    assert decision.directive.act != DirectiveAct.close


@pytest.mark.asyncio
async def test_candidate_end_request_bypasses_knockout_gate():
    """move=close + end_requested=True → terminal close EVEN WITH a knockout pending.

    A candidate may always end the screen; the knockout-verification gate only
    blocks a brain-INITIATED close, never a candidate's explicit end request.
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
    all_specs = [
        SignalSpec(
            signal="distributed_systems",
            signal_type=SignalType.competency,
            weight=3,
            priority=SignalPriority.required,
            knockout=True,
        ),
    ]
    cp = _make_control_plane(llm_call=llm, all_specs=all_specs)
    turn = _make_turn_input(knockout_pending=["distributed_systems"])

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    # Candidate-initiated end is honored despite the pending knockout.
    assert decision.directive.is_terminal is True
    assert decision.directive.act == DirectiveAct.close


def _make_knockout_control_plane(llm_call) -> ControlPlane:
    """Build a ControlPlane whose session context + all_specs carry a knockout signal
    (distributed_systems), so confirmed_knockout_signals() can surface it at finalize."""
    config = _make_session_config()
    config.signal_metadata[0].knockout = True  # distributed_systems → knockout
    session_context = build_session_context(config)
    all_specs = [
        SignalSpec(
            signal="distributed_systems",
            signal_type=SignalType.competency,
            weight=3,
            priority=SignalPriority.required,
            knockout=True,
        ),
    ]
    return ControlPlane(
        session_context=session_context,
        system_prompt="You are a helpful interview brain.",
        projection=CoverageProjection(),
        resolver_questions=_make_resolver_questions(),
        all_specs=all_specs,
        budget_cfg=BudgetConfig(close_reserve_s=45.0, winding_down_s=90.0),
        knockout_tracker=None,
        llm_call=llm_call,
    )


@pytest.mark.asyncio
async def test_knockout_close_forces_reflect_back_before_recording():
    """move=close + knockout_confirmed=True on the FIRST disclaim (no prior reflect-back)
    → the engine does NOT end yet; it forces ONE reflect-back confirm and records nothing.

    Robustness guarantee: a knockout never ends the screen without first reflecting it
    back to the candidate (guards STT mishearing / a misread scope).
    """
    canned = BrainTurnOutput(
        reasoning="Candidate disclaimed the mandatory skill — but no reflect-back yet.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
        knockout_confirmed=True,
    )
    cp = _make_knockout_control_plane(_fake_llm(canned))
    turn = _make_turn_input(knockout_pending=["distributed_systems"])

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    # Reflect-back forced; the screen is NOT ended and nothing is recorded yet.
    assert decision.directive.is_terminal is False
    assert decision.directive.act == DirectiveAct.confirm
    assert cp.confirmed_knockout_signals() == []


@pytest.mark.asyncio
async def test_brain_confirmed_knockout_closes_after_reflect_back():
    """A reflect-back confirm (turn 1) THEN move=close + knockout_confirmed (turn 2) →
    terminal close AND the signal is recorded. Mirrors the natural disclaim→confirm→close flow.
    """
    reflect_out = BrainTurnOutput(
        reasoning="Reflecting the mandatory-skill absence back to the candidate.",
        observations=[],
        move=BrainMove.confirm,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say="So you haven't worked with that directly yet — is that right?",
        knockout_confirmed=False,
    )
    close_out = BrainTurnOutput(
        reasoning="Candidate confirmed the absence — close and record the knockout.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
        knockout_confirmed=True,
    )
    cp = _make_knockout_control_plane(_fake_llm_seq([reflect_out, close_out]))
    turn = _make_turn_input(knockout_pending=["distributed_systems"])

    # Turn 1: the reflect-back (registers the reflect; non-terminal).
    d1 = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)
    assert d1.directive.is_terminal is False
    assert cp.confirmed_knockout_signals() == []

    # Turn 2: the close is now honored and the knockout recorded.
    d2 = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)
    assert d2.directive.is_terminal is True
    assert d2.directive.act == DirectiveAct.close
    assert cp.confirmed_knockout_signals() == ["distributed_systems"]


@pytest.mark.asyncio
async def test_knockout_reflected_hint_injected_on_turn_after_reflect():
    """After a reflect-back (confirm move while a knockout is pending), the NEXT brain
    call's messages carry the 'KNOCKOUT ALREADY REFLECTED' hint → the brain closes
    instead of re-confirming. (Guards the double-confirm UX.)"""
    reflect_out = BrainTurnOutput(
        reasoning="Reflecting the Workato absence back.",
        observations=[],
        move=BrainMove.confirm,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say="So you haven't worked with that directly yet — is that right?",
        knockout_confirmed=False,
    )
    second_out = BrainTurnOutput(
        reasoning="Second turn.",
        observations=[],
        move=BrainMove.confirm,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say="anything",
        knockout_confirmed=False,
    )
    llm = _fake_llm_seq([reflect_out, second_out])
    captured: list[list[dict]] = []

    async def _capturing(messages):
        captured.append(messages)
        return await llm(messages)

    cp = _make_knockout_control_plane(_capturing)
    turn = _make_turn_input(knockout_pending=["distributed_systems"])

    await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)  # reflect
    await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)  # next turn

    # Turn 1 had no reflected hint; turn 2 carries it.
    assert "KNOCKOUT ALREADY REFLECTED" not in str(captured[0])
    assert "KNOCKOUT ALREADY REFLECTED" in str(captured[1])


@pytest.mark.asyncio
async def test_brain_knockout_confirmed_without_pending_signal_does_not_fabricate():
    """move=close + knockout_confirmed=True but NO matching pending knockout →
    the engine does NOT fabricate a knockout (deterministic guard).

    The brain can only knockout-close a signal the engine itself flagged absent
    (membership in knockout_pending). With nothing pending, no KnockoutOutcome is
    recorded; the close still proceeds as an ordinary close (gate allows it).
    """
    canned = BrainTurnOutput(
        reasoning="Brain claims a knockout but the engine flagged none.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
        knockout_confirmed=True,
    )
    llm = _fake_llm(canned)
    config = _make_session_config()
    config.signal_metadata[0].knockout = True
    session_context = build_session_context(config)
    all_specs = [
        SignalSpec(
            signal="distributed_systems",
            signal_type=SignalType.competency,
            weight=3,
            priority=SignalPriority.required,
            knockout=True,
        ),
    ]
    cp = ControlPlane(
        session_context=session_context,
        system_prompt="You are a helpful interview brain.",
        projection=CoverageProjection(),
        resolver_questions=_make_resolver_questions(),
        all_specs=all_specs,
        budget_cfg=BudgetConfig(close_reserve_s=45.0, winding_down_s=90.0),
        knockout_tracker=None,
        llm_call=llm,
    )
    # No knockout flagged absent this turn.
    turn = _make_turn_input(knockout_pending=[])

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    # Close proceeds, but NO knockout is fabricated/recorded.
    assert decision.directive.is_terminal is True
    assert cp.confirmed_knockout_signals() == []


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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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

    decision = await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

    # Probe exhausted → falls back to ask
    assert decision.directive.act == DirectiveAct.ask
    # say should be the next unasked bank question text
    assert decision.directive.say == _Q2_TEXT


# ============================================================================
# Test 7: close → say None + is_terminal
# ============================================================================

@pytest.mark.asyncio
async def test_close_say_none_and_terminal():
    """move=close with no knockout_pending → directive.act==close, say is None, is_terminal True."""
    canned = BrainTurnOutput(
        reasoning="All done.",
        observations=[],
        move=BrainMove.close,
        probe_dimension=None,
        preferred_next_signal=None,
        composed_say=None,
    )
    llm = _fake_llm(canned)
    # No knockout specs → gate passes
    all_specs = [
        SignalSpec(signal="distributed_systems", signal_type=SignalType.competency, weight=3, priority=SignalPriority.required, knockout=False),
    ]
    cp = _make_control_plane(llm_call=llm, all_specs=all_specs)
    # No knockout_pending in turn input
    turn = _make_turn_input(knockout_pending=[])

    decision = await cp.decide(turn, asked_ids={"q-001", "q-002"}, time_remaining_s=600.0)

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

    await cp.decide(turn, asked_ids={"q-001"}, time_remaining_s=600.0)

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


async def test_knockout_flow_advances_and_terminates(_make_control_plane_with_knockout=None):
    """Regression (F3): repeated close-on-knockout must ADVANCE the tracker
    (probe → check_alternatives → reflect_confirm → confirmed) and finally let
    the close through — NOT loop the same knockout-probe forever. Also exposes
    confirmed_knockout_signals() for the driver to record the KnockoutOutcome."""
    from app.modules.interview_engine.brain.service import ControlPlane
    from app.modules.interview_engine.brain.input_builder import CoverageProjection
    from app.modules.interview_engine.contracts import (
        BrainSessionContext, SignalSpec, BrainTurnOutput, BrainMove, BrainTurnInput,
        ActiveQuestionRubric,
    )
    from app.modules.interview_runtime.evidence import SignalType, SignalPriority

    ko_signal = "Workato hands-on"
    ctx = BrainSessionContext(
        job_title="x", seniority_level="mid", role_summary="r", hiring_bar="b",
        signals=[SignalSpec(signal=ko_signal, signal_type=SignalType.experience,
                            weight=3, priority=SignalPriority.required, knockout=True)],
        bank_index=[],
    )

    async def _close_llm(messages):
        return BrainTurnOutput(reasoning="no exp", observations=[], move=BrainMove.close)

    cp = ControlPlane(
        session_context=ctx, system_prompt="sys", projection=CoverageProjection(),
        resolver_questions=[], all_specs=ctx.signals,
        budget_cfg=__import__("app.modules.interview_engine.brain.resolver", fromlist=["BudgetConfig"]).BudgetConfig(close_reserve_s=45, winding_down_s=90),
        llm_call=_close_llm,
    )

    rubric = ActiveQuestionRubric(question_id="q", text="t", excellent="e", meets_bar="m",
                                  below_bar="b", positive_evidence=["a","b","c"], red_flags=["x","y"],
                                  evaluation_hint="h", follow_ups=[])

    def _ti():
        from app.modules.interview_engine.contracts import BudgetPhase
        return BrainTurnInput(turn_ref="t", active_question=rubric, on_the_floor="?",
                              candidate_utterance="no", thread_turn_count=1,
                              evidence_so_far=[], transcript_window=[],
                              budget_phase=BudgetPhase.on_track,
                              uncovered_signals=[], knockout_pending=[ko_signal])

    # First three closes are BLOCKED + steered (non-terminal), advancing the tracker.
    acts = []
    for _ in range(3):
        d = await cp.decide(_ti(), asked_ids=set(), time_remaining_s=600)
        acts.append((d.directive.act.value, d.is_terminal))
    assert all(not term for _, term in acts), f"steps should be non-terminal: {acts}"
    assert all(a == "probe" for a, _ in acts), f"steps should steer via probe: {acts}"

    # The signal is now confirmed → exposed for the driver to record.
    assert cp.confirmed_knockout_signals() == [ko_signal]

    # The NEXT close is finally allowed through (terminal) — no infinite loop.
    d4 = await cp.decide(_ti(), asked_ids=set(), time_remaining_s=600)
    assert d4.directive.act.value == "close" and d4.is_terminal
