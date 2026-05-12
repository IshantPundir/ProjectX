"""InterviewOrchestrator — drives the per-turn pipeline.

This is the LiveKit hook surface. on_enter delivers the first question via a
synthesized JudgeOutput. on_user_turn_completed runs Judge → State Engine →
Speaker on each candidate turn. on_close builds the SessionResult.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.modules.interview_engine.audit_events import (
    FrontendAttributePayload, JudgeSyntheticPayload,
    SessionTerminalDeliveredPayload,
    SpeakerCallPayload, SpeakerOutputPayload,
    TurnCoalescedPayload, TurnCompletedPayload, TurnStartedPayload,
)
from app.modules.interview_engine.event_kinds import (
    FRONTEND_ATTRIBUTE_PUBLISHED, JUDGE_SYNTHETIC,
    SESSION_TERMINAL_DELIVERED,
    SPEAKER_CALL, SPEAKER_OUTPUT, TURN_COALESCED, TURN_COMPLETED, TURN_STARTED,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_TIME_REMAINING_SECONDS,
    ATTR_TOTAL_QUESTIONS, AttributePublisher,
)
from app.modules.interview_engine.judge.service import JudgeService
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.speaker.service import SpeakerService
from app.modules.interview_engine.state.engine import (
    StateEngine,
)
from app.modules.interview_runtime import SessionConfig

_log = structlog.get_logger(__name__)

# Recent-turns window applied to BOTH judge and speaker LLM input payloads.
# The State Engine still holds the full transcript (for SessionResult and
# audit); this is purely a per-call slice to bound prompt growth.
#
# Sized for ~4 candidate-agent pairs of context — enough to maintain
# continuity ("you mentioned X earlier") without paying ~200 tok/turn of
# inflation forever. With 80-turn sessions this caps recent_turns at
# ~1.5 KB instead of ~12 KB.
_RECENT_TURNS_WINDOW = 8


def _slice_recent_turns(transcript: list) -> list:
    """Return the last _RECENT_TURNS_WINDOW transcript entries."""
    if len(transcript) <= _RECENT_TURNS_WINDOW:
        return transcript
    return transcript[-_RECENT_TURNS_WINDOW:]


def _was_interrupted(speech_handle: Any) -> bool:
    """True iff LiveKit's SpeechHandle reports the candidate interrupted.

    Defensive: some test mocks return None or AsyncMock from session.say
    rather than a real SpeechHandle. ``.interrupted`` is the documented
    LiveKit API (see /agents/multimodality/audio/ docs); when it's
    missing or unreadable we conservatively return False so the existing
    fallback path still fires.
    """
    if speech_handle is None:
        return False
    try:
        return bool(getattr(speech_handle, "interrupted", False))
    except Exception:  # noqa: BLE001
        return False


def _derive_sub_context(speaker_input: Any) -> str:
    """Discriminator string for the current turn's sub-kind.

    Used by Continuation Coalescing (``_COALESCIBLE_KINDS``) and by the
    ``turn.coalesced`` audit event's ``prior_sub_context`` field for
    forensic replay. Values are stable strings — never persist the
    raw Python identity, only this discriminator.
    """
    kind = speaker_input.instruction_kind
    if kind == InstructionKind.deliver_question:
        if getattr(speaker_input, "is_post_cap_advance", False):
            return "post_cap_advance"
        return "default"
    if kind == InstructionKind.redirect:
        tm = speaker_input.turn_metadata
        if tm is not None:
            if tm.candidate_social_or_greeting:
                return "social_or_greeting"
            if tm.candidate_abusive:
                return "abusive"
            if tm.candidate_attempted_injection:
                return "injection"
            if tm.candidate_off_topic:
                return "off_topic"
        return "off_topic"  # default redirect bucket
    if kind == InstructionKind.push_back:
        return speaker_input.push_back_reason_code or "default"
    if kind == InstructionKind.polite_close:
        if speaker_input.failed_signal_value:
            return "knockout"
        return "default"
    return "default"


def _map_livekit_close_reason(close_reason: str | None) -> str:
    """Map a LiveKit ``CloseReason`` value-string to a SessionOutcome string.

    Used by :meth:`InterviewOrchestrator.resolve_close_outcome` as the
    fallback when no structured ``lifecycle.last_outcome`` was set by
    the State Engine. The returned string is one of the
    ``state.lifecycle.SessionOutcome`` values so it is interchangeable
    with the structured-outcome branch.

    Mapping:

    * ``"participant_disconnected"`` → ``"candidate_disconnected"``
      (LiveKit's word for "the remote party left" maps cleanly to ours).
    * ``"user_initiated"`` → ``"completed"``. ``user_initiated`` is the
      reason LiveKit reports when WE call ``session.shutdown()``; if the
      structured agent did so without setting ``last_outcome`` (rare —
      checkpoint shutdown, manual ops shutdown), "completed" is the
      least-misleading label.
    * ``"error"`` → ``"error"`` (defensive — the higher-priority
      ``CloseReason.ERROR`` branch in :meth:`resolve_close_outcome`
      already handles this; included here for completeness so unit
      tests against the mapper directly do the right thing).
    * Anything else (None, unknown future enum value) → ``"error"``.
      Unknown reasons are treated as errors for safety: the audit
      envelope is forensic data, and "error" surfaces an unexpected
      close cleanly rather than masquerading as a normal completion.
    """
    if close_reason == "participant_disconnected":
        return "candidate_disconnected"
    if close_reason == "user_initiated":
        return "completed"
    if close_reason == "error":
        return "error"
    return "error"


@dataclass(frozen=True, slots=True)
class _PriorTurnSnapshot:
    """End-of-turn snapshot used by Continuation Coalescing.

    Captured at end of each turn, read at the start of the next. See
    ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.

    ``body_started_wall_at`` is the wall-clock timestamp (seconds since the
    Unix epoch, ``time.time()`` units) at which the Speaker body's audio
    playback was scheduled with LiveKit. It is ``None`` when no body played
    on this turn — interrupted/empty Speaker output, the error-recovery
    branch, or the synthetic session-start turn. Used by the pre-body
    coalescing gate to detect the case where the candidate produced a
    continuation utterance BEFORE the agent's response was audible (so the
    continuation cannot be a reply to it).
    """
    turn_id: str
    completed_monotonic: float
    candidate_text: str
    instruction_kind: str
    sub_context: str
    speaker_emitted_content: bool
    body_started_wall_at: float | None = None


@dataclass(frozen=True, slots=True)
class _SpeakerStreamOutcome:
    """Return shape for ``_stream_speaker_and_say``.

    Named fields beat a 4-tuple at the call site — the prior-turn snapshot
    capture is explicit about which value goes into which field of
    ``_PriorTurnSnapshot``.
    """
    final_text: str
    interrupted: bool
    sub_context: str
    body_started_wall_at: float | None


# Judge actions whose response MUST be delivered to the candidate, even
# if the candidate resumed speaking during the Judge LLM call. These are
# explicit candidate-intent acknowledgements (or terminal states); silently
# dropping them is catastrophic UX — the candidate is left hanging and
# typically forced to re-issue the request.
#
# Diagnosed in session 11b3c321 (2026-05-11): the candidate said "I would
# like to end this interview now.", Judge correctly returned end_session,
# but the post-Judge resumption gate dropped the response because the
# candidate had already started saying "End this interview now." (a
# re-issue, because the agent hadn't yet acknowledged the first request).
# The candidate had to say end twice.
#
# Source of truth for the action names: NextAction StrEnum in
# ``app/modules/interview_engine/models/judge.py``. Values are checked
# as strings (StrEnum compares equal to its string value), so the test
# whitelist below stays in sync regardless of import-cycle concerns.
_MUST_DELIVER_JUDGE_ACTIONS: frozenset[str] = frozenset({
    "end_session",                # candidate asked to end
    "polite_close",               # engine-initiated terminal close
    "acknowledge_no_experience",  # candidate disclosed "I don't know"
    "repeat",                     # candidate asked to hear the question again
})


# (instruction_kind, sub_context) pairs whose prior turn is eligible to be
# merged forward into the next turn's candidate utterance text. The check
# is on the PRIOR turn's resolved action — see the spec's classification
# table for the rationale of each entry.
_COALESCIBLE_KINDS: frozenset[tuple[str, str]] = frozenset({
    # deliver_question — fresh main question
    ("deliver_question", "default"),
    ("deliver_question", "post_cap_advance"),
    # deliver_probe — follow-up probe
    ("deliver_probe", "default"),
    # push_back — the Judge wanted more specifics
    ("push_back", "vague_answer"),
    ("push_back", "deflection"),
    ("push_back", "missing_specifics"),
    ("push_back", "unanswered_subquestion"),
    # clarify — Judge wanted to rephrase
    ("clarify", "default"),
    # acknowledge_no_experience — Judge confirmed no-experience routing
    ("acknowledge_no_experience", "default"),
    # redirect — only social_or_greeting; off_topic/abusive/injection are
    # explicit behavioral judgments and not coalescible
    ("redirect", "social_or_greeting"),
})


@dataclass(frozen=True, slots=True)
class _CoalesceDecision:
    """Outcome of the coalescing decision plus a string reason for audit logging."""
    should: bool
    reason: str


def _should_coalesce(
    *,
    prior: _PriorTurnSnapshot | None,
    now_monotonic: float,
    coalesce_enabled: bool,
    coalesce_window_ms: int,
    current_user_stopped_speaking_at: float | None = None,
    last_user_speech_end_monotonic: float | None = None,
) -> _CoalesceDecision:
    """Pure decision function for Continuation Coalescing.

    Two coalescing gates are evaluated. The first is the original
    "Speaker did not deliver" path; the second handles the case where the
    Speaker DID deliver a body but the candidate's new utterance ended
    BEFORE that body became audible — they cannot have been responding
    to it (root-cause for session 3a8ebdaa, turn 5 → turn 6).

    The window check uses a **silence-aware reference**: when
    ``last_user_speech_end_monotonic`` is more recent than
    ``prior.completed_monotonic``, the gap is measured from the
    candidate's most recent silence onset rather than from the prior
    turn's completion. This keeps continuous-speech continuations
    eligible even when the orchestrator's queue lag pushes the new
    turn past ``coalesce_window_ms`` of the prior turn's completion
    (root-cause for session 741c2910, turn 11 → turn 12: 12.5s
    between turn boundaries, but only 0.5s of actual silence).

    Returns ``_CoalesceDecision(should=True, ...)`` with reason ``"coalesced"``
    or ``"coalesced_pre_body"`` when ALL gates pass. Otherwise returns
    ``should=False`` with a reason string identifying which gate failed.
    The reason is consumed by audit logging and tests.

    Preconditions evaluated in order:

    1. ``coalesce_enabled`` is True.
    2. A ``prior`` snapshot exists.
    3. Either:
       a. ``prior.speaker_emitted_content`` is False — reason ``"coalesced"``, or
       b. ``prior.speaker_emitted_content`` is True AND
          ``prior.body_started_wall_at`` is not None AND
          ``current_user_stopped_speaking_at`` is not None AND
          ``current_user_stopped_speaking_at < prior.body_started_wall_at``
          — reason ``"coalesced_pre_body"``.
       Otherwise returns ``"speaker_delivered"``.
    4. The gap from the window reference (the more recent of
       ``prior.completed_monotonic`` and ``last_user_speech_end_monotonic``)
       to ``now_monotonic`` is strictly less than ``coalesce_window_ms``.
    5. The prior turn's ``(instruction_kind, sub_context)`` is in
       ``_COALESCIBLE_KINDS``.
    """
    if not coalesce_enabled:
        return _CoalesceDecision(False, "disabled")
    if prior is None:
        return _CoalesceDecision(False, "no_prior_turn")

    # Gate 3 — Speaker-delivery discriminator. Computes the coalesce reason
    # (or rejects) BEFORE evaluating the window / kind preconditions so the
    # reason strings match the existing audit contract.
    if not prior.speaker_emitted_content:
        coalesce_reason = "coalesced"
    elif (
        prior.body_started_wall_at is not None
        and current_user_stopped_speaking_at is not None
        and current_user_stopped_speaking_at < prior.body_started_wall_at
    ):
        # Speaker delivered, but the candidate finished speaking before
        # the body became audible — treat as continuation, not response.
        coalesce_reason = "coalesced_pre_body"
    else:
        return _CoalesceDecision(False, "speaker_delivered")

    # Silence-aware window reference: prefer the candidate's most recent
    # silence onset when it postdates the prior turn's completion. Strict
    # ">" so that an equal timestamp falls back to the original semantic
    # (no behavioral change at the boundary).
    window_reference = prior.completed_monotonic
    if (
        last_user_speech_end_monotonic is not None
        and last_user_speech_end_monotonic > prior.completed_monotonic
    ):
        window_reference = last_user_speech_end_monotonic

    gap_ms = (now_monotonic - window_reference) * 1000
    if gap_ms >= coalesce_window_ms:
        return _CoalesceDecision(False, "window_expired")
    if (prior.instruction_kind, prior.sub_context) not in _COALESCIBLE_KINDS:
        return _CoalesceDecision(False, "kind_not_coalescible")
    return _CoalesceDecision(True, coalesce_reason)


@dataclass(slots=True)
class OrchestratorConfig:
    checkpoint_turns: int = 10
    checkpoint_seconds: int = 30
    # Canned terminal message played when the candidate keeps talking
    # after lifecycle has already entered closing/closed. Supports a
    # ``{candidate_name}`` placeholder. Default has no placeholder so
    # the entrypoint's env-driven Settings value is the source of truth
    # in production; this default keeps tests deterministic.
    session_ended_message: str = (
        "Thanks for your time. This session has ended; the recruitment "
        "team will be in contact with you."
    )
    # Continuation coalescing — see _should_coalesce + _PriorTurnSnapshot
    # above, and the spec at
    # docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md
    coalesce_enabled: bool = True
    coalesce_window_ms: int = 5000
    # Stale-turn drop-and-drain — see _is_stale_turn and
    # _buffer_dropped_text. When on_user_turn_completed fires with a
    # fragment older than this threshold AND a more-recent silence
    # onset has been observed, the text is buffered and the reply
    # suppressed; the buffer drains into the next non-dropped turn.
    stale_turn_threshold_ms: int = 8000
    stale_buffer_max: int = 8
    # Post-Judge resumption gate — see _user_resumed_speaking_after.
    # Tolerance for the speech-resumption check that runs AFTER Judge
    # returns and BEFORE Speaker. A listening→speaking transition
    # observed within this window of the on_user_turn_completed entry
    # is treated as the tail of the just-finished utterance, not a
    # genuine new turn.
    post_judge_resumption_epsilon_ms: int = 200


class InterviewOrchestrator:
    def __init__(
        self,
        *,
        session_config: SessionConfig,
        tenant_settings: Any,
        state_engine: StateEngine,
        judge: JudgeService,
        speaker: SpeakerService,
        attr_publisher: AttributePublisher,
        event_collector: EventCollector,
        correlation_id: str,
        config: OrchestratorConfig | None = None,
        tenant_id: str,
    ) -> None:
        self._cfg = session_config
        self._tenant = tenant_settings
        self._tenant_id = tenant_id
        self._state = state_engine
        self._judge = judge
        self._speaker = speaker
        self._attr = attr_publisher
        self._collector = event_collector
        self._correlation_id = correlation_id
        self._config = config or OrchestratorConfig()
        self._turn_index = -1  # incremented to 0 on session-start synthetic turn
        self._session_started_monotonic: float | None = None
        # Tracks whether ``agent.session.shutdown`` has already been
        # scheduled for this orchestrator instance. The hard-stop path
        # (lifecycle in closing/closed + candidate-input arrival) and
        # the post-Judge knockout-policy-override path both call into
        # ``_schedule_shutdown``; this flag keeps it idempotent.
        self._shutdown_scheduled: bool = False
        # Continuation coalescing — see _should_coalesce / _capture_prior_turn_snapshot.
        # Populated at end of each turn; consulted at the start of the next turn.
        self._last_turn: _PriorTurnSnapshot | None = None
        # Silence-aware coalescing window reference. Tracks the wall-monotonic
        # timestamp of the candidate's most recent speaking→listening
        # transition (silence onset). Populated by ``observe_user_state``,
        # which is wired from agent.py's session.user_state_changed listener.
        # When more recent than the prior turn's completion timestamp,
        # ``_should_coalesce`` uses it as the window reference so a
        # continuously-talking candidate isn't fragmented across multiple
        # un-coalesced turns by orchestrator queue lag.
        self._last_user_speech_end_monotonic: float | None = None
        # Wall-clock companion of the field above. Used by ``_is_stale_turn``
        # to compare against ``new_message.metrics.stopped_speaking_at``
        # (a wall-clock value) so we can detect "the candidate produced
        # speech AFTER this fragment was sealed." Both clocks are updated
        # together in ``observe_user_state`` so they stay consistent.
        self._last_user_speech_end_wall: float | None = None
        # Stale-turn drop buffer. Holds candidate texts that were dropped
        # because they arrived past the staleness threshold; drained into
        # the next non-dropped turn's candidate_text BEFORE the coalesce
        # gate runs. List order is preservation-order (oldest first).
        self._stale_buffer: list[str] = []
        # Wall-clock timestamp of the candidate's most recent
        # listening→speaking transition (i.e., "user resumed talking").
        # Populated by ``observe_user_state`` and consulted by the
        # post-Judge resumption gate to detect "the user started a new
        # utterance while I was running Judge." See
        # ``_user_resumed_speaking_after``.
        self._resumed_speaking_at: float | None = None

    # --- Public accessors ---

    def lifecycle_snapshot(self) -> Any:
        """Public passthrough to the underlying StateEngine.

        The close handler in ``agent.py`` needs to read
        ``lifecycle.last_outcome`` to decide the persisted SessionOutcome
        without reaching into the orchestrator's private ``_state``.
        """
        return self._state.lifecycle_snapshot()

    def resolve_close_outcome(self, *, close_reason: str | None) -> str:
        """Determine the canonical session outcome string at close time.

        Resolution order (highest priority first):

        1. **LiveKit ERROR close reason** → ``"error"``. A pipeline-level
           error wins over any structured outcome — there is no "graceful"
           recovery from an underlying transport / plugin failure, and the
           audit envelope must reflect that the session terminated abnormally.
        2. **``state_engine.lifecycle.last_outcome``** — the structured
           agent's authoritative outcome (``knockout_closed``, ``completed``,
           ``candidate_ended``, ``time_expired``, etc.) set by the State
           Engine when a knockout-policy override fires, the candidate
           ends the session, mandatory coverage completes, the time
           budget exhausts, etc.
        3. **LiveKit-reported close reason** mapped via
           :func:`_map_livekit_close_reason` — fallback used when no
           structured outcome was recorded (e.g. the candidate disconnected
           mid-session before any State Engine outcome was set).

        Used by ``agent.py::_handle_close`` to populate BOTH the
        ``session.close`` audit payload's ``controller_end_outcome``
        field AND the ``session_outcome`` participant attribute that the
        candidate frontend reads. They MUST agree, so the resolution
        logic lives once, here, on the orchestrator (which already owns
        the StateEngine). Previously the close handler was reading a
        local mirror (``agent._end_outcome``) that nothing populated for
        knockout / completed / time_expired paths — only the
        participant-disconnect listener wrote to it — so the audit event
        and frontend attribute reported ``null`` for every structured
        close.
        """
        if close_reason == "error":
            return "error"
        lifecycle_outcome = self._state.lifecycle_snapshot().last_outcome
        if lifecycle_outcome is not None:
            return lifecycle_outcome.value
        return _map_livekit_close_reason(close_reason)

    def observe_user_state(
        self,
        *,
        new_state: str,
        now_monotonic: float | None = None,
        now_wall: float | None = None,
    ) -> None:
        """Record a candidate speech-state transition.

        Updates three pieces of state, all load-bearing:

        * ``_last_user_speech_end_monotonic`` (on ``"listening"``) —
          referenced by the silence-aware coalescing window (compared
          against ``prior.completed_monotonic``).
        * ``_last_user_speech_end_wall`` (on ``"listening"``) —
          referenced by stale-turn drop detection (compared against
          ``new_message.metrics.stopped_speaking_at``, a wall-clock
          value).
        * ``_resumed_speaking_at`` (on ``"speaking"``) — referenced by
          the post-Judge resumption gate. When a listening→speaking
          transition is observed while the orchestrator is mid-Judge,
          this timestamp drives the gate's "user started a new
          utterance" decision.

        Called from agent.py's ``user_state_changed`` event handler.
        ``now_monotonic`` / ``now_wall`` default to ``time.monotonic()``
        / ``time.time()`` so production callers don't need to capture
        timestamps themselves; tests pass explicit values for
        determinism.

        Thread-safety: invoked on the same asyncio loop as
        ``on_user_turn_completed``, so simple attribute writes are
        race-free under Python's GIL semantics.
        """
        if new_state == "listening":
            self._last_user_speech_end_monotonic = (
                now_monotonic if now_monotonic is not None else time.monotonic()
            )
            self._last_user_speech_end_wall = (
                now_wall if now_wall is not None else time.time()
            )
        elif new_state == "speaking":
            self._resumed_speaking_at = (
                now_wall if now_wall is not None else time.time()
            )

    def _is_stale_turn(
        self,
        *,
        stopped_speaking_at: float | None,
        now_wall: float | None = None,
    ) -> bool:
        """Decide whether a delivered user-turn is stale enough to drop.

        Two preconditions must BOTH hold:

        1. ``staleness_ms = (now_wall - stopped_speaking_at) * 1000``
           strictly exceeds ``config.stale_turn_threshold_ms``.
        2. ``_last_user_speech_end_wall`` is strictly more recent than
           ``stopped_speaking_at`` — evidence that the candidate
           produced speech AFTER this fragment was sealed, i.e. a
           fresher turn is queued behind it.

        Returns False (don't drop) when either signal is missing —
        we'd rather over-process a fragment than drop a legitimate
        user turn whose timestamps we couldn't observe.
        """
        if stopped_speaking_at is None:
            return False
        if self._last_user_speech_end_wall is None:
            return False
        if now_wall is None:
            now_wall = time.time()
        staleness_ms = (now_wall - stopped_speaking_at) * 1000
        if staleness_ms <= self._config.stale_turn_threshold_ms:
            return False
        # Both timestamps are wall-clock seconds; the strict ">" semantics
        # mirror _should_coalesce so equal timestamps do not flip the
        # decision at the boundary.
        return self._last_user_speech_end_wall > stopped_speaking_at

    def _user_resumed_speaking_after(self, t_wall: float) -> bool:
        """Was a listening→speaking transition observed STRICTLY after
        ``t_wall + epsilon``?

        Used by the post-Judge resumption gate: ``t_wall`` is the
        wall-clock at which ``on_user_turn_completed`` entered; the gate
        fires if the candidate started a new utterance during Judge
        processing. The epsilon comes from
        ``config.post_judge_resumption_epsilon_ms`` and tolerates
        clock skew between LiveKit's ``stopped_speaking_at`` and our
        own ``observe_user_state`` timing — a "resumption" within
        epsilon of the callback fire is the tail of the just-finished
        utterance, not a fresh turn.
        """
        if self._resumed_speaking_at is None:
            return False
        epsilon_s = self._config.post_judge_resumption_epsilon_ms / 1000.0
        return self._resumed_speaking_at > t_wall + epsilon_s

    def _buffer_dropped_text(
        self,
        *,
        candidate_text: str,
        turn_id: str,
        stopped_speaking_at: float | None,
        staleness_ms: int,
    ) -> None:
        """Append a dropped turn's text to ``_stale_buffer`` and emit
        the ``turn.dropped`` audit event. Enforces the configured
        ``stale_buffer_max`` cap by evicting the oldest entry FIFO.
        """
        from app.modules.interview_engine.audit_events import TurnDroppedPayload
        from app.modules.interview_engine.event_kinds import TURN_DROPPED

        self._stale_buffer.append(candidate_text)
        while len(self._stale_buffer) > self._config.stale_buffer_max:
            self._stale_buffer.pop(0)

        self._append(TURN_DROPPED, TurnDroppedPayload(
            turn_id=turn_id,
            candidate_text=candidate_text,
            stopped_speaking_at=stopped_speaking_at,
            staleness_ms=staleness_ms,
            buffer_size_after=len(self._stale_buffer),
        ).model_dump())

    def _drain_stale_buffer(
        self,
        *,
        candidate_text: str,
        current_turn_id: str,
    ) -> str:
        """Drain buffered stale texts (if any) into the front of
        ``candidate_text``. Emits ``turn.drain_replayed`` and clears
        the buffer. Returns the candidate text the coalesce gate
        should then operate on.
        """
        if not self._stale_buffer:
            return candidate_text

        from app.modules.interview_engine.audit_events import TurnDrainReplayedPayload
        from app.modules.interview_engine.event_kinds import TURN_DRAIN_REPLAYED

        dropped_texts = list(self._stale_buffer)
        combined = " ".join(dropped_texts + [candidate_text])
        self._stale_buffer.clear()

        self._append(TURN_DRAIN_REPLAYED, TurnDrainReplayedPayload(
            current_turn_id=current_turn_id,
            dropped_count=len(dropped_texts),
            dropped_texts=dropped_texts,
            combined_text=combined,
        ).model_dump())
        return combined

    # --- LiveKit lifecycle hooks ---

    async def on_enter(self, agent: Any) -> None:
        self._session_started_monotonic = time.monotonic()
        turn_id = str(uuid.uuid4())
        self._turn_index += 1

        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=None, stt_text_used=None,
        ).model_dump())

        synthetic = self._state.initialize_for_session_start()
        self._append(JUDGE_SYNTHETIC, JudgeSyntheticPayload(
            turn_id=turn_id, output=synthetic.model_dump(mode="json"),
            reason="session_start",
        ).model_dump())

        decision = self._state.process_judge_output(
            turn_id=turn_id, judge_output=synthetic,
            candidate_utterance_text=None, elapsed_ms=0,
        )

        # Determine total questions (located on stage.questions per the actual schema).
        total_questions = len(self._cfg.stage.questions)
        await self._publish_attributes(
            turn_id=turn_id,
            current_question_index=self._state.queue_snapshot().active_index or 0,
            total_questions=total_questions,
            time_remaining_seconds=int(
                self._state.lifecycle_snapshot().time_remaining_seconds()
            ),
        )

        self._append_speaker_input(turn_id=turn_id, speaker_input=decision.speaker_input)
        await self._stream_speaker_and_say(
            agent=agent, turn_id=turn_id,
            speaker_input=decision.speaker_input,
        )

        # Tick lifecycle elapsed-time so subsequent attribute publishes
        # / Judge inputs see a counted-down ``time_remaining_seconds``.
        self._state.set_time_elapsed(self._elapsed_ms() / 1000.0)

        self._append(TURN_COMPLETED, TurnCompletedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            duration_ms=int((time.monotonic() - self._session_started_monotonic) * 1000),
        ).model_dump())

    async def on_user_turn_completed(
        self, agent: Any, turn_ctx: Any, new_message: Any,
    ) -> None:
        # No StopResponse here — see StructuredInterviewAgent docstring.
        # Returning normally lets the framework auto-append new_message to
        # chat_ctx, which fires conversation_item_added and populates the
        # LiveKit chat_history. The agent's llm_node override yields
        # nothing, so no duplicate LLM reply is generated.
        candidate_text = getattr(new_message, "text_content", None)
        # ChatMessage.text_content can be a property — call it if it's a method,
        # otherwise it's a string already.
        if callable(candidate_text):
            candidate_text = candidate_text()
        if not candidate_text:
            return  # nothing to process; framework's default flow is harmless

        # Hard-stop: lifecycle is closing/closed. Bypass Judge entirely
        # and play the canned terminal message. Ensure LiveKit session
        # shutdown is scheduled (idempotent). This is the fix for the
        # "agent keeps talking after polite_close" bug — without it the
        # framework keeps listening and the orchestrator would run a full
        # Judge → State → Speaker turn against an already-closed session.
        lifecycle_snap = self._state.lifecycle_snapshot()
        if lifecycle_snap.state.value in ("closing", "closed"):
            await self._handle_post_close_turn(
                agent=agent, candidate_text=candidate_text,
            )
            return

        # Wall-clock timestamp at which on_user_turn_completed fired.
        # Captured up-front so the post-Judge resumption gate can ask
        # "did the candidate start a new utterance AFTER we got the
        # callback?" using a stable reference point. ``time.monotonic()``
        # is not used here because we compare against
        # ``_resumed_speaking_at``, which is a wall-clock value.
        original_callback_wall = time.time()

        # Wall-clock timestamp at which the candidate stopped speaking on
        # this turn. LiveKit's ChatMessage exposes a ``metrics`` object
        # (``AgentMetrics``) with ``stopped_speaking_at: float | None``.
        # Both attribute lookups are defensive: some test mocks pass a
        # plain object without a ``metrics`` attribute, and the framework
        # itself can produce STT-edge cases where ``stopped_speaking_at``
        # is None. Either way, the pre-body coalescing gate and the
        # stale-turn drop detector degrade to safe defaults.
        current_user_stopped_speaking_at = getattr(
            getattr(new_message, "metrics", None),
            "stopped_speaking_at",
            None,
        )

        # Stale-turn drop-and-drain: if this fragment is older than
        # ``stale_turn_threshold_ms`` AND we've observed a more recent
        # silence onset, the orchestrator's queue is behind the
        # candidate's real-time speech. Buffer the text and return
        # early; the framework's `llm_node` override is a no-op so no
        # reply plays. The buffer drains into the next non-dropped
        # turn (see _drain_stale_buffer below).
        if self._is_stale_turn(stopped_speaking_at=current_user_stopped_speaking_at):
            dropped_turn_id = str(uuid.uuid4())
            now_wall = time.time()
            staleness_ms = int(
                (now_wall - (current_user_stopped_speaking_at or now_wall)) * 1000
            )
            self._buffer_dropped_text(
                candidate_text=candidate_text,
                turn_id=dropped_turn_id,
                stopped_speaking_at=current_user_stopped_speaking_at,
                staleness_ms=staleness_ms,
            )
            return

        # Pre-generate turn_id so the coalesce-audit event references the
        # same value that TURN_STARTED will carry below.
        turn_id = str(uuid.uuid4())

        # Drain any buffered stale fragments BEFORE the coalesce gate.
        # The drain prepends them to candidate_text in original-drop
        # order; coalescing then sees the merged text.
        candidate_text = self._drain_stale_buffer(
            candidate_text=candidate_text,
            current_turn_id=turn_id,
        )

        candidate_text = self._maybe_coalesce(
            current_turn_id=turn_id,
            candidate_text=candidate_text,
            now_monotonic=time.monotonic(),
            current_user_stopped_speaking_at=current_user_stopped_speaking_at,
        )
        self._turn_index += 1
        elapsed_ms = self._elapsed_ms()
        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=candidate_text, stt_text_used=candidate_text,
        ).model_dump())
        self._append_state_snapshot(turn_id=turn_id)

        from app.modules.interview_engine.judge.input_builder import (
            ActiveSignalMeta, build_judge_input,
        )
        active_qid = self._state.queue_snapshot().active_index
        active_q_cfg = (
            self._cfg.stage.questions[active_qid] if active_qid is not None else None
        )
        ledger = self._state.ledger_snapshot()
        queue = self._state.queue_snapshot()
        claims = self._state.claims_snapshot()
        # Cap recent_turns to bound per-call prompt growth. The State Engine
        # retains the full transcript for SessionResult; this slice is only
        # what the Judge LLM sees this turn.
        recent = _slice_recent_turns(self._state.transcript_snapshot())
        time_remaining = int(self._state.lifecycle_snapshot().time_remaining_seconds())

        # Project the active question's signal_values to ActiveSignalMeta
        # so the Judge can see knockout flags. Enforcement still happens
        # deterministically at the State Engine — this is informational.
        active_signal_meta: list[ActiveSignalMeta] = []
        if active_q_cfg is not None:
            sig_meta_map = {sm.value: sm for sm in self._cfg.signal_metadata}
            for sv in active_q_cfg.signal_values:
                sm = sig_meta_map.get(sv)
                if sm is not None:
                    active_signal_meta.append(ActiveSignalMeta(
                        value=sm.value,
                        knockout=sm.knockout,
                        priority=sm.priority,
                    ))

        # Build the remaining-probes dict from the queue's
        # probes_remaining_ids (probe_ids that have NOT been consumed yet)
        # mapped to their text. Replaces the old "send full follow_ups
        # list and let the Judge pick anything" model that triggered
        # invalid_probe_id self-heals when the Judge re-picked a
        # consumed probe.
        remaining_probes_dict: dict[str, str] = {}
        active_q_state = queue.questions[queue.active_index] if queue.active_index is not None else None
        if active_q_cfg is not None and active_q_state is not None:
            for pid in active_q_state.probes_remaining_ids:
                try:
                    idx = int(pid)
                except ValueError:
                    continue
                if 0 <= idx < len(active_q_cfg.follow_ups):
                    remaining_probes_dict[pid] = active_q_cfg.follow_ups[idx]

        active_push_back_count = (
            queue.questions[queue.active_index].push_back_count
            if queue.active_index is not None
            else 0
        )
        active_dont_know_count = (
            queue.questions[queue.active_index].consecutive_dont_know_count
            if queue.active_index is not None
            else 0
        )

        judge_input = build_judge_input(
            active_question=active_q_cfg,
            ledger_snapshot=ledger, queue_snapshot=queue, claims_snapshot=claims,
            recent_turns=recent, candidate_utterance=candidate_text,
            time_remaining_seconds=time_remaining,
            next_pending_mandatory_id=self._state.next_pending_mandatory_id(),
            active_signal_metadata=active_signal_meta,
            active_remaining_probes=remaining_probes_dict,
            active_question_push_back_count=active_push_back_count,
            active_question_consecutive_dont_know_count=active_dont_know_count,
        )

        result = await self._judge.call(
            turn_id=turn_id, input_payload=judge_input,
            correlation_id=self._correlation_id,
            tenant_id=self._tenant_id,
        )
        self._append_judge_event(turn_id=turn_id, result=result, input_payload=judge_input)

        # Post-Judge resumption gate. If the candidate produced a new
        # listening→speaking transition WHILE Judge was running, the
        # response we're about to commit is to the prior fragment — but
        # the candidate has already moved on. Buffer the text and abort:
        # no State Engine mutation, no Speaker call, no audio reply. The
        # next on_user_turn_completed callback drains the buffer + new
        # text and runs Judge on the merged input.
        #
        # EXCEPTION: when Judge's action is in ``_MUST_DELIVER_JUDGE_ACTIONS``,
        # the response is an explicit candidate-intent acknowledgement
        # (end_session, polite_close, acknowledge_no_experience, repeat)
        # — silently dropping it would leave the candidate hanging and
        # typically force them to re-issue the request. Deliver the
        # response immediately and let the resumed speech become the
        # next turn (which the framework will deliver after the close
        # speech finishes or, for non-terminal actions, after the agent
        # finishes speaking).
        #
        # The Judge audit event above is kept intentionally: the Judge
        # ran, the LLM result exists, and it's forensically useful for
        # replay tooling to see what Judge classified the stale text as
        # before the orchestrator decided to abandon it.
        judge_action = result.judge_output.next_action
        is_must_deliver = (
            str(judge_action) in _MUST_DELIVER_JUDGE_ACTIONS
        )
        if (
            not is_must_deliver
            and self._user_resumed_speaking_after(original_callback_wall)
        ):
            now_wall = time.time()
            staleness_ms = int(
                (now_wall - (current_user_stopped_speaking_at or now_wall)) * 1000
            )
            self._buffer_dropped_text(
                candidate_text=candidate_text,
                turn_id=turn_id,
                stopped_speaking_at=current_user_stopped_speaking_at,
                staleness_ms=staleness_ms,
            )
            return

        decision = self._state.process_judge_output(
            turn_id=turn_id, judge_output=result.judge_output,
            candidate_utterance_text=candidate_text, elapsed_ms=elapsed_ms,
        )
        self._append_validation_warnings(turn_id=turn_id, decision=decision)

        # Outcome populated by the repeat/stream branches below; used to
        # capture the prior-turn snapshot immediately before TURN_COMPLETED.
        outcome: _SpeakerStreamOutcome

        if decision.speaker_input.instruction_kind == InstructionKind.repeat:
            from app.modules.interview_engine.event_kinds import SPEAKER_CACHED
            from app.modules.interview_engine.audit_events import SpeakerCachedPayload
            cached = decision.cached_utterance or ""
            await agent.session.say(
                cached, allow_interruptions=True, add_to_chat_ctx=False,
            )
            # Repeat branch: the cached utterance was just scheduled with
            # LiveKit, so the body-playback wall-clock is now. The
            # candidate hears this just like a freshly-streamed body, so
            # the pre-body coalescing gate must see a non-None timestamp.
            repeat_body_started_wall_at = time.time()
            self._append(SPEAKER_CACHED, SpeakerCachedPayload(
                turn_id=turn_id, instruction_kind="repeat",
                source_turn_id=decision.cached_source_turn_id or "",
                final_utterance=cached,
            ).model_dump())
            outcome = _SpeakerStreamOutcome(
                final_text=cached,
                interrupted=False,
                sub_context="default",
                body_started_wall_at=repeat_body_started_wall_at,
            )
        else:
            self._append_speaker_input(turn_id=turn_id, speaker_input=decision.speaker_input)
            outcome = await self._stream_speaker_and_say(
                agent=agent, turn_id=turn_id,
                speaker_input=decision.speaker_input,
            )

        # Tick lifecycle elapsed-time so the published
        # ``time_remaining_seconds`` attribute reflects the most recent
        # elapsed wall-clock — without this the frontend timer is stuck
        # at the initial budget and never counts down.
        self._state.set_time_elapsed(self._elapsed_ms() / 1000.0)

        await self._publish_attributes(
            turn_id=turn_id,
            current_question_index=self._state.queue_snapshot().active_index,
            time_remaining_seconds=int(
                self._state.lifecycle_snapshot().time_remaining_seconds()
            ),
        )

        # Capture the prior-turn snapshot so the next turn can consult it
        # for Continuation Coalescing. Must be called before TURN_COMPLETED
        # so that ``completed_monotonic`` matches the timestamp written
        # to the audit envelope.
        self._capture_prior_turn_snapshot(
            turn_id=turn_id,
            completed_monotonic=time.monotonic(),
            candidate_text=candidate_text,
            instruction_kind=decision.speaker_input.instruction_kind.value,
            sub_context=outcome.sub_context,
            final_text=outcome.final_text,
            interrupted=outcome.interrupted,
            body_started_wall_at=outcome.body_started_wall_at,
        )

        self._append(TURN_COMPLETED, TurnCompletedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            duration_ms=self._elapsed_ms() - elapsed_ms,
        ).model_dump())

        # If processing this turn caused the lifecycle to transition to
        # ``closing`` (e.g. polite_close, end_session, or knockout-policy
        # override), schedule the actual LiveKit session shutdown. Drain
        # is True so the closing speech finishes playing before the
        # connection terminates.
        new_state = self._state.lifecycle_snapshot().state.value
        if new_state == "closing" and not self._shutdown_scheduled:
            self._schedule_shutdown(agent)

    async def on_close(
        self, agent: Any, audio_tuning_summary: dict | None,
    ) -> "SessionResult":
        from app.modules.interview_runtime import SessionResult
        from datetime import datetime, timezone

        ledger = self._state.ledger_snapshot()
        queue = self._state.queue_snapshot()
        claims = self._state.claims_snapshot()
        lifecycle = self._state.lifecycle_snapshot()

        questions_asked = sum(
            1 for q in queue.questions
            if q.main_asked_at_turn is not None
        )
        total_probes = sum(len(q.probes_asked_ids) for q in queue.questions)
        duration = (time.monotonic() - (self._session_started_monotonic or time.monotonic()))

        # Phase 9.3 — session-level rollups derived from QuestionState +
        # SignalLedger. Lets the (future) Report Builder LLM consume one
        # session-level signal without re-walking the per-question queue.
        # Per-question detail (push_back_count, quality_observations) is
        # preserved on QuestionState for fine-grained scoring.
        push_back_total = sum(q.push_back_count for q in queue.questions)
        # A question whose advance was cap-forced is one where:
        # the candidate triggered push_back_count >= 2 AND the question
        # ended up completed/skipped (status != active/pending). The cap
        # rule (state/engine.py) is "downgrade to advance once count
        # reaches 2 and a 3rd push_back arrives" — by the time the queue
        # snapshot lands, count >= 2 + completed/skipped is the deterministic
        # marker for a cap-forced exit.
        cap_forced_advance_count = sum(
            1 for q in queue.questions
            if q.push_back_count >= 2 and q.status.value in ("completed", "skipped")
        )
        quality_distribution: dict[str, int] = {}
        for q in queue.questions:
            for grade, count in q.quality_observations.items():
                quality_distribution[grade] = (
                    quality_distribution.get(grade, 0) + count
                )

        return SessionResult(
            session_id=self._cfg.session_id,
            job_title=self._cfg.job_title,
            stage_id=self._cfg.stage.stage_id,
            stage_type=self._cfg.stage.stage_type,
            candidate_name=self._cfg.candidate.name,
            duration_seconds=max(0.0, duration),
            questions_asked=questions_asked,
            questions_skipped=0,  # locked: structured agent never skips
            total_probes_fired=total_probes,
            full_transcript=self._state.transcript_snapshot(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            knockout_failures=lifecycle.knockout_failures,
            audio_tuning_summary=audio_tuning_summary,
            signal_ledger=ledger,
            question_queue=queue,
            claims_pool=claims,
            audit_envelope_ref=None,  # set by entrypoint after sink.write()
            push_back_total=push_back_total,
            cap_forced_advance_count=cap_forced_advance_count,
            quality_distribution=quality_distribution,
        )

    # --- Internals ---

    _RECOVERY_TEXT = "I apologize — could you say that again?"

    def _format_session_ended_message(self) -> str:
        """Render the canned terminal message with candidate-name interpolation.

        When ``candidate.name`` is empty, the placeholder is removed and any
        leading "Thanks for your time, ." artifact is cleaned up so the
        candidate hears a grammatical sentence regardless of name presence.
        """
        template = self._config.session_ended_message
        name = (self._cfg.candidate.name or "").strip()
        msg = template.format(candidate_name=name)
        # Clean up artifacts when name is empty.
        msg = msg.replace(", .", ".").replace(",  ", " ").replace(" ,", "")
        return msg.strip()

    async def _handle_post_close_turn(
        self, *, agent: Any, candidate_text: str,
    ) -> None:
        """Hard-stop branch: lifecycle is closing/closed and the candidate
        spoke. Bypass Judge / State Engine / Speaker entirely; play the
        canned terminal message and ensure shutdown is scheduled.

        Records a TURN_STARTED → SESSION_TERMINAL_DELIVERED →
        TURN_COMPLETED triplet so the audit envelope shows exactly what
        the candidate heard after the session ended.
        """
        turn_id = str(uuid.uuid4())
        self._turn_index += 1
        elapsed_ms = self._elapsed_ms()

        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=candidate_text, stt_text_used=candidate_text,
        ).model_dump())

        message = self._format_session_ended_message()
        lifecycle_snap = self._state.lifecycle_snapshot()

        # Try to play the canned message. The LiveKit session may already
        # be shutting down (drain in flight), so guard the call — we
        # still want the audit event for forensic completeness.
        try:
            await agent.session.say(
                message, allow_interruptions=False, add_to_chat_ctx=True,
            )
        except Exception as exc:  # noqa: BLE001
            structlog.get_logger().warning(
                "interview_engine.terminal_say_failed",
                error_class=type(exc).__name__,
                error_message=str(exc)[:200],
            )

        self._append(SESSION_TERMINAL_DELIVERED, SessionTerminalDeliveredPayload(
            turn_id=turn_id,
            lifecycle_state=lifecycle_snap.state.value,  # type: ignore[arg-type]
            lifecycle_outcome=(
                lifecycle_snap.last_outcome.value
                if lifecycle_snap.last_outcome else None
            ),
            message=message,
        ).model_dump())

        # Ensure shutdown is scheduled (idempotent).
        if not self._shutdown_scheduled:
            self._schedule_shutdown(agent)

        self._append(TURN_COMPLETED, TurnCompletedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            duration_ms=self._elapsed_ms() - elapsed_ms,
        ).model_dump())

    def _schedule_shutdown(self, agent: Any) -> None:
        """Schedule the LiveKit session to shut down. Idempotent.

        ``AgentSession.shutdown(drain=True)`` is itself non-blocking:
        it schedules drain in the background and returns ``None``.
        Wrapping it in ``asyncio.create_task`` raises
        ``TypeError: a coroutine was expected, got None`` and pollutes
        the framework's post-turn pipeline with an exception. Call it
        directly. (See LiveKit docs: /agents/server/job/ →
        "Ending the session".)
        """
        if self._shutdown_scheduled:
            return
        self._shutdown_scheduled = True
        agent.session.shutdown(drain=True)

    async def _stream_speaker_and_say(
        self, *, agent: Any, turn_id: str, speaker_input: Any,
    ) -> _SpeakerStreamOutcome:
        """Run the Speaker LLM + TTS pipeline. Single utterance per turn.

        Returns a :class:`_SpeakerStreamOutcome` so callers can capture
        the prior-turn snapshot without re-deriving sub-context or
        scraping timestamps from audit events. Field semantics:

        * ``final_text`` — utterance actually produced (empty string for
          interrupted, empty-output, or error paths).
        * ``interrupted`` — True only when the candidate's voice
          cancelled the in-flight TTS stream.
        * ``sub_context`` — string discriminator derived from
          ``speaker_input``; used by Continuation Coalescing and audit
          replay.
        * ``body_started_wall_at`` — wall-clock seconds when the
          Speaker's TTS playback was scheduled with LiveKit. ``None``
          on code paths that never reached playback (interrupted /
          empty / error).
        """
        sub_context = _derive_sub_context(speaker_input)

        try:
            handle = await self._speaker.stream(
                turn_id=turn_id,
                speaker_input=speaker_input,
                correlation_id=self._correlation_id,
                tenant_id=self._tenant_id,
            )
            stream = handle.stream()
            speech_handle = await agent.session.say(
                stream, allow_interruptions=True, add_to_chat_ctx=True,
            )
            # Wall-clock at which the body's audio playback was scheduled
            # with LiveKit. Used by the pre-body coalescing gate (see
            # ``_should_coalesce``) to detect candidate utterances that
            # ended before the body became audible. This is the
            # scheduling time, not the first-audio-frame time — TTS TTFB
            # (~200-500ms) means actual audio plays slightly later,
            # which makes the discriminator conservative in the safe
            # direction.
            body_started_wall_at = time.time()
            final_text = await handle.final_text()

            if not final_text.strip():
                # Distinguish "candidate interrupted before the LLM
                # produced output" (don't talk back over them) from
                # "true empty output" (play a deterministic fallback).
                # LiveKit's SpeechHandle.interrupted is the signal: True
                # means the candidate's voice cancelled the in-flight
                # TTS pipeline mid-stream, which also cancels the
                # upstream LLM stream we were consuming.
                if _was_interrupted(speech_handle):
                    interrupted_text = await self._handle_interrupted_speaker(
                        turn_id=turn_id,
                        speaker_input=speaker_input, handle=handle,
                    )
                    return _SpeakerStreamOutcome(
                        final_text=interrupted_text,
                        interrupted=True,
                        sub_context=sub_context,
                        body_started_wall_at=None,
                    )
                empty_text = await self._handle_empty_speaker_output(
                    agent=agent, turn_id=turn_id,
                    speaker_input=speaker_input, handle=handle,
                )
                return _SpeakerStreamOutcome(
                    final_text=empty_text,
                    interrupted=False,
                    sub_context=sub_context,
                    body_started_wall_at=None,
                )

            self._append(SPEAKER_CALL, SpeakerCallPayload(
                turn_id=turn_id, model="speaker",
                prompt_hash=handle.prompt_hash,
                instruction_kind=speaker_input.instruction_kind.value,
                bank_text_present=speaker_input.bank_text is not None,
                latency_ms_first_token=handle.latency_ms_first_token,
                latency_ms_total=handle.latency_ms_total,
                usage=handle.usage, final_utterance=final_text,
            ).model_dump())
            self._append(SPEAKER_OUTPUT, SpeakerOutputPayload(
                turn_id=turn_id, final_utterance=final_text,
            ).model_dump())

            # register_agent_utterance is transcript-only;
            # register_agent_question_for_repeat does the cache update
            # with the empty-text + non-question-kind guards.
            self._state.register_agent_utterance(
                turn_id=turn_id, text=final_text,
                instruction_kind=speaker_input.instruction_kind,
            )
            self._state.register_agent_question_for_repeat(
                turn_id=turn_id, text=final_text,
                instruction_kind=speaker_input.instruction_kind,
            )
            return _SpeakerStreamOutcome(
                final_text=final_text,
                interrupted=False,
                sub_context=sub_context,
                body_started_wall_at=body_started_wall_at,
            )

        except Exception as exc:
            from app.modules.interview_engine.event_kinds import SPEAKER_ERROR
            from app.modules.interview_engine.audit_events import SpeakerErrorPayload
            self._append(SPEAKER_ERROR, SpeakerErrorPayload(
                turn_id=turn_id, model="speaker",
                error_class=type(exc).__name__,
                error_message=str(exc)[:500],
                recovery_utterance=self._RECOVERY_TEXT,
            ).model_dump())
            await agent.session.say(
                self._RECOVERY_TEXT,
                allow_interruptions=True, add_to_chat_ctx=False,
            )
            # Cache intentionally NOT updated — _RECOVERY_TEXT is a
            # generic apology, not the question.
            self._state.register_agent_utterance(
                turn_id=turn_id, text=self._RECOVERY_TEXT,
                instruction_kind=speaker_input.instruction_kind,
            )
            return _SpeakerStreamOutcome(
                final_text=self._RECOVERY_TEXT,
                interrupted=False,
                sub_context=sub_context,
                body_started_wall_at=None,
            )

    async def _handle_interrupted_speaker(
        self, *, turn_id: str, speaker_input: Any, handle: Any,
    ) -> str:
        """The candidate interrupted the Speaker stream before any output
        text was produced. The candidate is talking — DO NOT play a
        fallback (would talk over them). Stay silent, audit the cause,
        and let the next user turn drive the next reply.

        Distinct from ``_handle_empty_speaker_output``, which IS supposed
        to play a fallback for true empty outputs (model decided nothing
        to say without a candidate interruption).

        Returns empty string. The transcript reflects the interruption
        via ``register_agent_utterance(text="", ...)`` so the next
        Judge turn sees "no agent reply happened on the prior turn."
        """
        from app.modules.interview_engine.event_kinds import SPEAKER_INTERRUPTED
        from app.modules.interview_engine.audit_events import SpeakerInterruptedPayload
        self._append(SPEAKER_INTERRUPTED, SpeakerInterruptedPayload(
            turn_id=turn_id,
            instruction_kind=speaker_input.instruction_kind.value,
            event_types_seen=handle.event_types_seen,
            response_id=handle.response_id,
        ).model_dump())
        # Empty agent transcript entry preserves forensic completeness:
        # downstream replay tools see an empty utterance next to the
        # corresponding speaker.interrupted audit event.
        # Cache is intentionally NOT updated here — Phase 9.9 contract
        # (see register_agent_question_for_repeat docstring) — empty/
        # interrupted Speaker turns must not poison the repeat cache.
        self._state.register_agent_utterance(
            turn_id=turn_id, text="",
            instruction_kind=speaker_input.instruction_kind,
        )
        return ""

    async def _handle_empty_speaker_output(
        self, *, agent: Any, turn_id: str, speaker_input: Any, handle: Any,
    ) -> str:
        """The Speaker LLM streamed nothing. Play a deterministic fallback so
        the candidate doesn't hear silence; emit speaker.output.empty for
        audit visibility (Bug D from session 8317142f-3166-...).

        Bypasses the SPEAKER_CALL / SPEAKER_OUTPUT audit events on purpose
        — those describe a successful LLM call. The empty-output condition
        is its own audit kind.

        Phase 9.3: also propagates the handle's diagnostic state (event
        types seen, refusal text, response id, finish reason) into the
        audit payload so we can root-cause the empty output offline.
        """
        from app.modules.interview_engine.event_kinds import SPEAKER_OUTPUT_EMPTY
        from app.modules.interview_engine.audit_events import SpeakerOutputEmptyPayload
        fallback = self._compose_empty_output_fallback(speaker_input)
        await agent.session.say(
            fallback, allow_interruptions=True, add_to_chat_ctx=True,
        )
        self._append(SPEAKER_OUTPUT_EMPTY, SpeakerOutputEmptyPayload(
            turn_id=turn_id,
            instruction_kind=speaker_input.instruction_kind.value,
            fallback_text=fallback,
            event_types_seen=handle.event_types_seen,
            refusal_text=handle.refusal_text,
            response_id=handle.response_id,
            finish_reason=handle.finish_reason,
        ).model_dump())
        # Cache is intentionally NOT updated here (Phase 9.9 contract) —
        # the fallback is a recovery utterance ("Let me restate that.
        # {bank_text}"), not the agent's actual question for repeat
        # purposes. The transcript records the fallback was played.
        self._state.register_agent_utterance(
            turn_id=turn_id, text=fallback,
            instruction_kind=speaker_input.instruction_kind,
        )
        return fallback

    def _compose_empty_output_fallback(self, speaker_input: Any) -> str:
        """Deterministic, no LLM. Restates bank_text when available;
        otherwise a generic re-ask."""
        if speaker_input.bank_text:
            return f"Let me restate that. {speaker_input.bank_text}"
        return "Could you take it from the top?"

    async def _publish_attributes(
        self, *, turn_id: str | None,
        current_question_index: int | None = None,
        total_questions: int | None = None,
        time_remaining_seconds: int | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if total_questions is not None:
            kwargs[ATTR_TOTAL_QUESTIONS] = total_questions
        if current_question_index is not None:
            kwargs[ATTR_CURRENT_QUESTION_INDEX] = current_question_index
        if time_remaining_seconds is not None:
            kwargs[ATTR_TIME_REMAINING_SECONDS] = time_remaining_seconds
        pushed = await self._attr.publish(**kwargs)
        for k, v in pushed.items():
            self._append(FRONTEND_ATTRIBUTE_PUBLISHED, FrontendAttributePayload(
                turn_id=turn_id, attribute_name=k, value=v,
            ).model_dump())

    def _maybe_coalesce(
        self,
        *,
        current_turn_id: str,
        candidate_text: str,
        now_monotonic: float,
        current_user_stopped_speaking_at: float | None = None,
    ) -> str:
        """Apply Continuation Coalescing if eligible. Returns the candidate
        text the Judge should see — either ``candidate_text`` unchanged or
        the prior turn's text prepended.

        ``current_user_stopped_speaking_at`` is the wall-clock timestamp at
        which the candidate stopped speaking on this new turn, sourced from
        LiveKit's ``ChatMessage.metrics.stopped_speaking_at``. Defaults to
        ``None`` for callers (tests) that don't supply it; the pre-body
        coalescing gate then degrades gracefully to the original behavior.

        The silence-aware window reference is read from
        ``self._last_user_speech_end_monotonic`` (populated by
        :meth:`observe_user_state`).

        When coalescing fires:
        * Emits a ``turn.coalesced`` audit event with the full merge context.
        * Clears ``self._last_turn`` so a third consecutive fragment doesn't
          double-merge. The new turn's outcome will repopulate
          ``self._last_turn`` for the next decision.

        See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
        """
        decision = _should_coalesce(
            prior=self._last_turn,
            now_monotonic=now_monotonic,
            coalesce_enabled=self._config.coalesce_enabled,
            coalesce_window_ms=self._config.coalesce_window_ms,
            current_user_stopped_speaking_at=current_user_stopped_speaking_at,
            last_user_speech_end_monotonic=self._last_user_speech_end_monotonic,
        )
        if not decision.should:
            return candidate_text

        # decision.should is True implies self._last_turn is not None — invariant
        # from _should_coalesce. Pull it out so type-checkers and readers see it.
        prior = self._last_turn
        assert prior is not None
        gap_ms = int((now_monotonic - prior.completed_monotonic) * 1000)
        # silence_gap_ms is non-None ONLY when the silence-aware reference
        # was load-bearing (i.e., more recent than the prior turn's
        # completion). Otherwise the original gap_ms field tells the full
        # story. This keeps the audit payload self-documenting about which
        # reference was used without breaking the existing gap_ms semantic.
        silence_gap_ms: int | None = None
        last_silence = self._last_user_speech_end_monotonic
        if last_silence is not None and last_silence > prior.completed_monotonic:
            silence_gap_ms = int((now_monotonic - last_silence) * 1000)
        combined_text = prior.candidate_text + " " + candidate_text

        self._append(TURN_COALESCED, TurnCoalescedPayload(
            prior_turn_id=prior.turn_id,
            current_turn_id=current_turn_id,
            prior_text=prior.candidate_text,
            current_text=candidate_text,
            combined_text=combined_text,
            prior_instruction_kind=prior.instruction_kind,
            prior_sub_context=prior.sub_context,
            gap_ms=gap_ms,
            silence_gap_ms=silence_gap_ms,
            coalesce_window_ms=self._config.coalesce_window_ms,
            reason=decision.reason,  # type: ignore[arg-type]
        ).model_dump())

        # Clear so a third fragment doesn't double-merge. The new turn's
        # _capture_prior_turn_snapshot call at end-of-turn repopulates this.
        self._last_turn = None
        return combined_text

    def _capture_prior_turn_snapshot(
        self,
        *,
        turn_id: str,
        completed_monotonic: float,
        candidate_text: str,
        instruction_kind: str,
        sub_context: str,
        final_text: str,
        interrupted: bool,
        body_started_wall_at: float | None,
    ) -> None:
        """Record the just-finished turn so the next turn can consult it for
        Continuation Coalescing.

        ``speaker_emitted_content`` is True iff the Speaker produced
        non-whitespace output AND was not interrupted — i.e., the candidate
        actually heard the agent. Cache-replay branches (``repeat``) pass
        the cached utterance as ``final_text`` with ``interrupted=False``,
        so they correctly count as delivered.

        ``body_started_wall_at`` is the wall-clock timestamp when the body
        audio was scheduled with LiveKit, or ``None`` when no body played
        (interrupted/empty/error paths). Used by the pre-body coalescing
        gate (see ``_should_coalesce``).

        See ``docs/superpowers/specs/2026-05-11-turn-continuation-coalescing-design.md``.
        """
        delivered = (not interrupted) and bool(final_text.strip())
        self._last_turn = _PriorTurnSnapshot(
            turn_id=turn_id,
            completed_monotonic=completed_monotonic,
            candidate_text=candidate_text,
            instruction_kind=instruction_kind,
            sub_context=sub_context,
            speaker_emitted_content=delivered,
            body_started_wall_at=body_started_wall_at,
        )

    def _append(self, kind: str, payload: dict) -> None:
        wall_ms = int(time.time() * 1000)
        self._collector.append(kind=kind, payload=payload, wall_ms=wall_ms)

    def _elapsed_ms(self) -> int:
        if self._session_started_monotonic is None:
            return 0
        return int((time.monotonic() - self._session_started_monotonic) * 1000)

    def _append_judge_event(
        self, *, turn_id: str, result: Any, input_payload: Any,
    ) -> None:
        """Emit JUDGE_FALLBACK or JUDGE_CALL with the full input payload.

        ``input_payload`` is the JudgeInputPayload that was sent to the
        LLM — its ``model_dump(mode='json')`` populates ``input_summary``
        so replay tools can reproduce why the Judge made a given decision.
        """
        from app.modules.interview_engine.event_kinds import JUDGE_CALL, JUDGE_FALLBACK
        from app.modules.interview_engine.audit_events import (
            JudgeCallPayload, JudgeFallbackPayload,
        )
        if result.is_fallback:
            self._append(JUDGE_FALLBACK, JudgeFallbackPayload(
                turn_id=turn_id, reason=result.fallback_reason.value,
                original_failure_context=result.original_failure_context or {},
                synthesized_output=result.judge_output.model_dump(mode="json"),
            ).model_dump())
        else:
            self._append(JUDGE_CALL, JudgeCallPayload(
                turn_id=turn_id, model=result.model_used,
                prompt_hash="sha256:judge",
                input_summary=input_payload.model_dump(mode="json"),
                output=result.judge_output.model_dump(mode="json"),
                latency_ms=result.latency_ms,
                usage=result.usage,
            ).model_dump())

    def _append_state_snapshot(self, *, turn_id: str) -> None:
        """Emit a state.snapshot audit event capturing State Engine state
        BEFORE process_judge_output mutates it.

        Lets replay tools reconstruct any turn's input state to the State
        Engine: the queue (active question, push_back/dont_know counts,
        probes_remaining_ids), the ledger (per-signal coverage), the
        claims pool, and the lifecycle (state, knockout_failures, time
        remaining).
        """
        from app.modules.interview_engine.event_kinds import STATE_SNAPSHOT
        from app.modules.interview_engine.audit_events import StateSnapshotPayload
        self._append(STATE_SNAPSHOT, StateSnapshotPayload(
            turn_id=turn_id,
            ledger=self._state.ledger_snapshot().model_dump(mode="json"),
            queue=self._state.queue_snapshot().model_dump(mode="json"),
            claims=self._state.claims_snapshot().model_dump(mode="json"),
            lifecycle=self._state.lifecycle_snapshot().model_dump(mode="json"),
        ).model_dump())

    def _append_speaker_input(
        self, *, turn_id: str, speaker_input: Any,
    ) -> None:
        """Emit a speaker.input audit event capturing exactly what the
        Speaker LLM is about to receive.

        Lets us audit anti-leak after-the-fact (no rubric / anchors /
        coverage / signal_metadata in the payload) and reproduce why the
        Speaker said what it said.
        """
        from app.modules.interview_engine.event_kinds import SPEAKER_INPUT
        from app.modules.interview_engine.audit_events import SpeakerInputPayload
        self._append(SPEAKER_INPUT, SpeakerInputPayload(
            turn_id=turn_id,
            speaker_input=speaker_input.model_dump(mode="json"),
        ).model_dump())

    def _append_validation_warnings(self, *, turn_id: str, decision: Any) -> None:
        from app.modules.interview_engine.event_kinds import JUDGE_VALIDATION
        from app.modules.interview_engine.audit_events import JudgeValidationPayload
        for w in decision.validation_warnings:
            self._append(JUDGE_VALIDATION, JudgeValidationPayload(
                turn_id=turn_id, level=w.level,
                code=w.code, details=w.details,
            ).model_dump())

    async def maybe_checkpoint(self, *, db: Any) -> bool:
        """Write engine_checkpoint if cadence threshold reached. Returns True if written."""
        if not hasattr(self, "_last_checkpoint_turn"):
            self._last_checkpoint_turn = -1
            self._last_checkpoint_monotonic = self._session_started_monotonic or time.monotonic()
        turns_since = self._turn_index - self._last_checkpoint_turn
        seconds_since = time.monotonic() - self._last_checkpoint_monotonic
        if (
            turns_since < self._config.checkpoint_turns
            and seconds_since < self._config.checkpoint_seconds
        ):
            return False
        checkpoint = self._state.to_checkpoint(
            last_audit_seq_flushed=len(self._collector.events),
            captured_at_ms=int(time.time() * 1000),
        )
        from sqlalchemy import update
        from app.modules.session.models import Session
        await db.execute(
            update(Session)
            .where(Session.id == self._cfg.session_id)
            .values(engine_checkpoint=checkpoint.model_dump(mode="json"))
        )
        await db.commit()
        from app.modules.interview_engine.event_kinds import CHECKPOINT_WRITTEN
        from app.modules.interview_engine.audit_events import CheckpointWrittenPayload
        self._append(CHECKPOINT_WRITTEN, CheckpointWrittenPayload(
            turn_id="",
            last_audit_seq_flushed=checkpoint.last_audit_seq_flushed,
            captured_at_ms=checkpoint.captured_at_ms,
        ).model_dump())
        self._last_checkpoint_turn = self._turn_index
        self._last_checkpoint_monotonic = time.monotonic()
        return True
