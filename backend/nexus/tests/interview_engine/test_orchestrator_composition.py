"""Composition test: real Orchestrator + real StateEngine + mocked Judge / Speaker.

Per the user's testing memory ("composition tests catch wrap-bugs;
parent+child rendered together; mock at API boundary"), this test wires
up the orchestrator with the real state engine and feeds it scripted
Judge / Speaker outputs across a multi-turn session.

Asserts (end-to-end, all four bugs from session 8317142f-... in one flow):

- Bug A (redirect on social/greeting input): turn 1 ("Hi") and turn 2
  ("How are you?") flow through the new ``redirect`` action and never
  burn the candidate's first answer attempt on q1.
- Bug B (repeat-cache filter): on turn 3 ("Can you repeat?") the State
  Engine replays the question delivered on turn 0, NOT turn 1's redirect
  utterance — even though redirects are the most recent agent turn,
  ``register_agent_utterance`` filters non-question kinds out of the
  repeat cache.
- Bug C (->failed semantic guard): turn 4 includes a bogus observation
  with ``coverage_transition=none→failed`` AND ``anchor_id=0`` (a
  positive anchor). The State Engine's guard at engine.py:171 drops it
  with an ``illegal_failure_observation`` warning and the lifecycle's
  ``knockout_failures`` list stays empty — even though the signal is
  marked ``knockout=True``. Without the guard this would record a
  knockout and (under ``record_only`` policy) still pollute the audit
  trail; under ``close_polite`` it would close the session.
- Bug D (empty Speaker output fallback): turn 2's mocked SpeakerHandle
  returns ``""``. The orchestrator emits a ``speaker.output.empty``
  audit event and plays a deterministic fallback through ``session.say``.

Negative control (Bug C): comment out the
``if transition.endswith("→failed") and obs.anchor_id != -1`` guard in
``app/modules/interview_engine/state/engine.py`` (around line 171) and
re-run this test. Expected failures:

  - ``state.lifecycle_snapshot().knockout_failures == []`` fails because
    the bogus observation reaches ``apply_observation``, lands the signal
    in ``failed``, and the knockout-detection pass below records a
    ``KnockoutFailure``.
  - The ``illegal_failure_observation`` JUDGE_VALIDATION event is also
    absent (the guard is what emits that warning).

Both assertions are load-bearing: removing the guard breaks both.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.event_kinds import (
    JUDGE_VALIDATION, SPEAKER_CACHED, SPEAKER_OUTPUT_EMPTY,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.models.judge import (
    CoverageTransition, JudgeOutput, NextAction, Observation, ProbePayload,
    RedirectPayload, RepeatPayload, TurnMetadata,
)
from app.modules.interview_engine.openers import OpenerLibrary
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator, OrchestratorConfig,
)
from app.modules.interview_engine.state.engine import StateEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collector() -> EventCollector:
    return EventCollector(
        session_id="s", tenant_id="t", correlation_id="c",
        controller_prompt_hash="sha256:ctrl",
        model_versions={"judge": "m1", "speaker": "m1"},
        redaction_mode="metadata",
        task_prompt_hashes={"judge": "sha256:j", "speaker": "sha256:s"},
    )


class _ScriptedSpeakerHandle:
    """Mocked SpeakerStreamHandle. ``text=""`` simulates the empty-output
    bug (Bug D) — ``stream()`` yields nothing and ``final_text()`` returns
    the empty string."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.usage = {"prompt_tokens": 5, "completion_tokens": 5}
        self.latency_ms_first_token = 100
        self.latency_ms_total = 250
        self.prompt_hash = "sha256:" + ("0" * 64)
        # Phase 9.3 diagnostic fields. Real SpeakerStreamHandle exposes
        # these and the orchestrator's speaker.output.empty path reads
        # them; without them the test would hit AttributeError and route
        # into the speaker-error recovery branch instead.
        self.event_types_seen: list[str] = []
        self.refusal_text: str | None = None
        self.response_id: str | None = None
        self.finish_reason: str | None = None

    def stream(self):
        text = self._text

        async def gen():
            if text:
                yield text
            return  # pragma: no cover

        return gen()

    async def final_text(self) -> str:
        return self._text


def _msg(text: str):
    """Build a LiveKit ChatMessage with a candidate utterance."""
    from livekit.agents.llm import ChatMessage
    return ChatMessage(role="user", content=[text])


def _judge_result(judge_output: JudgeOutput) -> Any:
    """Wrap a JudgeOutput in a JudgeCallResult-shaped MagicMock."""
    return MagicMock(
        judge_output=judge_output,
        is_fallback=False,
        fallback_reason=None,
        original_failure_context=None,
        latency_ms=10,
        usage={"prompt_tokens": 8, "completion_tokens": 4},
        model_used="gpt-test",
    )


def _build_orch(
    *,
    make_session_config,
    make_question,
    scripted_judge_outputs: list[JudgeOutput],
    scripted_speaker_outputs: list[str],
    knockout_signal: str,
) -> tuple[InterviewOrchestrator, Any]:
    """Build a real InterviewOrchestrator + real StateEngine wired up with
    mocked Judge / Speaker. Returns (orchestrator, agent).

    The session config has one knockout signal so the bogus
    ``→failed``/``anchor_id=0`` observation in turn 4 is actually
    load-bearing — without ``knockout=True`` on the targeted signal, the
    State Engine guard's drop wouldn't change the test outcome (no
    knockout recorded either way).
    """
    cfg = make_session_config(
        questions=[
            make_question(
                qid="q1", position=0, mandatory=True,
                text="Walk me through how you'd design a Jira workflow.",
                signal_values=[knockout_signal],
                follow_ups=["On those validators — what does the user see?"],
            ),
        ],
        signals=[knockout_signal],
        knockout_signal=knockout_signal,
    )

    state_engine = StateEngine(session_config=cfg)
    collector = _collector()

    judge_service = MagicMock()
    judge_service.call = AsyncMock(side_effect=[
        _judge_result(jo) for jo in scripted_judge_outputs
    ])

    speaker_service = MagicMock()
    speaker_service.stream = AsyncMock(side_effect=[
        _ScriptedSpeakerHandle(text) for text in scripted_speaker_outputs
    ])

    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    pub = AttributePublisher(room=room)

    fake_session = MagicMock()
    # Phase 9.4: orchestrator now reads SpeechHandle.interrupted from
    # session.say's return value. Default to interrupted=False so the
    # composition test's empty-output assertions exercise the true-empty
    # fallback path (rather than the new SPEAKER_INTERRUPTED path).
    fake_session.say = AsyncMock(return_value=MagicMock(interrupted=False))
    fake_session.shutdown = MagicMock()
    fake_agent = MagicMock()
    fake_agent.session = fake_session

    orch = InterviewOrchestrator(
        session_config=cfg,
        tenant_settings=MagicMock(engine_agent_name=None),
        state_engine=state_engine,
        judge=judge_service,
        speaker=speaker_service,
        attr_publisher=pub,
        event_collector=collector,
        correlation_id="c",
        config=OrchestratorConfig(),
        tenant_id="t",
        opener_library=OpenerLibrary(),
    )
    return orch, fake_agent


# ---------------------------------------------------------------------------
# The composition test
# ---------------------------------------------------------------------------


KNOCKOUT_SIGNAL = "jira_admin"


@pytest.mark.asyncio
async def test_full_session_no_false_knockout_no_silence_correct_repeat(
    make_session_config, make_question,
):
    """End-to-end composition. Walks through the multi-turn script that
    reproduces all four bugs from session 8317142f and asserts every
    new guard does its job.

    Per the design note in Task 15 of the redesign plan, the bogus
    ->failed observation is constructed with a SINGLE
    ``coverage_transition=none→failed`` + ``anchor_id=0`` on a fresh
    signal (state=none, no prior observations on q1). Construction
    rationale:

    - The signal starts at ``none`` (no prior observations). A
      ``none→failed`` transition with ``anchor_id=-1`` would be legal
      — both the LHS-state check (``none``) and the sentinel-anchor
      check (``-1``) would succeed.
    - Replacing ``anchor_id=-1`` with ``anchor_id=0`` is what makes
      the observation bogus: a positive anchor with a failure transition
      is the exact prompt-drift pattern from session 8317142f turn 7
      where the Judge mis-classified a strong answer span as a failure.
    - This shape activates the `transition.endswith("→failed") and
      obs.anchor_id != -1` guard (engine.py:171) WITHOUT first tripping
      the LHS-state mismatch (which would emit
      ``illegal_coverage_transition`` instead — same blocking effect,
      different audit code, weaker signal that we caught the right bug).
    """
    judge_outputs = [
        # Turn 1: candidate says "Hi" → redirect (social).
        JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.redirect,
            next_action_payload=RedirectPayload(),
            turn_metadata=TurnMetadata(candidate_social_or_greeting=True),
        ),
        # Turn 2: "How are you?" → redirect (Speaker output empty — Bug D).
        JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.redirect,
            next_action_payload=RedirectPayload(),
            turn_metadata=TurnMetadata(candidate_social_or_greeting=True),
        ),
        # Turn 3: "Can you repeat?" → repeat (cached delivery, no Speaker call).
        JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.repeat,
            next_action_payload=RepeatPayload(),
            turn_metadata=TurnMetadata(),
        ),
        # Turn 4: strong answer with bogus failure observation — Bug C
        # simulation. The ->failed guard MUST drop this; the lifecycle
        # MUST NOT record a knockout. The probe action proceeds normally.
        JudgeOutput(
            observations=[
                Observation(
                    signal_value=KNOCKOUT_SIGNAL,
                    anchor_id=0,                                       # BOGUS
                    evidence_quote="conditions and post-functions",
                    coverage_transition=CoverageTransition.none_to_failed,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=ProbePayload(
                probe_id="0",
            ),
            turn_metadata=TurnMetadata(),
        ),
    ]

    speaker_outputs = [
        # on_enter (turn 0): first question delivered.
        "Hey Ishant, good to meet you. Whenever you're ready, walk me through Jira.",
        # Turn 1: redirect → some short utterance.
        "Cool — let's jump in.",
        # Turn 2: redirect → EMPTY (Bug D: simulates the Speaker LLM
        # streaming nothing audible). The orchestrator must fall back.
        "",
        # Turn 3 is `repeat` → cached delivery; no Speaker call; no entry.
        # Turn 4: probe utterance.
        "On those validators — what does the user see when one fails?",
    ]

    orch, agent = _build_orch(
        make_session_config=make_session_config,
        make_question=make_question,
        scripted_judge_outputs=judge_outputs,
        scripted_speaker_outputs=speaker_outputs,
        knockout_signal=KNOCKOUT_SIGNAL,
    )

    # Drive the conversation: on_enter then four candidate turns.
    await orch.on_enter(agent)
    await orch.on_user_turn_completed(agent, MagicMock(), _msg("Hi"))
    await orch.on_user_turn_completed(agent, MagicMock(), _msg("How are you?"))
    await orch.on_user_turn_completed(agent, MagicMock(), _msg("Can you repeat?"))
    await orch.on_user_turn_completed(
        agent, MagicMock(),
        _msg(
            "I use validators to enforce required actions; "
            "conditions and post-functions for automation."
        ),
    )

    state = orch._state

    # ----- Bug C — no spurious knockout -------------------------------------
    # The bogus turn-4 observation must be dropped by the State Engine
    # guard. Lifecycle must remain active; knockout_failures must be empty
    # even though the signal is knockout=True.
    lifecycle = state.lifecycle_snapshot()
    assert lifecycle.knockout_failures == [], (
        "->failed guard regressed: a positive-anchor failure observation "
        "must NOT record a knockout."
    )
    assert lifecycle.state.value == "active", (
        "Session must stay active when the false knockout is suppressed."
    )

    # The illegal_failure_observation warning was emitted (Judge validation
    # event of code illegal_failure_observation must appear at least once).
    judge_validations = [
        e for e in orch._collector.events if e.kind == JUDGE_VALIDATION
    ]
    codes = [v.payload["code"] for v in judge_validations]
    assert "illegal_failure_observation" in codes, (
        "->failed guard must emit the illegal_failure_observation warning "
        "for audit visibility."
    )

    # ----- Bug D — empty Speaker output fallback ----------------------------
    # Turn 2's empty Speaker output must trigger speaker.output.empty +
    # a deterministic fallback played through agent.session.say.
    speaker_empty_events = [
        e for e in orch._collector.events if e.kind == SPEAKER_OUTPUT_EMPTY
    ]
    assert len(speaker_empty_events) == 1, (
        "Exactly one empty-Speaker-output event expected (turn 2)."
    )
    fallback = speaker_empty_events[0].payload["fallback_text"]
    # The fallback for redirect (no bank_text) is the generic "take it
    # from the top" line; for deliver_question it is "Let me restate that.
    # <bank_text>". Either substring confirms the fallback fired.
    assert "restate" in fallback.lower() or "top" in fallback.lower(), (
        f"Fallback text doesn't match the expected templates: {fallback!r}"
    )

    # ----- Bug B — repeat replays turn 0's question, not turn 1's redirect -
    # The transcript records candidate utterances + agent utterances written
    # by `register_agent_utterance` (called from the speaker stream paths).
    # The repeat path bypasses Speaker entirely and writes a SPEAKER_CACHED
    # audit event whose ``final_utterance`` IS the replayed question text
    # pulled from the State Engine's ``_question_utterances`` cache.
    speaker_cached_events = [
        e for e in orch._collector.events if e.kind == SPEAKER_CACHED
    ]
    assert len(speaker_cached_events) == 1, (
        "Exactly one cached-Speaker delivery expected (turn 3 repeat)."
    )
    cached_payload = speaker_cached_events[0].payload
    assert cached_payload["instruction_kind"] == "repeat"
    # The replayed text must be the ORIGINAL turn-0 question delivery,
    # NOT turn 1's redirect utterance ("Cool — let's jump in.") which
    # was the most recent agent utterance before the repeat. Without
    # the Bug B fix, ``_resolve_repeat`` would return the redirect
    # because it walked the unfiltered transcript.
    assert "Hey Ishant" in cached_payload["final_utterance"], (
        f"Repeat must replay the original question; got "
        f"{cached_payload['final_utterance']!r}"
    )
    assert "Cool" not in cached_payload["final_utterance"], (
        "Repeat must NOT replay turn 1's redirect utterance."
    )

    # The first-question agent utterance must also survive in the
    # transcript (sanity check: the State Engine wrote it on turn 0).
    transcript = state.transcript_snapshot()
    agent_texts = [t.text for t in transcript if t.role == "agent"]
    assert any("Hey Ishant" in t for t in agent_texts), (
        "First-question agent utterance must be reachable in the transcript."
    )

    # ----- Bug A — redirects don't burn q1 ---------------------------------
    # After two redirects, the active question must still be q1 (no
    # advance happened on social/greeting turns).
    queue = state.queue_snapshot()
    assert queue.active_index == 0, (
        f"redirect actions must NOT advance the queue (active_index={queue.active_index})"
    )

    # ----- Speaker mock call accounting -----------------------------------
    # Speaker LLM invoked exactly 4 times: on_enter (turn 0),
    # turn 1 redirect, turn 2 redirect (empty), turn 4 probe. Turn 3
    # repeat is cached and bypasses the Speaker entirely.
    assert orch._speaker.stream.await_count == 4, (
        f"Speaker call count mismatch (expected 4, got "
        f"{orch._speaker.stream.await_count}); turn 3 (repeat) should be "
        f"cached delivery."
    )

    # Judge invoked exactly 4 times — once per candidate turn (on_enter
    # uses a synthetic JudgeOutput, no LLM call).
    assert orch._judge.call.await_count == 4, (
        f"Judge call count mismatch (expected 4, got {orch._judge.call.await_count})"
    )


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back end-to-end composition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_back_flows_end_to_end_and_increments_count_in_judge_input(
    make_session_config, make_question,
):
    """End-to-end: candidate gives a thin answer, Judge emits push_back,
    Speaker is invoked with InstructionKind.push_back + reason_code,
    push_back_count is incremented on the active question, and the NEXT
    Judge call sees active_question_push_back_count=1 in its input.

    This is the regression guard for the bug from session 4cf43291: a
    candidate gave thin answers turn after turn, and the Judge advanced
    instead of pushing back.
    """
    from app.modules.interview_engine.models.judge import (
        CoverageQuality, PushBackPayload,
    )

    judge_outputs = [
        # Turn 1: candidate says "I would add validation checks" (vague).
        # Judge emits push_back vague_answer with one thin observation.
        JudgeOutput(
            observations=[
                Observation(
                    signal_value="S1", anchor_id=0,
                    evidence_quote="I would add validation checks",
                    coverage_transition=CoverageTransition.partial_to_partial,
                    quality=CoverageQuality.thin,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(),
        ),
        # Turn 2: candidate gives a concrete follow-up. Judge probes
        # for one more detail with a concrete observation. (Probe rather
        # than advance keeps the assertion focused on push_back_count
        # threading; the state engine's advance-quality-gate has its own
        # dedicated tests in test_engine.py.)
        JudgeOutput(
            observations=[
                Observation(
                    signal_value="S1", anchor_id=2,
                    evidence_quote="I'd add a workflow validator that checks "
                                   "the linked PR status",
                    coverage_transition=CoverageTransition.partial_to_sufficient,
                    quality=CoverageQuality.concrete,
                ),
            ],
            candidate_claims=[],
            next_action=NextAction.probe,
            next_action_payload=ProbePayload(probe_id="0"),
            turn_metadata=TurnMetadata(),
        ),
    ]

    speaker_outputs = [
        "First question delivered to the candidate.",  # on_enter
        "OK — walk me through one validation check you'd actually write.",  # turn 1 push_back
        "And how does it handle PR-system timeouts?",  # turn 2 probe
    ]

    orch, agent = _build_orch(
        make_session_config=make_session_config,
        make_question=make_question,
        scripted_judge_outputs=judge_outputs,
        scripted_speaker_outputs=speaker_outputs,
        knockout_signal="never_used",
    )

    await orch.on_enter(agent)
    # Turn 1: thin answer triggers push_back.
    await orch.on_user_turn_completed(
        agent, MagicMock(), _msg("I would add, like, validation checks"),
    )
    # Turn 2: concrete follow-up.
    await orch.on_user_turn_completed(
        agent, MagicMock(),
        _msg("I'd add a workflow validator that checks the linked PR status"),
    )

    state = orch._state

    # ----- push_back_count must be 1 after turn 1 ---------------------------
    snap = state.queue_snapshot()
    assert snap.questions[0].push_back_count == 1, (
        "push_back action must increment push_back_count on the active question"
    )

    # ----- Speaker was invoked with InstructionKind.push_back on turn 1 -----
    # speaker.stream calls: on_enter (#0) + turn 1 (#1) + turn 2 (#2)
    speaker_calls = orch._speaker.stream.call_args_list
    assert len(speaker_calls) == 3
    turn1_speaker_input = speaker_calls[1].kwargs["speaker_input"]
    from app.modules.interview_engine.models.speaker import InstructionKind
    assert turn1_speaker_input.instruction_kind == InstructionKind.push_back
    assert turn1_speaker_input.push_back_reason_code == "vague_answer"

    # ----- Turn 2 Judge input carries push_back_count=1 ---------------------
    # The orchestrator reads queue.questions[active_index].push_back_count
    # off the CURRENT snapshot at Judge-call time. After turn 1 the count
    # is 1; the turn-2 Judge call must reflect that.
    judge_call_args = orch._judge.call.call_args_list
    assert len(judge_call_args) == 2
    turn2_judge_input = judge_call_args[1].kwargs["input_payload"]
    assert turn2_judge_input.active_question_push_back_count == 1, (
        "Orchestrator must thread push_back_count from the queue into the "
        "next Judge input so the prompt's cap=2 rule has accurate data."
    )
