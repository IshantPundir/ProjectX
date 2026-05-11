"""Tests for the stale-turn drop-and-drain handler.

Diagnosed in session 0931c162: the framework's `on_user_turn_completed`
callback can fire with user text whose `stopped_speaking_at` is 8-25
seconds in the past while a more recent user turn is already queued.
Acting on stale fragments wastes Judge/Speaker effort, surfaces wrong
classifications, and leaves the candidate hearing replies to text they
no longer care about.

The drop-and-drain handler at the top of `on_user_turn_completed`
detects this case via two signals:

1. The new message's wall-clock staleness exceeds
   ``engine_stale_turn_threshold_ms``.
2. The candidate produced speech (observed via `audio.user.state`
   listening transitions) AFTER this turn's `stopped_speaking_at` —
   evidence that a fresher turn is queued.

When both signals fire, the orchestrator buffers the candidate text,
emits a ``turn.dropped`` audit event, and returns early. The
framework's `llm_node` is a no-op so no reply plays. The buffered
text drains into the next non-dropped turn's `candidate_text` BEFORE
the coalesce gate runs, so the State Engine still sees every word.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.interview_engine.orchestrator import InterviewOrchestrator


# Test helpers ---------------------------------------------------------------


def _make_orch_for_is_stale_turn(
    *,
    last_user_speech_end_wall: float | None,
    stale_threshold_ms: int = 8000,
) -> MagicMock:
    """Construct a MagicMock(spec=InterviewOrchestrator) with the
    minimum fields ``_is_stale_turn`` reads."""
    orch = MagicMock(spec=InterviewOrchestrator)
    orch._last_user_speech_end_wall = last_user_speech_end_wall
    orch._config = MagicMock(stale_turn_threshold_ms=stale_threshold_ms)
    return orch


class TestIsStaleTurn:
    """Decision logic for _is_stale_turn (pure method on orchestrator)."""

    def test_fresh_turn_is_not_stale(self):
        """Recent stopped_speaking_at → not stale (well under threshold)."""
        orch = _make_orch_for_is_stale_turn(last_user_speech_end_wall=100.5)
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=100.5,  # 500ms staleness
        )
        assert is_stale is False

    def test_stale_with_newer_speech_observed_is_stale(self):
        """stopped_speaking_at is 10s old AND _last_user_speech_end_wall
        is more recent → fresher turn was sealed but is queued behind →
        drop."""
        orch = _make_orch_for_is_stale_turn(
            last_user_speech_end_wall=109.0,  # silence onset 9s after the stale msg's stop
        )
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=110.0,  # 10s past stale-threshold of 8s
        )
        assert is_stale is True

    def test_stale_without_newer_speech_observed_is_not_stale(self):
        """High staleness alone is insufficient — without a more-recent
        silence onset there's no evidence a fresher turn is queued.
        Don't drop; let the orchestrator process it."""
        orch = _make_orch_for_is_stale_turn(
            last_user_speech_end_wall=95.0,  # silence BEFORE the stale msg's stop
        )
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=110.0,  # 10s old
        )
        assert is_stale is False

    def test_missing_stopped_speaking_at_is_not_stale(self):
        """LiveKit sometimes produces user transcripts without speaker
        timestamps. Can't decide staleness; play it safe and let it
        through. Better to over-process than to drop a legitimate turn."""
        orch = _make_orch_for_is_stale_turn(last_user_speech_end_wall=109.0)
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=None,
            now_wall=110.0,
        )
        assert is_stale is False

    def test_missing_last_user_speech_end_wall_is_not_stale(self):
        """No observed user_state transitions yet (session start). No
        evidence of more-recent speech → don't drop."""
        orch = _make_orch_for_is_stale_turn(last_user_speech_end_wall=None)
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=110.0,
        )
        assert is_stale is False

    def test_staleness_at_exactly_threshold_is_not_stale(self):
        """Boundary: staleness exactly at threshold doesn't drop.
        Strict `>` so the threshold is the highest non-stale value."""
        orch = _make_orch_for_is_stale_turn(last_user_speech_end_wall=108.0)
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=108.0,  # exactly 8000ms = threshold
        )
        assert is_stale is False

    def test_higher_threshold_changes_decision(self):
        """If config increases stale_turn_threshold_ms, what was stale
        before is now fresh. Sanity-check the config knob actually
        feeds into the decision."""
        orch = _make_orch_for_is_stale_turn(
            last_user_speech_end_wall=109.0,
            stale_threshold_ms=15000,  # tolerant 15s threshold
        )
        is_stale = InterviewOrchestrator._is_stale_turn(
            orch,
            stopped_speaking_at=100.0,
            now_wall=110.0,  # 10s — would be stale under 8s, fresh under 15s
        )
        assert is_stale is False


class TestObserveUserStateRecordsBothClocks:
    """observe_user_state must record both monotonic AND wall clock at
    each silence onset. The monotonic value powers the silence-aware
    coalescing window; the wall value powers staleness detection."""

    def test_listening_transition_records_both_timestamps(self):
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None
        orch._last_user_speech_end_wall = None

        InterviewOrchestrator.observe_user_state(
            orch,
            new_state="listening",
            now_monotonic=123.456,
            now_wall=1778500000.0,
        )
        assert orch._last_user_speech_end_monotonic == 123.456
        assert orch._last_user_speech_end_wall == 1778500000.0

    def test_speaking_transition_does_not_record_speech_end(self):
        """Speech-END timestamps (used by the silence-aware coalescing
        window and the at-callback staleness gate) must only update on
        speaking→listening transitions. A listening→speaking transition
        is NOT a silence onset."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None
        orch._last_user_speech_end_wall = None
        orch._resumed_speaking_at = None

        InterviewOrchestrator.observe_user_state(
            orch,
            new_state="speaking",
            now_monotonic=10.0,
            now_wall=1778500000.0,
        )
        assert orch._last_user_speech_end_monotonic is None
        assert orch._last_user_speech_end_wall is None


class TestObserveUserStateRecordsResumedSpeaking:
    """observe_user_state must record _resumed_speaking_at on
    listening→speaking transitions. Used by the post-Judge resumption
    gate to detect "user started speaking again while I was thinking."
    """

    def test_speaking_transition_records_resumed_speaking_wall(self):
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None
        orch._last_user_speech_end_wall = None
        orch._resumed_speaking_at = None

        InterviewOrchestrator.observe_user_state(
            orch,
            new_state="speaking",
            now_monotonic=10.0,
            now_wall=1778500000.0,
        )
        assert orch._resumed_speaking_at == 1778500000.0

    def test_listening_transition_does_not_change_resumed_speaking(self):
        """Resumed-speaking timestamp tracks the LATEST listening→speaking
        only. A subsequent speaking→listening event must not clear or
        update it; we want to remember "user spoke at time X" for as long
        as relevant."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None
        orch._last_user_speech_end_wall = None
        orch._resumed_speaking_at = 1778500000.0  # set by a prior speaking event

        InterviewOrchestrator.observe_user_state(
            orch,
            new_state="listening",
            now_monotonic=20.0,
            now_wall=1778500005.0,
        )
        assert orch._resumed_speaking_at == 1778500000.0

    def test_repeated_speaking_transitions_keep_latest(self):
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None
        orch._last_user_speech_end_wall = None
        orch._resumed_speaking_at = None

        for ts in [1778500001.0, 1778500005.0, 1778500009.0]:
            InterviewOrchestrator.observe_user_state(
                orch,
                new_state="speaking",
                now_monotonic=ts - 1778499000.0,
                now_wall=ts,
            )
        assert orch._resumed_speaking_at == 1778500009.0


class TestUserResumedSpeakingAfter:
    """Boundary tests for the predicate used by the post-Judge gate."""

    def _make_orch(self, *, resumed_at: float | None, epsilon_ms: int = 200) -> MagicMock:
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._resumed_speaking_at = resumed_at
        orch._config = MagicMock(post_judge_resumption_epsilon_ms=epsilon_ms)
        return orch

    def test_no_resumption_returns_false(self):
        orch = self._make_orch(resumed_at=None)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is False

    def test_resumption_after_threshold_returns_true(self):
        """Candidate resumed speaking 1.5s after the callback fired —
        that's well past epsilon, so the gate fires."""
        orch = self._make_orch(resumed_at=1001.5)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is True

    def test_resumption_before_callback_returns_false(self):
        """Resumed-speaking timestamp is OLDER than the callback fire —
        that resumption belonged to the prior turn; not relevant."""
        orch = self._make_orch(resumed_at=999.0)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is False

    def test_resumption_within_epsilon_returns_false(self):
        """A resumption 100ms after callback is within epsilon (200ms).
        Likely clock skew or tail of the very utterance we just received;
        don't treat as a new turn."""
        orch = self._make_orch(resumed_at=1000.1, epsilon_ms=200)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is False

    def test_resumption_at_exactly_epsilon_returns_false(self):
        """Boundary: at exactly epsilon, the strict-greater test fails →
        don't drop. Matches semantics of other gates in this module
        (window-expired, staleness threshold, etc.) which all use strict
        comparisons at the boundary."""
        orch = self._make_orch(resumed_at=1000.2, epsilon_ms=200)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is False

    def test_resumption_just_past_epsilon_returns_true(self):
        orch = self._make_orch(resumed_at=1000.201, epsilon_ms=200)
        assert InterviewOrchestrator._user_resumed_speaking_after(
            orch, t_wall=1000.0,
        ) is True


class TestStaleBufferDrain:
    """The drain logic: buffered stale texts are prepended to the next
    non-dropped turn's candidate_text BEFORE coalescing decisions run."""

    def test_drain_prepends_single_buffered_text(self):
        """One dropped turn buffered → next normal turn sees both."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = ["earlier dropped fragment."]
        orch._append = MagicMock()

        result = InterviewOrchestrator._drain_stale_buffer(
            orch,
            candidate_text="new turn text",
            current_turn_id="t-new",
        )
        assert result == "earlier dropped fragment. new turn text"
        # Buffer must be cleared so a third turn doesn't double-drain
        assert orch._stale_buffer == []

    def test_drain_preserves_order_for_multiple_dropped_turns(self):
        """If three turns are dropped back-to-back, drain order matches
        the order they were buffered (oldest first)."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = ["frag one.", "frag two.", "frag three."]
        orch._append = MagicMock()

        result = InterviewOrchestrator._drain_stale_buffer(
            orch,
            candidate_text="now",
            current_turn_id="t-now",
        )
        assert result == "frag one. frag two. frag three. now"
        assert orch._stale_buffer == []

    def test_drain_returns_text_unchanged_when_buffer_empty(self):
        """Common path: no drops in flight → buffer empty → return
        the new candidate_text untouched and emit nothing."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = []
        orch._append = MagicMock()

        result = InterviewOrchestrator._drain_stale_buffer(
            orch,
            candidate_text="just this",
            current_turn_id="t-x",
        )
        assert result == "just this"
        orch._append.assert_not_called()

    def test_drain_emits_audit_event_with_payload(self):
        """When drain happens, audit event records the buffered count
        and the texts that were drained so replay tools can see the
        merge sequence."""
        from app.modules.interview_engine.event_kinds import TURN_DRAIN_REPLAYED

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = ["a.", "b."]
        orch._append = MagicMock()

        InterviewOrchestrator._drain_stale_buffer(
            orch,
            candidate_text="c.",
            current_turn_id="turn-9",
        )
        orch._append.assert_called_once()
        kind_arg, payload_arg = orch._append.call_args.args
        assert kind_arg == TURN_DRAIN_REPLAYED
        assert payload_arg["current_turn_id"] == "turn-9"
        assert payload_arg["dropped_count"] == 2
        assert payload_arg["dropped_texts"] == ["a.", "b."]
        assert payload_arg["combined_text"] == "a. b. c."


class TestStaleBufferRespectsMax:
    """Defensive cap on buffer size prevents unbounded growth if the
    orchestrator stays behind for an extended period."""

    def test_buffer_drops_oldest_when_at_max(self):
        """When buffer hits stale_buffer_max, the new drop displaces
        the oldest entry. Prevents memory growth + keeps the drain
        rooted to the most recent fragments."""
        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = ["one.", "two.", "three.", "four.", "five.",
                              "six.", "seven.", "eight."]
        orch._config = MagicMock(stale_buffer_max=8)
        orch._append = MagicMock()

        InterviewOrchestrator._buffer_dropped_text(
            orch,
            candidate_text="nine.",
            turn_id="dropped-9",
            stopped_speaking_at=None,
            staleness_ms=10000,
        )
        # Buffer length is still 8, oldest is dropped.
        assert len(orch._stale_buffer) == 8
        assert "one." not in orch._stale_buffer
        assert orch._stale_buffer[-1] == "nine."


class TestDropAuditEvent:
    """The turn.dropped audit event must carry enough state for replay
    tooling to reconstruct what happened and why."""

    def test_buffer_drop_emits_turn_dropped_event(self):
        from app.modules.interview_engine.event_kinds import TURN_DROPPED

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._stale_buffer = []
        orch._config = MagicMock(stale_buffer_max=8)
        orch._append = MagicMock()

        InterviewOrchestrator._buffer_dropped_text(
            orch,
            candidate_text="stale fragment.",
            turn_id="dropped-x",
            stopped_speaking_at=100.0,
            staleness_ms=12345,
        )
        orch._append.assert_called_once()
        kind_arg, payload_arg = orch._append.call_args.args
        assert kind_arg == TURN_DROPPED
        assert payload_arg["turn_id"] == "dropped-x"
        assert payload_arg["candidate_text"] == "stale fragment."
        assert payload_arg["stopped_speaking_at"] == 100.0
        assert payload_arg["staleness_ms"] == 12345
        assert payload_arg["buffer_size_after"] == 1


class TestStaleDropEndToEnd:
    """Integration: on_user_turn_completed on a stale message buffers
    the text and returns without running Judge."""

    @pytest.mark.asyncio
    async def test_on_user_turn_completed_drops_stale_turn_and_buffers(self):
        """A stale message (10s past stop_speaking) with newer observed
        speech → orchestrator returns early, does not run Judge, and
        buffers the text for the next turn."""
        # We construct a real orchestrator with minimal dependencies and
        # exercise on_user_turn_completed. Asserting "Judge not called"
        # is the strongest behavioral check.
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, OrchestratorConfig,
        )
        from app.modules.interview_engine.state.lifecycle import (
            LifecycleSnapshot, LifecycleState,
        )

        # Minimal stubs
        state_engine = MagicMock()
        lifecycle = LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=900.0,
            time_elapsed_seconds=0.0,
            last_outcome=None,
        )
        state_engine.lifecycle_snapshot.return_value = lifecycle

        judge = MagicMock()
        judge.call = MagicMock()

        speaker = MagicMock()
        attr_pub = MagicMock()
        collector = MagicMock()
        from app.modules.interview_runtime.schemas import SessionConfig
        # Build a tiny SessionConfig is heavy — use MagicMock with the
        # attributes we touch.
        session_config = MagicMock()
        session_config.session_id = "00000000-0000-0000-0000-000000000000"
        session_config.stage.questions = []

        orch = InterviewOrchestrator(
            session_config=session_config,
            tenant_settings=MagicMock(),
            state_engine=state_engine,
            judge=judge,
            speaker=speaker,
            attr_publisher=attr_pub,
            event_collector=collector,
            correlation_id="cid",
            config=OrchestratorConfig(stale_turn_threshold_ms=8000),
            tenant_id="tid",
        )

        # Simulate observed user speech AFTER the stale message's stop
        import time as _time
        now_wall = _time.time()
        stale_stop = now_wall - 10.0  # 10 seconds ago
        recent_silence = now_wall - 0.5  # 500ms ago
        orch._last_user_speech_end_wall = recent_silence
        orch._last_user_speech_end_monotonic = _time.monotonic() - 0.5

        # Build a fake new_message
        new_message = MagicMock()
        new_message.text_content = "stale fragment text"
        new_message.metrics = MagicMock(stopped_speaking_at=stale_stop)

        agent = MagicMock()

        await orch.on_user_turn_completed(
            agent=agent, turn_ctx=MagicMock(), new_message=new_message,
        )

        # Judge MUST NOT have been called for the stale turn
        judge.call.assert_not_called()
        # Buffer holds the dropped text
        assert orch._stale_buffer == ["stale fragment text"]


class TestPostJudgeResumptionEndToEnd:
    """Integration: on_user_turn_completed runs Judge, but if the
    candidate started speaking again WHILE Judge was running, the
    orchestrator must drop the response (buffer text, do NOT invoke
    Speaker, do NOT mutate State Engine).

    Reproduces the session 3eabb4d0 race:
      callback fires at T0 (user just stopped, no resumption yet)
        → drop-and-drain at-callback gate does NOT fire (correct)
      → Judge runs (T0 → T0+2.7s)
      → user resumes speaking at T0+1.05s
      → Judge returns at T0+2.7s
      → orchestrator commits to a response → candidate is interrupted
    """

    @pytest.mark.asyncio
    async def test_post_judge_resumption_drops_response(self):
        import time as _time
        from unittest.mock import AsyncMock

        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, OrchestratorConfig,
        )
        from app.modules.interview_engine.state.lifecycle import (
            LifecycleSnapshot, LifecycleState,
        )

        state_engine = MagicMock()
        state_engine.lifecycle_snapshot.return_value = LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=900.0,
            time_elapsed_seconds=0.0,
            last_outcome=None,
        )
        # Make queue/ledger/claims snapshots benign — Judge input builder
        # touches them but we don't care about content here.
        state_engine.queue_snapshot.return_value = MagicMock(
            active_index=None, questions=[],
        )
        state_engine.ledger_snapshot.return_value = MagicMock()
        state_engine.claims_snapshot.return_value = MagicMock()
        state_engine.transcript_snapshot.return_value = []
        state_engine.next_pending_mandatory_id.return_value = None

        judge = MagicMock()
        speaker = MagicMock()
        speaker.stream = AsyncMock()  # MUST not be called
        attr_pub = MagicMock()
        collector = MagicMock()
        session_config = MagicMock()
        session_config.session_id = "00000000-0000-0000-0000-000000000000"
        session_config.stage.questions = []
        session_config.signal_metadata = []

        orch = InterviewOrchestrator(
            session_config=session_config,
            tenant_settings=MagicMock(),
            state_engine=state_engine,
            judge=judge,
            speaker=speaker,
            attr_publisher=attr_pub,
            event_collector=collector,
            correlation_id="cid",
            config=OrchestratorConfig(
                stale_turn_threshold_ms=8000,
                post_judge_resumption_epsilon_ms=200,
            ),
            tenant_id="tid",
        )

        # Configure Judge mock to "simulate" the candidate resuming
        # speech during the Judge call. The side-effect runs synchronously
        # inside the AsyncMock's await — by the time on_user_turn_completed
        # checks the post-Judge gate, _resumed_speaking_at is populated.
        callback_t0 = _time.time()

        def _judge_side_effect(*args, **kwargs):
            # Simulate user listening→speaking transition 1.0s after callback
            orch._resumed_speaking_at = callback_t0 + 1.0
            judge_output = MagicMock()
            judge_output.model_dump.return_value = {}
            res = MagicMock()
            res.is_fallback = False
            res.judge_output = judge_output
            res.model_used = "test-model"
            res.latency_ms = 100
            res.usage = None
            return res

        judge.call = AsyncMock(side_effect=_judge_side_effect)

        # Build a fresh (non-stale) new_message — drop-and-drain at-callback
        # gate must NOT fire so we reach the Judge call.
        new_message = MagicMock()
        new_message.text_content = "and it was a data dashboard working with IoT."
        new_message.metrics = MagicMock(stopped_speaking_at=callback_t0 - 0.5)

        # Fresh state: no observed resumption yet at top of callback.
        orch._resumed_speaking_at = None
        orch._last_user_speech_end_wall = callback_t0 - 0.5
        orch._last_user_speech_end_monotonic = _time.monotonic() - 0.5

        agent = MagicMock()

        await orch.on_user_turn_completed(
            agent=agent, turn_ctx=MagicMock(), new_message=new_message,
        )

        # Judge WAS called — the gate fires after Judge, not before.
        judge.call.assert_awaited_once()
        # State Engine mutations MUST NOT have happened — the candidate's
        # current speech might change the right classification.
        state_engine.process_judge_output.assert_not_called()
        # Speaker MUST NOT have been invoked — no audio reply played.
        speaker.stream.assert_not_called()
        # The dropped text is buffered for the next non-dropped turn.
        assert orch._stale_buffer == [
            "and it was a data dashboard working with IoT.",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("must_deliver_action", [
        "end_session",
        "polite_close",
        "acknowledge_no_experience",
        "repeat",
    ])
    async def test_must_deliver_actions_bypass_post_judge_gate(
        self, must_deliver_action,
    ):
        """The post-Judge resumption gate MUST NOT drop responses for
        actions that are explicit acknowledgements of candidate intent
        (or terminal States). Dropping these is catastrophic UX:

        - end_session: the candidate explicitly asked to end. Silence
          forces them to ask twice (session 11b3c321).
        - polite_close: engine-initiated terminal — already final.
        - acknowledge_no_experience: the candidate disclosed they don't
          know. Silence leaves them hanging.
        - repeat: the candidate asked to hear the question again.
          Silence is the worst response.

        For these, even if the candidate resumed speaking during Judge
        (e.g., re-asking because the first request wasn't acknowledged
        fast enough), we MUST deliver the response. The drop logic
        applies only to "we want more info from you" actions
        (probe, push_back, clarify, etc.).
        """
        import time as _time
        from unittest.mock import AsyncMock

        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, OrchestratorConfig,
        )
        from app.modules.interview_engine.state.lifecycle import (
            LifecycleSnapshot, LifecycleState,
        )
        from app.modules.interview_engine.models.speaker import InstructionKind

        state_engine = MagicMock()
        state_engine.lifecycle_snapshot.return_value = LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=900.0,
            time_elapsed_seconds=0.0,
            last_outcome=None,
        )
        state_engine.queue_snapshot.return_value = MagicMock(
            active_index=None, questions=[],
        )
        state_engine.ledger_snapshot.return_value = MagicMock()
        state_engine.claims_snapshot.return_value = MagicMock()
        state_engine.transcript_snapshot.return_value = []
        state_engine.next_pending_mandatory_id.return_value = None

        # Process Judge output produces a decision the orchestrator
        # then routes through Speaker. Map the must-deliver action to
        # a compatible InstructionKind so the orchestrator's branch
        # logic doesn't crash.
        action_to_kind = {
            "end_session": InstructionKind.polite_close,  # end_session routes through polite_close speech
            "polite_close": InstructionKind.polite_close,
            "acknowledge_no_experience": InstructionKind.acknowledge_no_experience,
            "repeat": InstructionKind.repeat,
        }
        decision = MagicMock()
        decision.speaker_input = MagicMock()
        decision.speaker_input.instruction_kind = action_to_kind[must_deliver_action]
        decision.cached_utterance = "cached question" if must_deliver_action == "repeat" else None
        decision.cached_source_turn_id = "src-turn" if must_deliver_action == "repeat" else None
        decision.validation_warnings = []
        state_engine.process_judge_output.return_value = decision
        state_engine.set_time_elapsed.return_value = None

        judge = MagicMock()
        speaker = MagicMock()
        attr_pub = MagicMock()
        collector = MagicMock()
        session_config = MagicMock()
        session_config.session_id = "00000000-0000-0000-0000-000000000000"
        session_config.stage.questions = []
        session_config.signal_metadata = []

        orch = InterviewOrchestrator(
            session_config=session_config,
            tenant_settings=MagicMock(),
            state_engine=state_engine,
            judge=judge,
            speaker=speaker,
            attr_publisher=attr_pub,
            event_collector=collector,
            correlation_id="cid",
            config=OrchestratorConfig(
                stale_turn_threshold_ms=8000,
                post_judge_resumption_epsilon_ms=200,
            ),
            tenant_id="tid",
        )

        # Judge mock returns the must-deliver action, AND simulates
        # the candidate resuming speech mid-call (which would normally
        # trigger the post-Judge gate to drop). The whitelist must
        # override the gate.
        callback_t0 = _time.time()
        from app.modules.interview_engine.models.judge import NextAction

        def _judge_side_effect(*args, **kwargs):
            orch._resumed_speaking_at = callback_t0 + 1.0  # well past epsilon
            judge_output = MagicMock()
            judge_output.next_action = NextAction(must_deliver_action)
            judge_output.model_dump.return_value = {"next_action": must_deliver_action}
            res = MagicMock()
            res.is_fallback = False
            res.judge_output = judge_output
            res.model_used = "test-model"
            res.latency_ms = 100
            res.usage = None
            return res

        judge.call = AsyncMock(side_effect=_judge_side_effect)

        # _stream_speaker_and_say is a benign no-op for the non-repeat
        # branches; the repeat branch uses agent.session.say directly.
        from app.modules.interview_engine.orchestrator import _SpeakerStreamOutcome
        orch._stream_speaker_and_say = AsyncMock(
            return_value=_SpeakerStreamOutcome(
                final_text="response", interrupted=False,
                sub_context="default", body_started_wall_at=None,
            )
        )
        orch._publish_attributes = AsyncMock()
        orch._schedule_shutdown = MagicMock()

        new_message = MagicMock()
        new_message.text_content = "test text"
        new_message.metrics = MagicMock(stopped_speaking_at=callback_t0 - 0.5)

        orch._resumed_speaking_at = None
        orch._last_user_speech_end_wall = callback_t0 - 0.5
        orch._last_user_speech_end_monotonic = _time.monotonic() - 0.5

        agent = MagicMock()
        agent.session.say = AsyncMock()

        await orch.on_user_turn_completed(
            agent=agent, turn_ctx=MagicMock(), new_message=new_message,
        )

        # CRITICAL: Judge was called AND State Engine processed → response
        # was NOT dropped despite the resumption.
        judge.call.assert_awaited_once()
        state_engine.process_judge_output.assert_called_once()
        # No entry in the stale buffer — the response went through, it
        # was not buffered for a future turn.
        assert orch._stale_buffer == []

    @pytest.mark.asyncio
    async def test_no_resumption_during_judge_proceeds_normally(self):
        """Negative control: same setup, but the Judge side-effect does
        NOT simulate any resumption. The orchestrator must proceed —
        State Engine called, Speaker invoked, no buffer growth."""
        import time as _time
        from unittest.mock import AsyncMock

        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, OrchestratorConfig,
        )
        from app.modules.interview_engine.state.lifecycle import (
            LifecycleSnapshot, LifecycleState,
        )

        state_engine = MagicMock()
        state_engine.lifecycle_snapshot.return_value = LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=900.0,
            time_elapsed_seconds=0.0,
            last_outcome=None,
        )
        state_engine.queue_snapshot.return_value = MagicMock(
            active_index=None, questions=[],
        )
        state_engine.ledger_snapshot.return_value = MagicMock()
        state_engine.claims_snapshot.return_value = MagicMock()
        state_engine.transcript_snapshot.return_value = []
        state_engine.next_pending_mandatory_id.return_value = None

        # process_judge_output returns a benign decision so the orchestrator
        # can proceed to Speaker. Set instruction_kind to something that
        # routes through _stream_speaker_and_say (NOT repeat).
        from app.modules.interview_engine.models.speaker import InstructionKind
        decision = MagicMock()
        decision.speaker_input = MagicMock()
        decision.speaker_input.instruction_kind = InstructionKind.deliver_probe
        decision.validation_warnings = []
        state_engine.process_judge_output.return_value = decision
        state_engine.set_time_elapsed.return_value = None
        state_engine.lifecycle_snapshot.return_value = LifecycleSnapshot(
            state=LifecycleState.active,
            time_budget_total_seconds=900.0,
            time_elapsed_seconds=0.0,
            last_outcome=None,
        )

        judge = MagicMock()
        speaker = MagicMock()
        attr_pub = MagicMock()
        collector = MagicMock()
        session_config = MagicMock()
        session_config.session_id = "00000000-0000-0000-0000-000000000000"
        session_config.stage.questions = []
        session_config.signal_metadata = []

        orch = InterviewOrchestrator(
            session_config=session_config,
            tenant_settings=MagicMock(),
            state_engine=state_engine,
            judge=judge,
            speaker=speaker,
            attr_publisher=attr_pub,
            event_collector=collector,
            correlation_id="cid",
            config=OrchestratorConfig(
                stale_turn_threshold_ms=8000,
                post_judge_resumption_epsilon_ms=200,
            ),
            tenant_id="tid",
        )

        # Judge returns a clean result. Side-effect does NOT touch
        # _resumed_speaking_at — the user is genuinely done.
        def _judge_side_effect(*args, **kwargs):
            judge_output = MagicMock()
            judge_output.model_dump.return_value = {}
            res = MagicMock()
            res.is_fallback = False
            res.judge_output = judge_output
            res.model_used = "test-model"
            res.latency_ms = 100
            res.usage = None
            return res

        judge.call = AsyncMock(side_effect=_judge_side_effect)

        # Make _stream_speaker_and_say a benign no-op AsyncMock so we can
        # assert it was called without driving the real Speaker LLM path.
        from app.modules.interview_engine.orchestrator import _SpeakerStreamOutcome
        orch._stream_speaker_and_say = AsyncMock(
            return_value=_SpeakerStreamOutcome(
                final_text="response", interrupted=False,
                sub_context="default", body_started_wall_at=None,
            )
        )
        orch._publish_attributes = AsyncMock()

        callback_t0 = _time.time()
        new_message = MagicMock()
        new_message.text_content = "real candidate answer."
        new_message.metrics = MagicMock(stopped_speaking_at=callback_t0 - 0.5)

        orch._resumed_speaking_at = None
        orch._last_user_speech_end_wall = callback_t0 - 0.5
        orch._last_user_speech_end_monotonic = _time.monotonic() - 0.5

        agent = MagicMock()

        await orch.on_user_turn_completed(
            agent=agent, turn_ctx=MagicMock(), new_message=new_message,
        )

        judge.call.assert_awaited_once()
        state_engine.process_judge_output.assert_called_once()
        orch._stream_speaker_and_say.assert_awaited_once()
        # No drops on the happy path
        assert orch._stale_buffer == []
