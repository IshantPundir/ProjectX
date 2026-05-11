"""Tests for Continuation Coalescing decision + integration.

Spec: docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.orchestrator import (
    _CoalesceDecision,
    _PriorTurnSnapshot,
    _should_coalesce,
)


def _snapshot(
    *,
    completed_monotonic: float = 1000.0,
    speaker_emitted_content: bool = False,
    instruction_kind: str = "push_back",
    sub_context: str = "missing_specifics",
    body_started_wall_at: float | None = None,
) -> _PriorTurnSnapshot:
    return _PriorTurnSnapshot(
        turn_id="prior-1",
        completed_monotonic=completed_monotonic,
        candidate_text="First one, like, I would communicate with the client.",
        instruction_kind=instruction_kind,
        sub_context=sub_context,
        speaker_emitted_content=speaker_emitted_content,
        body_started_wall_at=body_started_wall_at,
    )


class TestShouldCoalesce:
    def test_no_prior_turn_returns_false(self):
        decision = _should_coalesce(
            prior=None,
            now_monotonic=1000.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "no_prior_turn"

    def test_disabled_returns_false_even_when_otherwise_eligible(self):
        decision = _should_coalesce(
            prior=_snapshot(),
            now_monotonic=1000.1,
            coalesce_enabled=False,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "disabled"

    def test_prior_speaker_delivered_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(speaker_emitted_content=True),
            now_monotonic=1000.1,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_gap_exceeds_window_returns_false(self):
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1006.0,  # 6000ms after — beyond 5000ms window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_gap_exactly_at_window_returns_false(self):
        """Boundary: gap_ms == coalesce_window_ms is NOT coalesced (strict <)."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0),
            now_monotonic=1005.0,  # exactly 5000ms after
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("redirect", "off_topic"),
        ("redirect", "abusive"),
        ("redirect", "injection"),
        ("polite_close", "default"),
        ("polite_close", "knockout"),
        ("repeat", "default"),
        ("deliver_first_question", "default"),
    ])
    def test_non_coalescible_kinds_return_false(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(instruction_kind=kind, sub_context=sub_ctx),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is False
        assert decision.reason == "kind_not_coalescible"

    @pytest.mark.parametrize("kind,sub_ctx", [
        ("push_back", "vague_answer"),
        ("push_back", "deflection"),
        ("push_back", "missing_specifics"),
        ("push_back", "unanswered_subquestion"),
        ("clarify", "default"),
        ("deliver_question", "default"),
        ("deliver_question", "post_cap_advance"),
        ("deliver_probe", "default"),
        ("acknowledge_no_experience", "default"),
        ("redirect", "social_or_greeting"),
    ])
    def test_coalescible_kinds_with_undelivered_speaker_return_true(self, kind, sub_ctx):
        decision = _should_coalesce(
            prior=_snapshot(
                instruction_kind=kind,
                sub_context=sub_ctx,
                speaker_emitted_content=False,
            ),
            now_monotonic=1000.5,  # 500ms — well within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
        )
        assert decision.should is True
        assert decision.reason == "coalesced"


class TestShouldCoalescePreBodyGate:
    """Tests for the second coalescing gate: even when the prior Speaker
    delivered its body, coalesce when the candidate's new utterance stopped
    BEFORE the body's audio playback began — they could not have been
    responding to a body they hadn't heard yet.

    Root cause covered by these tests: session 3a8ebdaa, turn 5 → turn 6.
    The framework queued a second STT final ("the question that you were
    asking me.") while the orchestrator was running turn 5; the existing
    speaker_emitted_content=True gate excluded coalescing even though the
    candidate stopped speaking BEFORE the body audio started.
    """

    def test_coalesces_when_speaker_delivered_but_user_stopped_before_body_started(self):
        decision = _should_coalesce(
            prior=_snapshot(
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1000.5,  # gap = 500ms, within window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=1999.0,  # candidate finished 1s BEFORE body played
        )
        assert decision.should is True
        assert decision.reason == "coalesced_pre_body"

    def test_no_coalesce_when_user_stopped_after_body_started(self):
        """Real continuation: the candidate spoke AFTER the body began, so the
        new utterance is a genuine response, not a stale continuation."""
        decision = _should_coalesce(
            prior=_snapshot(
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1000.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=2001.0,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_no_coalesce_when_user_stopped_exactly_when_body_started(self):
        """Boundary: equal timestamps are treated as 'after' — strict < below the
        body-start time is required to coalesce. Equal timestamps are too
        ambiguous to safely override the existing gate."""
        decision = _should_coalesce(
            prior=_snapshot(
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1000.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=2000.0,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_no_coalesce_when_body_start_unknown(self):
        """body_started_wall_at=None means we couldn't observe a body
        playback start (cached repeat under a test mock, or a code path
        that never recorded it). Fall through to the existing gate so
        production behavior is preserved when instrumentation is missing."""
        decision = _should_coalesce(
            prior=_snapshot(
                speaker_emitted_content=True,
                body_started_wall_at=None,
            ),
            now_monotonic=1000.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=999.0,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_no_coalesce_when_user_stop_time_unknown(self):
        """LiveKit ChatMessage.metrics.stopped_speaking_at can be None for
        some STT/VAD edge cases. Treat as 'can't tell' → preserve the
        existing speaker_delivered behavior rather than guessing."""
        decision = _should_coalesce(
            prior=_snapshot(
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1000.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=None,
        )
        assert decision.should is False
        assert decision.reason == "speaker_delivered"

    def test_pre_body_gate_still_respects_window(self):
        """Pre-body coalescing does NOT bypass the time window. A candidate
        utterance that arrives after coalesce_window_ms is too stale to
        merge regardless of body-start timing."""
        decision = _should_coalesce(
            prior=_snapshot(
                completed_monotonic=1000.0,
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1006.0,  # gap = 6000ms > 5000ms window
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=1999.0,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_pre_body_gate_still_respects_kind_allowlist(self):
        """Pre-body coalescing is constrained to the same _COALESCIBLE_KINDS
        set. e.g. polite_close prior must not be merged forward even if
        the candidate stopped before its body played."""
        decision = _should_coalesce(
            prior=_snapshot(
                instruction_kind="polite_close",
                sub_context="default",
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1000.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=1999.0,
        )
        assert decision.should is False
        assert decision.reason == "kind_not_coalescible"


class TestShouldCoalesceSilenceAwareWindow:
    """Tests for the silence-aware coalescing window.

    Root cause covered by these tests: session 741c2910 — when the
    candidate is continuously speaking through the orchestrator's
    queue lag, the gap between prior TURN_COMPLETED and the new
    TURN_STARTED can exceed coalesce_window_ms, but the candidate
    had no real silence in between. ``_should_coalesce`` now accepts
    ``last_user_speech_end_monotonic`` (the most recent observed
    speaking→listening transition). When that value is more recent
    than ``prior.completed_monotonic``, the window is measured
    against it instead — so a candidate who kept talking gets
    merged forward instead of fragmented across multiple turns.
    """

    def test_window_extends_when_user_kept_speaking_past_prior_completion(self):
        """Prior turn ended at t=1000.0, but candidate's last silence
        onset was at t=1006.0 (i.e., they kept talking 6s past the
        turn boundary). Now=1006.5 → 500ms gap from last silence →
        within window. Should coalesce despite 6.5s elapsed since
        prior turn ended."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0, speaker_emitted_content=False),
            now_monotonic=1006.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            last_user_speech_end_monotonic=1006.0,
        )
        assert decision.should is True
        assert decision.reason == "coalesced"

    def test_window_unchanged_when_silence_onset_predates_prior_completion(self):
        """Candidate's last silence onset was at t=998.0, before the
        prior turn completed at t=1000.0. Prior completion is the
        relevant reference and 6s gap exceeds the 5s window."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0, speaker_emitted_content=False),
            now_monotonic=1006.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            last_user_speech_end_monotonic=998.0,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_window_unchanged_when_no_speech_state_observed(self):
        """At the very first user turn there's been no observed user
        speaking→listening transition yet. ``last_user_speech_end_monotonic``
        is None; the window reference must fall back to the prior
        turn's completion timestamp."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0, speaker_emitted_content=False),
            now_monotonic=1006.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            last_user_speech_end_monotonic=None,
        )
        assert decision.should is False
        assert decision.reason == "window_expired"

    def test_silence_aware_window_composes_with_pre_body_gate(self):
        """Pre-body gate fires AND silence-aware window allows it:
        prior speaker delivered, current user stopped before body
        started, user kept speaking past prior turn completion.
        All three signals align — coalesce with the pre-body reason."""
        decision = _should_coalesce(
            prior=_snapshot(
                completed_monotonic=1000.0,
                speaker_emitted_content=True,
                body_started_wall_at=2000.0,
            ),
            now_monotonic=1006.5,  # 6.5s after prior completion (outside default window)
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            current_user_stopped_speaking_at=1999.0,  # before body started
            last_user_speech_end_monotonic=1006.0,    # 500ms ago in silence terms
        )
        assert decision.should is True
        assert decision.reason == "coalesced_pre_body"

    def test_silence_aware_window_still_respects_kind_allowlist(self):
        """Silence-aware extension MUST NOT bypass _COALESCIBLE_KINDS.
        A non-coalescible prior kind (e.g., polite_close) is rejected
        regardless of how recent the user's last silence onset was."""
        decision = _should_coalesce(
            prior=_snapshot(
                completed_monotonic=1000.0,
                instruction_kind="polite_close",
                sub_context="default",
                speaker_emitted_content=False,
            ),
            now_monotonic=1006.5,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            last_user_speech_end_monotonic=1006.0,
        )
        assert decision.should is False
        assert decision.reason == "kind_not_coalescible"

    def test_silence_aware_window_strictly_after_prior_completion(self):
        """Edge case: ``last_user_speech_end_monotonic`` equals
        ``prior.completed_monotonic`` exactly. Either reference yields
        the same gap; existing window-expired result must be preserved."""
        decision = _should_coalesce(
            prior=_snapshot(completed_monotonic=1000.0, speaker_emitted_content=False),
            now_monotonic=1006.0,
            coalesce_enabled=True,
            coalesce_window_ms=5000,
            last_user_speech_end_monotonic=1000.0,  # equal to prior completion
        )
        assert decision.should is False
        assert decision.reason == "window_expired"


class TestObserveUserState:
    """``observe_user_state`` records the most recent silence onset
    (user speaking→listening transition) on the orchestrator. Used as
    the reference for the silence-aware coalescing window."""

    def test_listening_transition_records_timestamp(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None

        InterviewOrchestrator.observe_user_state(
            orch, new_state="listening", now_monotonic=123.456,
        )
        assert orch._last_user_speech_end_monotonic == 123.456

    def test_speaking_transition_does_not_record_timestamp(self):
        """Only silence ONSETS (transition INTO listening) record a
        timestamp. Speech onsets (listening→speaking) don't, because
        they're not relevant to the window-reference calculation."""
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None

        InterviewOrchestrator.observe_user_state(
            orch, new_state="speaking", now_monotonic=42.0,
        )
        assert orch._last_user_speech_end_monotonic is None

    def test_repeat_listening_transitions_overwrite_with_latest(self):
        """Multiple silence onsets through a session must always store
        the most recent one — that's the reference the window check
        uses."""
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_user_speech_end_monotonic = None

        InterviewOrchestrator.observe_user_state(
            orch, new_state="listening", now_monotonic=10.0,
        )
        InterviewOrchestrator.observe_user_state(
            orch, new_state="speaking", now_monotonic=12.0,
        )
        InterviewOrchestrator.observe_user_state(
            orch, new_state="listening", now_monotonic=15.0,
        )
        assert orch._last_user_speech_end_monotonic == 15.0


class TestCapturePriorTurnSnapshot:
    """Verify _last_turn is populated correctly at every turn-completion path."""

    def test_capture_records_speaker_delivered_when_text_present_and_not_interrupted(
        self,
    ):
        from unittest.mock import MagicMock

        # Construct a bare orchestrator instance (no need for full DI) — we
        # only exercise _capture_prior_turn_snapshot which is a pure method
        # on self._last_turn.
        orch = MagicMock()
        orch._last_turn = None

        # Import the method as an unbound callable and bind to our mock.
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator,
        )
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-1",
            completed_monotonic=42.0,
            candidate_text="hello",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            final_text="What specifically did you set up first?",
            interrupted=False,
            body_started_wall_at=1234.5,
        )
        snap = orch._last_turn
        assert snap is not None
        assert snap.turn_id == "t-1"
        assert snap.candidate_text == "hello"
        assert snap.instruction_kind == "push_back"
        assert snap.sub_context == "missing_specifics"
        assert snap.speaker_emitted_content is True
        assert snap.body_started_wall_at == 1234.5

    def test_capture_records_interrupted_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-2",
            completed_monotonic=43.0,
            candidate_text="basic slips",
            instruction_kind="push_back",
            sub_context="vague_answer",
            final_text="What specifically",  # partial text but interrupted
            interrupted=True,
            body_started_wall_at=None,
        )
        assert orch._last_turn.speaker_emitted_content is False
        assert orch._last_turn.body_started_wall_at is None

    def test_capture_records_empty_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-3",
            completed_monotonic=44.0,
            candidate_text="something",
            instruction_kind="clarify",
            sub_context="default",
            final_text="",
            interrupted=False,
            body_started_wall_at=None,
        )
        assert orch._last_turn.speaker_emitted_content is False
        assert orch._last_turn.body_started_wall_at is None

    def test_capture_records_whitespace_only_speaker_output_as_not_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import InterviewOrchestrator

        orch = MagicMock()
        orch._last_turn = None
        InterviewOrchestrator._capture_prior_turn_snapshot(
            orch,
            turn_id="t-4",
            completed_monotonic=45.0,
            candidate_text="x",
            instruction_kind="clarify",
            sub_context="default",
            final_text="   \n  ",
            interrupted=False,
            body_started_wall_at=None,
        )
        assert orch._last_turn.speaker_emitted_content is False
        assert orch._last_turn.body_started_wall_at is None


class TestCoalescingIntegration:
    """End-to-end check that the orchestrator merges adjacent turns correctly.

    Uses a thin in-test helper that exercises just the coalesce-application
    block at the top of on_user_turn_completed, bypassing the full Judge /
    Speaker pipeline. The pure decision function is already covered by
    TestShouldCoalesce; the snapshot capture is covered by
    TestCapturePriorTurnSnapshot. This test focuses on the wiring between
    them — that a populated _last_turn + a new utterance produces the
    correct combined_text, emits TURN_COALESCED, and clears _last_turn.
    """

    def test_coalesces_two_utterances_and_emits_audit_event(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )
        from app.modules.interview_engine.event_kinds import TURN_COALESCED

        orch = MagicMock(spec=InterviewOrchestrator)
        # Pre-populate _last_turn as if a prior push_back turn finished
        # without delivering its Speaker body.
        prior_text = "First one, like, I would communicate with the client."
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-1",
            completed_monotonic=100.0,
            candidate_text=prior_text,
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=False,
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._collector = MagicMock()
        orch._append = MagicMock()

        new_text = "They are trying to achieve what their existing workflow is."
        # Bind the real method to our mock orchestrator and call it.
        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-1",
            candidate_text=new_text,
            now_monotonic=100.5,  # 500ms after prior → within window
        )
        assert result == prior_text + " " + new_text
        # _last_turn was cleared after coalescing
        assert orch._last_turn is None
        # TURN_COALESCED emitted with correct payload
        orch._append.assert_called_once()
        (kind_arg, payload_arg) = orch._append.call_args.args
        assert kind_arg == TURN_COALESCED
        assert payload_arg["prior_turn_id"] == "prior-1"
        assert payload_arg["current_turn_id"] == "new-1"
        assert payload_arg["combined_text"] == prior_text + " " + new_text
        assert payload_arg["prior_instruction_kind"] == "push_back"
        assert payload_arg["prior_sub_context"] == "missing_specifics"
        assert payload_arg["gap_ms"] == 500
        assert payload_arg["coalesce_window_ms"] == 5000

    def test_no_coalesce_when_speaker_delivered(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-2",
            completed_monotonic=200.0,
            candidate_text="prior text",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=True,  # ← delivered
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-2",
            candidate_text="new text",
            now_monotonic=200.5,
        )
        assert result == "new text"
        # _last_turn is NOT cleared by the no-coalesce path
        assert orch._last_turn is not None
        orch._append.assert_not_called()

    def test_no_coalesce_when_disabled(self):
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-3",
            completed_monotonic=300.0,
            candidate_text="prior text",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=False,
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=False, coalesce_window_ms=5000)
        orch._append = MagicMock()

        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-3",
            candidate_text="new text",
            now_monotonic=300.5,
        )
        assert result == "new text"
        orch._append.assert_not_called()

    def test_coalesces_when_prior_body_played_but_user_stopped_first(self):
        """End-to-end integration check for the pre-body gate.

        Reproduces session 3a8ebdaa, turn 5 → turn 6: prior turn's Speaker
        body fully delivered, but the candidate's STT final #2 timestamp
        predates the body's playback start. The orchestrator should merge
        the two candidate utterances and emit a TURN_COALESCED event.
        """
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )
        from app.modules.interview_engine.event_kinds import TURN_COALESCED

        orch = MagicMock(spec=InterviewOrchestrator)
        prior_text = "Can you please explain that question? I didn't quite understood"
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-5",
            completed_monotonic=100.0,
            candidate_text=prior_text,
            instruction_kind="clarify",
            sub_context="default",
            speaker_emitted_content=True,        # body WAS delivered
            body_started_wall_at=1000.10,        # wall-clock at body playback start
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        new_text = "the question that you were asking me."
        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-6",
            candidate_text=new_text,
            now_monotonic=100.5,
            current_user_stopped_speaking_at=1000.05,  # 50ms BEFORE body started
        )
        assert result == prior_text + " " + new_text
        # _last_turn was cleared after coalescing
        assert orch._last_turn is None
        orch._append.assert_called_once()
        (kind_arg, payload_arg) = orch._append.call_args.args
        assert kind_arg == TURN_COALESCED
        assert payload_arg["combined_text"] == prior_text + " " + new_text
        assert payload_arg["prior_instruction_kind"] == "clarify"
        assert payload_arg["prior_sub_context"] == "default"

    def test_coalesces_continuous_speech_past_default_window(self):
        """Reproduces session 741c2910 turn 11 → turn 12.

        Prior turn was a 'clarify' whose Speaker got interrupted (so
        speaker_emitted_content=False — old gate applies). The
        candidate then kept speaking continuously for 12 seconds
        (Deepgram fragmenting into many small finals), so the gap
        between prior TURN_COMPLETED and the new TURN_STARTED is
        12.5s — well past the default 5s coalesce_window_ms. But
        the candidate's most recent silence onset was 500ms ago.
        With the silence-aware window, this must coalesce.
        """
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )
        from app.modules.interview_engine.event_kinds import TURN_COALESCED

        orch = MagicMock(spec=InterviewOrchestrator)
        prior_text = "And, like, what you are trying to ask."
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-11",
            completed_monotonic=100.0,
            candidate_text=prior_text,
            instruction_kind="clarify",
            sub_context="default",
            speaker_emitted_content=False,  # interrupted before body
            body_started_wall_at=None,
        )
        # Silence onset 500ms before "now" — candidate was still speaking
        # 12 seconds past the prior turn boundary.
        orch._last_user_speech_end_monotonic = 112.0
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        new_text = "Yeah. So, like, most of the frameworks keep getting evolved with time."
        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-12",
            candidate_text=new_text,
            now_monotonic=112.5,  # 12.5s after prior completion, 0.5s after silence
            current_user_stopped_speaking_at=None,
        )
        assert result == prior_text + " " + new_text
        assert orch._last_turn is None
        orch._append.assert_called_once()
        (kind_arg, payload_arg) = orch._append.call_args.args
        assert kind_arg == TURN_COALESCED
        assert payload_arg["reason"] == "coalesced"
        # gap_ms preserves its original semantic (prior turn → now); the
        # silence-aware reference shows up separately.
        assert payload_arg["gap_ms"] == 12500
        assert payload_arg["silence_gap_ms"] == 500

    def test_silence_gap_ms_is_none_when_silence_reference_not_load_bearing(self):
        """When the prior turn completed more recently than the user's
        last silence onset (or no silence onset has been observed),
        silence_gap_ms must be None — making the audit envelope
        self-documenting about which reference was used."""
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-x",
            completed_monotonic=100.0,
            candidate_text="prior",
            instruction_kind="push_back",
            sub_context="missing_specifics",
            speaker_emitted_content=False,
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-x",
            candidate_text="new",
            now_monotonic=100.5,
            current_user_stopped_speaking_at=None,
        )
        (_, payload_arg) = orch._append.call_args.args
        assert payload_arg["silence_gap_ms"] is None

    def test_does_not_coalesce_when_prior_body_played_and_user_replied_after(self):
        """Negative control for the pre-body gate: candidate's
        stopped_speaking_at is AFTER the body started, so the new utterance
        is a genuine response, not a stale continuation. Must NOT coalesce."""
        from unittest.mock import MagicMock
        from app.modules.interview_engine.orchestrator import (
            InterviewOrchestrator, _PriorTurnSnapshot,
        )

        orch = MagicMock(spec=InterviewOrchestrator)
        orch._last_turn = _PriorTurnSnapshot(
            turn_id="prior-7",
            completed_monotonic=200.0,
            candidate_text="prior",
            instruction_kind="clarify",
            sub_context="default",
            speaker_emitted_content=True,
            body_started_wall_at=2000.0,
        )
        orch._last_user_speech_end_monotonic = None
        orch._config = MagicMock(coalesce_enabled=True, coalesce_window_ms=5000)
        orch._append = MagicMock()

        result = InterviewOrchestrator._maybe_coalesce(
            orch,
            current_turn_id="new-8",
            candidate_text="real reply",
            now_monotonic=200.5,
            current_user_stopped_speaking_at=2001.0,  # AFTER body start
        )
        assert result == "real reply"
        assert orch._last_turn is not None  # prior snapshot kept (no coalesce)
        orch._append.assert_not_called()
