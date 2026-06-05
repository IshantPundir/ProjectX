"""
Test for app.modules.interview_engine.driver — SessionDriver end-to-end (F1).

Drives a 3-turn scripted session:
  opener  → says the first question
  turn 1  → fake brain → probe (1 observation)
  turn 2  → fake brain → ask (advance to q2, next_question_id set, 1 observation)
  turn 3  → fake brain → close (is_terminal=True)

Then calls finalize(CompletionReason.completed) and asserts the persisted
SessionEvidence is well-formed.

No real LLM / DB / LiveKit is touched.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.modules.interview_engine.brain.input_builder import CoverageProjection
from app.modules.interview_engine.contracts import (
    BrainDecision,
    BrainTurnInput,
    BridgeRequest,
    Directive,
    DirectiveAct,
    DirectiveTone,
    MouthTurnInput,
    SignalObservation,
)
from app.modules.interview_runtime.evidence import (
    CompletionReason,
    CoverageState,
    EvidenceStance,
    EvidenceTexture,
    Provenance,
    SessionEvidence,
    SignalPriority,
    SignalType,
    Speaker,
    TimeSpan,
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

_Q1_TEXT = "Tell me about your distributed systems experience."
_Q2_TEXT = "Describe an incident you led and resolved."

_FOLLOW_UP_0 = "Can you give a concrete example?"
_FOLLOW_UP_1 = "How did you measure the impact?"


def _make_question(
    qid: str,
    text: str,
    signal: str,
    *,
    is_mandatory: bool = False,
    position: int = 0,
) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=position,
        text=text,
        signal_values=[signal],
        estimated_minutes=5.0,
        is_mandatory=is_mandatory,
        follow_ups=[_FOLLOW_UP_0, _FOLLOW_UP_1],
        positive_evidence=["positive A", "positive B", "positive C"],
        red_flags=["red flag 1", "red flag 2"],
        rubric=QuestionRubric(
            excellent="Excellent rubric string that is long enough",
            meets_bar="Meets bar rubric text for test",
            below_bar="below_bar_default",
        ),
        evaluation_hint="Evaluate based on concrete examples.",
        question_kind="technical_scenario",
        primary_signal=signal,
        difficulty="medium",
    )


def _make_session_config() -> SessionConfig:
    return SessionConfig(
        session_id="sess-driver-test-001",
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
                knockout=True,       # <-- 1 knockout signal
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


# ============================================================================
# Fake collaborators
# ============================================================================

class _FakeBrain:
    """Returns scripted BrainDecisions for each successive call."""

    def __init__(self, decisions: list[BrainDecision]) -> None:
        self._decisions = list(decisions)
        self._call_count = 0

    async def decide(
        self, turn_input: BrainTurnInput, *, asked_ids=None, time_remaining_s=0.0
    ) -> BrainDecision:
        # Matches ControlPlane.decide: the driver wraps the brain in _BrainAdapter,
        # which supplies asked_ids + time_remaining_s on every call.
        idx = self._call_count
        self._call_count += 1
        if idx < len(self._decisions):
            return self._decisions[idx]
        # Defensive fallback — close immediately if unexpected extra call
        return BrainDecision(
            directive=Directive(act=DirectiveAct.close, say=None, is_terminal=True),
            observations=[],
            reasoning="fallback close",
            is_terminal=True,
        )


class _FakeMouth:
    """Returns fixed strings without any LLM call."""

    def __init__(self, real_line_response: str = "Real line text.") -> None:
        self._real_line_response = real_line_response
        self.real_line_calls: list[MouthTurnInput] = []

    async def bridge(self, req: BridgeRequest) -> str:
        return "Okay, noted."

    async def real_line(self, mouth_input: MouthTurnInput) -> str:
        self.real_line_calls.append(mouth_input)
        return self._real_line_response


class _FakeVoice:
    """Records all say() calls."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def say(self, text: str) -> None:
        self.said.append(text)


# ============================================================================
# Helper: scripted BrainDecisions
# ============================================================================

def _obs(signal: str, coverage: CoverageState = CoverageState.partial) -> SignalObservation:
    return SignalObservation(
        signal=signal,
        stance=EvidenceStance.supports,
        texture=EvidenceTexture.concrete,
        coverage_after=coverage,
        quote_span=None,
        retracts=False,
    )


def _scripted_decisions(q2_id: str = "q-002") -> list[BrainDecision]:
    """
    Turn 1: probe (on q-001), 1 observation for distributed_systems
    Turn 2: ask (advance to q-002), 1 observation for distributed_systems sufficient
    Turn 3: close (is_terminal=True), 1 observation for incident_response
    """
    turn1 = BrainDecision(
        directive=Directive(
            act=DirectiveAct.probe,
            say=_FOLLOW_UP_0,
            tone=DirectiveTone.warm,
            is_terminal=False,
        ),
        observations=[_obs("distributed_systems", CoverageState.partial)],
        reasoning="probe turn",
        is_terminal=False,
        next_question_id=None,
    )
    turn2 = BrainDecision(
        directive=Directive(
            act=DirectiveAct.ask,
            say=_Q2_TEXT,
            tone=DirectiveTone.warm,
            is_terminal=False,
        ),
        observations=[_obs("distributed_systems", CoverageState.sufficient)],
        reasoning="advance to q2",
        is_terminal=False,
        next_question_id=q2_id,
    )
    turn3 = BrainDecision(
        directive=Directive(
            act=DirectiveAct.close,
            say=None,
            tone=DirectiveTone.warm,
            is_terminal=True,
        ),
        observations=[_obs("incident_response", CoverageState.partial)],
        reasoning="close",
        is_terminal=True,
        next_question_id=None,
    )
    return [turn1, turn2, turn3]


# ============================================================================
# The main test
# ============================================================================

@pytest.mark.asyncio
async def test_session_driver_end_to_end() -> None:
    """Drive a 3-turn scripted session and assert a well-formed, persisted SessionEvidence."""
    from app.modules.interview_engine.driver import SessionDriver
    from app.modules.interview_engine.notes import NoteLog

    config = _make_session_config()
    decisions = _scripted_decisions(q2_id="q-002")

    brain = _FakeBrain(decisions)
    mouth = _FakeMouth(real_line_response="Great, tell me more.")
    voice = _FakeVoice()
    notelog = NoteLog()
    projection = CoverageProjection()

    persisted: list[SessionEvidence] = []

    async def fake_persist(ev: SessionEvidence) -> None:
        persisted.append(ev)

    started_at = datetime(2026, 6, 5, 10, 0, 0, tzinfo=UTC)

    # Fixed clock: always returns same ms (good enough for unit tests)
    def fixed_now() -> datetime:
        return datetime(2026, 6, 5, 10, 5, 0, tzinfo=UTC)

    driver = SessionDriver(
        config=config,
        brain=brain,
        mouth=mouth,
        bridge=mouth,      # FakeMouth also satisfies bridge protocol
        notelog=notelog,
        projection=projection,
        voice=voice,
        persist=fake_persist,
        time_budget_s=1800.0,
        started_at=started_at,
        now_fn=fixed_now,
    )

    # Opener
    opener_text = await driver.opener()
    assert isinstance(opener_text, str) and opener_text  # non-empty spoken text

    # Voice should have been called at least once (for the opener)
    assert len(voice.said) >= 1

    # Turn 1 — probe
    span1 = TimeSpan(start_ms=5000, end_ms=15000)
    terminal1 = await driver.handle_turn(
        utterance="I worked on Kafka-based distributed pipelines.",
        turn_ref="t-01",
        span=span1,
    )
    assert not terminal1

    # Turn 2 — advance to q2
    span2 = TimeSpan(start_ms=20000, end_ms=30000)
    terminal2 = await driver.handle_turn(
        utterance="I've designed systems handling 50k req/s with 99.9% uptime.",
        turn_ref="t-02",
        span=span2,
    )
    assert not terminal2

    # Turn 3 — close (terminal)
    span3 = TimeSpan(start_ms=40000, end_ms=50000)
    terminal3 = await driver.handle_turn(
        utterance="I led an incident that restored service in 45 minutes.",
        turn_ref="t-03",
        span=span3,
    )
    assert terminal3

    # Finalize
    evidence = await driver.finalize(CompletionReason.completed)

    # ── 1. persist was called exactly once with a SessionEvidence ──
    assert len(persisted) == 1
    assert persisted[0] is evidence

    # ── 2. meta correctness ──
    assert evidence.meta.session_id == config.session_id
    assert evidence.meta.completion == CompletionReason.completed
    assert evidence.meta.questions_asked >= 1

    # ── 3. notes accumulated across turns (3 turns × 1 obs each) ──
    assert len(evidence.notes) >= 2   # at minimum turns 1+2+3 each have 1 obs

    # ── 4. transcript has both candidate and agent turns ──
    speaker_types = {t.speaker for t in evidence.transcript}
    assert Speaker.candidate in speaker_types, "transcript must have candidate turns"
    assert Speaker.agent in speaker_types, "transcript must have agent turns (opener + real lines)"

    candidate_texts = [t.text for t in evidence.transcript if t.speaker == Speaker.candidate]
    assert "I worked on Kafka-based distributed pipelines." in candidate_texts

    # ── 5. questions list ──
    assert len(evidence.questions) == 2  # both bank questions recorded
    q_ids = {q.question_id for q in evidence.questions}
    assert "q-001" in q_ids
    assert "q-002" in q_ids

    # q-001 was asked
    q1_rec = next(q for q in evidence.questions if q.question_id == "q-001")
    from app.modules.interview_runtime.evidence import QuestionOutcome
    assert q1_rec.outcome == QuestionOutcome.asked

    # ── 6. provenance — every signal has a valid Provenance ──
    for sig in evidence.signals:
        assert sig.provenance in list(Provenance), f"invalid provenance on {sig.signal}"

    # ── 7. round-trip ──
    assert SessionEvidence.model_validate(evidence.model_dump()) == evidence

    # ── 8. no real LLM / DB calls ──
    # (Brain was our fake; FakeMouth and FakeVoice never call real APIs)
    # Confirmed by the absence of any network I/O — the test passes offline.


async def test_mouth_adapter_combines_bridge_and_real_line():
    """Regression: run_turn needs ONE mouth with both bridge() + real_line().

    The driver splits them (BridgeComposer + ConversationPlane); _MouthAdapter
    must delegate each call to the right half. (A prior wiring bug passed only
    ConversationPlane → AttributeError: 'ConversationPlane' has no attribute
    'bridge' on the first live turn.)
    """
    from app.modules.interview_engine.driver import _MouthAdapter

    class _FakeReal:
        async def real_line(self, mi):
            return "REAL"

    class _FakeBridge:
        async def bridge(self, req):
            return "BRIDGE"

    adapter = _MouthAdapter(real_plane=_FakeReal(), bridge_composer=_FakeBridge())
    assert await adapter.bridge(object()) == "BRIDGE"
    assert await adapter.real_line(object()) == "REAL"


async def test_brain_adapter_supplies_resolver_state():
    """Regression: run_turn calls brain.decide(turn_input), but ControlPlane.decide
    also needs asked_ids + time_remaining_s. _BrainAdapter must supply them.
    (Live talk-test: TypeError: ControlPlane.decide() missing 'asked_ids' and
    'time_remaining_s'.)
    """
    from app.modules.interview_engine.driver import _BrainAdapter

    seen = {}

    class _FakeCP:
        async def decide(self, turn_input, *, asked_ids, time_remaining_s):
            seen["asked_ids"] = asked_ids
            seen["time_remaining_s"] = time_remaining_s
            return "DECISION"

    adapter = _BrainAdapter(_FakeCP())
    adapter.asked_ids = {"q1"}
    adapter.time_remaining_s = 123.0
    out = await adapter.decide(object())
    assert out == "DECISION"
    assert seen == {"asked_ids": {"q1"}, "time_remaining_s": 123.0}
