"""InterviewOrchestrator — drives the per-turn pipeline.

This is the LiveKit hook surface. on_enter delivers the first question via a
synthesized JudgeOutput. on_user_turn_completed runs Judge → State Engine →
Speaker on each candidate turn. on_close builds the SessionResult.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from livekit.agents import StopResponse

from app.modules.interview_engine.audit_events import (
    FrontendAttributePayload, JudgeSyntheticPayload,
    NaturalnessFlags,
    SessionTerminalDeliveredPayload,
    SpeakerCallPayload, SpeakerOutputPayload,
    StateSnapshotCommittedPayload, StateSnapshotRestoredPayload,
    StateSnapshotTakenPayload,
    TurnAbortedForContinuationPayload, TurnCompletedPayload,
    TurnLoopGuardFiredPayload, TurnStartedPayload,
    TurnStitchedContinuationPayload,
)
from app.modules.interview_engine.event_kinds import (
    FRONTEND_ATTRIBUTE_PUBLISHED, JUDGE_SYNTHETIC,
    SESSION_TERMINAL_DELIVERED,
    SPEAKER_CALL, SPEAKER_OUTPUT,
    STATE_SNAPSHOT_COMMITTED, STATE_SNAPSHOT_RESTORED, STATE_SNAPSHOT_TAKEN,
    TURN_ABORTED_FOR_CONTINUATION, TURN_COMPLETED, TURN_LOOP_GUARD_FIRED,
    TURN_STARTED, TURN_STITCHED_CONTINUATION,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_TIME_REMAINING_SECONDS,
    ATTR_TOTAL_QUESTIONS, AttributePublisher,
)
from app.modules.interview_engine.judge.service import JudgeService
from app.modules.interview_engine.models.judge import (
    JudgeOutput, NextAction, RepeatPayload, TurnMetadata,
)
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

# ---------------------------------------------------------------------------
# v2: Orchestrator pre-filter for `repeat` intent (Cluster 6, spec §10)
# ---------------------------------------------------------------------------

_REPEAT_PATTERN = re.compile(
    r"\b("
    r"repeat (that|it|the question|please)"
    r"|say (that|it) again"
    r"|what (was|did you say) (that|the question) again"
    r"|one more time"
    r"|sorry,? again\??"
    r"|come again\??"
    r")\b",
    re.IGNORECASE,
)

_REPEAT_DECLINE_PATTERN = re.compile(
    r"\b(explain|rephrase|what do you mean|i don't understand)\b",
    re.IGNORECASE,
)


def _maybe_synthesize_repeat(utterance: str) -> JudgeOutput | None:
    """Pre-filter for `repeat` intent.

    Returns a synthetic JudgeOutput when the candidate clearly asked to
    hear the last agent utterance again. Returns None for everything
    else, in which case the Judge runs normally.

    Negative guards (return None even on match):
    - utterance also contains "explain" / "rephrase" / "what do you
      mean" / "I don't understand" → that's clarify, not repeat (Judge
      prompt §1 tie-breaker preserved).
    - utterance is > 40 words → unlikely a pure repeat request; let the
      Judge see it.

    Spec: docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md §10.
    """
    if not utterance:
        return None
    if len(utterance.split()) > 40:
        return None
    if _REPEAT_DECLINE_PATTERN.search(utterance):
        return None
    if not _REPEAT_PATTERN.search(utterance):
        return None
    return JudgeOutput(
        reasoning=(
            "Pre-filter: candidate asked to hear the last turn again. "
            "Deterministic intent classification — Judge skipped."
        ),
        observations=[],
        candidate_claims=[],
        next_action=NextAction.repeat,
        next_action_payload=RepeatPayload(),
        turn_metadata=TurnMetadata(),
    )


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
class _SpeakerStreamOutcome:
    """Return shape for ``_stream_speaker_and_say``."""
    final_text: str
    interrupted: bool


@dataclass(slots=True)
class OrchestratorConfig:
    checkpoint_turns: int = 10
    checkpoint_seconds: int = 30
    # Canned terminal message override. None = use PersonaSpec.fallback_session_ended
    # (Arjun-voiced default). Populated from settings.engine_session_ended_message
    # at agent startup; tests leave it None to exercise the PersonaSpec path.
    session_ended_message: str | None = None

    # Conversational continuation — pre-Speaker cancellation watcher.
    # See docs/superpowers/specs/2026-05-17-conversational-continuation-design.md
    #
    # continuation_enabled: kill switch. When False the orchestrator
    #   skips the snapshot/watcher entirely and behaves like the
    #   pre-2026-05-17 code path.
    # continuation_min_word_count: noise filter — STT-final transcripts
    #   below this word count are treated as filler ("uh", "okay") and
    #   do not trigger the abort path. Default of 2 matches LiveKit's
    #   adaptive-interruption ``min_words`` convention.
    #
    #   Switched from VAD-based (user_state speaking sustained N ms) to
    #   STT-based (user_input_transcribed is_final=True with N words) on
    #   2026-05-17 after session 7970e91c showed VAD-triggered aborts on
    #   non-speech sounds. STT confirms the candidate actually produced
    #   recognizable content before we discard a turn.
    # continuation_consecutive_abort_cap: safety bound. After this many
    #   consecutive aborts on the same conceptual turn, the watcher is
    #   skipped to guarantee forward progress.
    continuation_enabled: bool = True
    continuation_min_word_count: int = 2
    continuation_consecutive_abort_cap: int = 3


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

        # Conversational-continuation state (2026-05-17 design).
        # _pending_continuation_text: prior aborted turn's candidate text.
        #   Stitched at the top of the NEXT on_user_turn_completed and
        #   cleared. Single nullable string — there is no queue.
        # _consecutive_aborts: counter, incremented on each abort, reset
        #   on each commit. When it reaches
        #   config.continuation_consecutive_abort_cap the watcher is
        #   skipped for the next turn to guarantee forward progress.
        # _last_abort_elapsed_ms: timestamp of the most recent abort,
        #   used to populate the gap_ms field on the TURN_STITCHED
        #   audit event. None when no prior abort exists.
        self._pending_continuation_text: str | None = None
        self._consecutive_aborts: int = 0
        self._last_abort_elapsed_ms: int | None = None

        # Tracks the previous turn's Speaker output text so naturalness
        # flag detection can flag name-overuse across turns. None on
        # first turn / after empty/interrupted output.
        self._prior_speaker_output: str | None = None

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
        """Top-level per-turn hook.

        Two layers wrapped around the actual Judge → State → Speaker
        pipeline (which lives in :meth:`_run_turn_pipeline`):

        1. **Early returns** — empty text or lifecycle in closing/closed
           bypass everything and either no-op or play the canned terminal
           message.

        2. **Continuation control** (2026-05-17 design) — when enabled,
           stitch any pending continuation text into the candidate input,
           snapshot the State Engine, run the pipeline under a
           cancellation watcher, and either commit or restore-and-abort
           based on whether the candidate resumed speaking before the
           first TTS audio frame.

        Returns normally on success (commit), raises ``StopResponse``
        on abort (the framework keeps listening for the merged text).
        """
        # No StopResponse here on the happy path — see
        # StructuredInterviewAgent docstring. Returning normally lets the
        # framework auto-append new_message to chat_ctx, which fires
        # conversation_item_added and populates LiveKit's chat_history.
        # The agent's llm_node override yields nothing, so no duplicate
        # LLM reply is generated.
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

        # --- Stitch pending continuation (2026-05-17) ----------------------
        # On a prior abort, the candidate's text was buffered. Prepend it
        # here so the merged utterance reaches the Judge as one input.
        # Cleared unconditionally — there is no re-buffer path.
        stitch_payload: TurnStitchedContinuationPayload | None = None
        prior_chars = 0
        if self._pending_continuation_text is not None:
            prior_chars = len(self._pending_continuation_text)
            current_chars = len(candidate_text)
            candidate_text = self._pending_continuation_text + " " + candidate_text
            # gap_ms: time since the prior abort. Falls back to 0 when
            # we have no abort timestamp (e.g. test pre-seeded the buffer).
            gap_ms = 0
            if self._last_abort_elapsed_ms is not None:
                gap_ms = max(0, self._elapsed_ms() - self._last_abort_elapsed_ms)
            stitch_payload = TurnStitchedContinuationPayload(
                turn_id="",  # filled below after turn_id is allocated
                prior_chars=prior_chars,
                current_chars=current_chars,
                combined_chars=len(candidate_text),
                gap_ms=gap_ms,
            )
            self._pending_continuation_text = None
            self._last_abort_elapsed_ms = None

        # --- Continuation-disabled fast path -------------------------------
        if not self._config.continuation_enabled:
            await self._run_turn_pipeline_unwatched(
                agent=agent, candidate_text=candidate_text,
                stitch_payload=stitch_payload,
            )
            return

        # --- Continuation-enabled path -------------------------------------
        turn_id = str(uuid.uuid4())
        self._turn_index += 1
        elapsed_ms = self._elapsed_ms()
        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=candidate_text, stt_text_used=candidate_text,
        ).model_dump())
        if stitch_payload is not None:
            stitch_payload = stitch_payload.model_copy(update={"turn_id": turn_id})
            self._append(
                TURN_STITCHED_CONTINUATION, stitch_payload.model_dump(),
            )
        self._append_state_snapshot(turn_id=turn_id)

        # Snapshot the State Engine. All in-turn mutations to self._state
        # below this line are reversible via restore_from(snapshot) on
        # the abort path.
        snapshot = self._state.snapshot_full()
        active_index = snapshot.queue.active_index
        self._append(STATE_SNAPSHOT_TAKEN, StateSnapshotTakenPayload(
            turn_id=turn_id,
            transcript_entries=len(snapshot.transcript),
            queue_active_index=active_index,
        ).model_dump())

        # Loop guard — after `cap` consecutive aborts on the same
        # conceptual turn, commit no-matter-what to guarantee forward
        # progress. The watcher is bypassed; the snapshot is still taken
        # so the audit trail is consistent.
        skip_watcher = (
            self._consecutive_aborts
            >= self._config.continuation_consecutive_abort_cap
        )
        if skip_watcher:
            self._append(TURN_LOOP_GUARD_FIRED, TurnLoopGuardFiredPayload(
                turn_id=turn_id,
                consecutive_aborts=self._consecutive_aborts,
            ).model_dump())
            await self._run_turn_body(
                agent=agent, turn_id=turn_id,
                candidate_text=candidate_text, elapsed_ms=elapsed_ms,
            )
            self._append(STATE_SNAPSHOT_COMMITTED, StateSnapshotCommittedPayload(
                turn_id=turn_id,
            ).model_dump())
            self._consecutive_aborts = 0
            return

        # --- Watcher race ---------------------------------------------------
        #
        # Trigger model (2026-05-17, updated after session 7970e91c):
        #   * ``cancel_event`` fires when a ``user_input_transcribed`` event
        #     arrives with ``is_final=True`` AND a transcript word count
        #     >= ``continuation_min_word_count``. STT-final means the
        #     candidate produced recognizable speech; the word-count gate
        #     filters out brief interjections ("uh", "okay") that don't
        #     warrant aborting an in-flight turn.
        #   * ``commit_event`` fires when ``agent_state_changed`` reports
        #     ``new_state == "speaking"`` — the first audible TTS frame.
        #     After that the framework's adaptive interruption handles
        #     candidate speech, so we disengage.
        #
        # The previous VAD-based trigger (user_state speaking sustained for
        # 500ms) fired on non-speech sounds and on the candidate's natural
        # short-utterance follow-ups, leading to spurious aborts.
        commit_event = asyncio.Event()
        cancel_event = asyncio.Event()

        def _on_user_input_transcribed(ev: Any) -> None:
            if commit_event.is_set():
                return
            if not getattr(ev, "is_final", False):
                return
            transcript = getattr(ev, "transcript", "") or ""
            if not transcript.strip():
                return
            word_count = len(transcript.split())
            if word_count < self._config.continuation_min_word_count:
                return
            cancel_event.set()

        def _on_agent_state(ev: Any) -> None:
            new_state = getattr(ev, "new_state", None)
            if new_state == "speaking":
                commit_event.set()

        agent.session.on("user_input_transcribed", _on_user_input_transcribed)
        agent.session.on("agent_state_changed", _on_agent_state)

        turn_task = asyncio.create_task(self._run_turn_body(
            agent=agent, turn_id=turn_id,
            candidate_text=candidate_text, elapsed_ms=elapsed_ms,
        ))
        cancel_wait_task = asyncio.create_task(cancel_event.wait())

        try:
            await asyncio.wait(
                {turn_task, cancel_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Abort path: watcher fired AND we haven't committed yet.
            if cancel_event.is_set() and not commit_event.is_set():
                turn_task.cancel()
                try:  # noqa: SIM105
                    await turn_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                # Best-effort: cancel any TTS that may have started but
                # not yet played audibly. session.interrupt() is a sync
                # method on the real AgentSession that returns None.
                try:  # noqa: SIM105
                    agent.session.interrupt()
                except Exception:  # noqa: BLE001
                    pass
                self._state.restore_from(snapshot)
                self._append(STATE_SNAPSHOT_RESTORED, StateSnapshotRestoredPayload(
                    turn_id=turn_id,
                ).model_dump())
                self._consecutive_aborts += 1
                self._pending_continuation_text = candidate_text
                self._last_abort_elapsed_ms = elapsed_ms
                self._append(
                    TURN_ABORTED_FOR_CONTINUATION,
                    TurnAbortedForContinuationPayload(
                        turn_id=turn_id,
                        phase="judge",
                        elapsed_ms=max(0, self._elapsed_ms() - elapsed_ms),
                        text_chars=len(candidate_text),
                        consecutive_aborts=self._consecutive_aborts,
                    ).model_dump(),
                )
                raise StopResponse()

            # Commit path: turn_task finished first. Drain any exception
            # the body may have raised.
            cancel_wait_task.cancel()
            try:  # noqa: SIM105
                await cancel_wait_task
            except asyncio.CancelledError:
                pass
            await turn_task  # propagates exceptions from the body

            self._append(STATE_SNAPSHOT_COMMITTED, StateSnapshotCommittedPayload(
                turn_id=turn_id,
            ).model_dump())
            self._consecutive_aborts = 0
        finally:
            agent.session.off("user_input_transcribed", _on_user_input_transcribed)
            agent.session.off("agent_state_changed", _on_agent_state)

    async def _run_turn_pipeline_unwatched(
        self, *, agent: Any, candidate_text: str,
        stitch_payload: TurnStitchedContinuationPayload | None,
    ) -> None:
        """Legacy / kill-switch path — run the turn pipeline without
        snapshot or watcher. Identical to the pre-2026-05-17 code path.

        Used when ``config.continuation_enabled=False``.
        """
        turn_id = str(uuid.uuid4())
        self._turn_index += 1
        elapsed_ms = self._elapsed_ms()
        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=candidate_text, stt_text_used=candidate_text,
        ).model_dump())
        if stitch_payload is not None:
            stitch_payload = stitch_payload.model_copy(update={"turn_id": turn_id})
            self._append(
                TURN_STITCHED_CONTINUATION, stitch_payload.model_dump(),
            )
        self._append_state_snapshot(turn_id=turn_id)
        await self._run_turn_body(
            agent=agent, turn_id=turn_id,
            candidate_text=candidate_text, elapsed_ms=elapsed_ms,
        )

    async def _run_turn_body(
        self, *, agent: Any, turn_id: str,
        candidate_text: str, elapsed_ms: int,
    ) -> None:
        """The Judge → State → Speaker turn body.

        Pure code motion from the previous in-line on_user_turn_completed
        body. Kept here so both the watched and the unwatched code paths
        can drive it. The TURN_STARTED + state-snapshot audit events are
        already emitted by the caller before this runs.
        """

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
                        type=sm.type,
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
            next_pending_question=self._state.next_pending_question(),
            active_signal_metadata=active_signal_meta,
            active_remaining_probes=remaining_probes_dict,
            active_question_push_back_count=active_push_back_count,
            active_question_consecutive_dont_know_count=active_dont_know_count,
        )

        # v2: orchestrator pre-filter for `repeat` intent. Bypasses the Judge
        # call entirely for clear repeat-intent utterances (≤40 words, no mixed
        # "explain"/"rephrase" intent). Emits judge.synthetic instead of
        # judge.call. Spec §10.
        pre_filter_repeat = _maybe_synthesize_repeat(candidate_text or "")
        if pre_filter_repeat is not None:
            self._append(JUDGE_SYNTHETIC, JudgeSyntheticPayload(
                turn_id=turn_id,
                output=pre_filter_repeat.model_dump(mode="json"),
                reason="pre_filter_repeat",
            ).model_dump())
            judge_output_for_state = pre_filter_repeat
        else:
            result = await self._judge.call(
                turn_id=turn_id, input_payload=judge_input,
                correlation_id=self._correlation_id,
                tenant_id=self._tenant_id,
            )
            self._append_judge_event(turn_id=turn_id, result=result, input_payload=judge_input)
            judge_output_for_state = result.judge_output

        decision = self._state.process_judge_output(
            turn_id=turn_id, judge_output=judge_output_for_state,
            candidate_utterance_text=candidate_text, elapsed_ms=elapsed_ms,
        )
        self._append_validation_warnings(turn_id=turn_id, decision=decision)

        if decision.speaker_input.instruction_kind == InstructionKind.repeat:
            from app.modules.interview_engine.event_kinds import SPEAKER_CACHED
            from app.modules.interview_engine.audit_events import SpeakerCachedPayload
            cached = decision.cached_utterance or ""
            await agent.session.say(
                cached, allow_interruptions=True, add_to_chat_ctx=False,
            )
            self._append(SPEAKER_CACHED, SpeakerCachedPayload(
                turn_id=turn_id, instruction_kind="repeat",
                source_turn_id=decision.cached_source_turn_id or "",
                final_utterance=cached,
            ).model_dump())
        else:
            self._append_speaker_input(turn_id=turn_id, speaker_input=decision.speaker_input)
            await self._stream_speaker_and_say(
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

    @property
    def _RECOVERY_TEXT(self) -> str:
        """Persona-voiced recovery text. Read from PersonaSpec so tone
        stays consistent with the rest of the Speaker output even on
        exception paths that bypass the LLM."""
        from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA
        return DEFAULT_PERSONA.fallback_recovery

    def _format_session_ended_message(self) -> str:
        """Render the terminal message.

        Override path (``session_ended_message`` is set): uses the legacy
        ``{candidate_name}`` template and cleans up punctuation artifacts when
        the name is absent.

        Default path (``session_ended_message`` is None): falls back to
        ``PersonaSpec.fallback_session_ended`` — Arjun-voiced, uses
        ``{comma_name}`` which collapses gracefully when no name is present.
        """
        from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA

        name = (self._cfg.candidate.name or "").strip()
        override = self._config.session_ended_message
        if override:
            # Legacy template path — uses {candidate_name}, needs cleanup.
            msg = override.format(candidate_name=name)
            msg = msg.replace(", .", ".").replace(",  ", " ").replace(" ,", "")
            return msg.strip()
        comma_name = f", {name}" if name else ""
        return DEFAULT_PERSONA.fallback_session_ended.format(comma_name=comma_name)

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

        Returns a :class:`_SpeakerStreamOutcome`. Field semantics:

        * ``final_text`` — utterance actually produced (empty string for
          interrupted, empty-output, or error paths).
        * ``interrupted`` — True only when the candidate's voice
          cancelled the in-flight TTS stream.
        """
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
                    )
                empty_text = await self._handle_empty_speaker_output(
                    agent=agent, turn_id=turn_id,
                    speaker_input=speaker_input, handle=handle,
                )
                return _SpeakerStreamOutcome(
                    final_text=empty_text,
                    interrupted=False,
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
            from app.modules.interview_engine.speaker.naturalness import (
                detect_banned_phrases,
                detect_exceeded_soft_target,
                detect_name_overuse,
                detect_repeated_opener,
            )
            flags = NaturalnessFlags(
                repeated_opener=detect_repeated_opener(
                    final_text, speaker_input.recent_reply_starts,
                ),
                banned_phrases_emitted=detect_banned_phrases(final_text),
                name_overuse=detect_name_overuse(
                    final_text,
                    speaker_input.candidate_name,
                    self._prior_speaker_output,
                ),
                exceeded_soft_target=detect_exceeded_soft_target(
                    final_text, speaker_input.instruction_kind.value,
                ),
            )
            self._append(SPEAKER_OUTPUT, SpeakerOutputPayload(
                turn_id=turn_id, final_utterance=final_text,
                naturalness_flags=flags,
            ).model_dump())
            self._prior_speaker_output = final_text

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
        """Deterministic, no LLM. Reads PersonaSpec for the Arjun-voiced
        fallback templates.

        Bank-text path splices the literal active question (same
        rubric-leak risk as the previous implementation — accepted because
        (i) rare failure path, (ii) candidate already heard the question,
        (iii) the alternative is a recursive LLM call that might also
        fail).
        """
        from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA
        if speaker_input.bank_text:
            return DEFAULT_PERSONA.fallback_empty_output.format(
                bank_text=speaker_input.bank_text,
            )
        return DEFAULT_PERSONA.fallback_empty_output_no_bank

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
