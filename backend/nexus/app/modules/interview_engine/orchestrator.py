"""InterviewOrchestrator — drives the per-turn pipeline.

This is the LiveKit hook surface. on_enter delivers the first question via a
synthesized JudgeOutput. on_user_turn_completed runs Judge → State Engine →
Speaker on each candidate turn. on_close builds the SessionResult.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

import structlog

from app.modules.interview_engine.audit_events import (
    FrontendAttributePayload, JudgeSyntheticPayload,
    SessionTerminalDeliveredPayload,
    SpeakerCallPayload, SpeakerOutputPayload,
    TurnCompletedPayload, TurnStartedPayload,
)
from app.modules.interview_engine.event_kinds import (
    FRONTEND_ATTRIBUTE_PUBLISHED, JUDGE_SYNTHETIC,
    SESSION_TERMINAL_DELIVERED,
    SPEAKER_CALL, SPEAKER_OUTPUT, TURN_COMPLETED, TURN_STARTED,
)
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_TIME_REMAINING_SECONDS,
    ATTR_TOTAL_QUESTIONS, AttributePublisher,
)
from app.modules.interview_engine.judge.service import JudgeService
from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.speaker.service import SpeakerService
from app.modules.interview_engine.openers import OpenerLibrary
from app.modules.interview_engine.state.engine import (
    StateEngine,
    _strip_cap_advance_segue,
    _strip_clarify_opener,
    _strip_push_back_opener,
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


def _compute_cache_text(*, speaker_input: Any, final_text: str) -> str | None:
    """Return the opener-stripped version of ``final_text`` for the
    repeat-cache, or ``None`` when no stripping applies.

    Dispatches on ``speaker_input.instruction_kind``:

      * ``deliver_question`` with ``is_post_cap_advance=True`` →
        strip the cap-forced-advance segue (Phase 9.5 Bug A).
      * ``push_back`` → strip the conversational opener
        ("Got it — ", "Right — ", etc.) (Phase 9.7 Bug C.1).
      * ``clarify`` → strip the rephrase acknowledgment
        ("Sure, let me rephrase. ", "Let me restate that. ",
        "No problem, Ishant. In simple terms: ") (Phase 9.7 Bug C.1).
      * everything else → ``None`` (cache uses the full spoken text).

    The strippers are conservative — they require recognized prefix
    patterns and return the input unchanged when the LLM produced
    something unexpected. Returning ``None`` when no change happened
    lets ``register_agent_utterance`` use its default (cache=text).
    """
    from app.modules.interview_engine.models.speaker import InstructionKind

    kind = speaker_input.instruction_kind
    if (
        kind == InstructionKind.deliver_question
        and getattr(speaker_input, "is_post_cap_advance", False)
    ):
        stripped = _strip_cap_advance_segue(final_text)
    elif kind == InstructionKind.push_back:
        stripped = _strip_push_back_opener(final_text)
    elif kind == InstructionKind.clarify:
        stripped = _strip_clarify_opener(final_text)
    else:
        return None
    return stripped if stripped != final_text else None


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


def _derive_sub_context(speaker_input: Any) -> "SubContext":
    """Map SpeakerInput context to the OpenerLibrary's SubContext key.

    See spec §4.3 sub-context derivation table.
    """
    from app.modules.interview_engine.models.speaker import InstructionKind
    from app.modules.interview_engine.openers import SubContext

    kind = speaker_input.instruction_kind
    if kind == InstructionKind.deliver_question:
        if getattr(speaker_input, "is_post_cap_advance", False):
            return SubContext.POST_CAP_ADVANCE
        return SubContext.DEFAULT
    if kind == InstructionKind.redirect:
        tm = speaker_input.turn_metadata
        if tm is not None:
            if tm.candidate_social_or_greeting:
                return SubContext.SOCIAL_OR_GREETING
            if tm.candidate_abusive:
                return SubContext.ABUSIVE
            if tm.candidate_attempted_injection:
                return SubContext.INJECTION
            if tm.candidate_off_topic:
                return SubContext.OFF_TOPIC
        return SubContext.OFF_TOPIC  # default redirect bucket
    if kind == InstructionKind.push_back:
        code = speaker_input.push_back_reason_code
        if code == "vague_answer":
            return SubContext.VAGUE_ANSWER
        if code == "deflection":
            return SubContext.DEFLECTION
        if code == "missing_specifics":
            return SubContext.MISSING_SPECIFICS
        if code == "unanswered_subquestion":
            return SubContext.UNANSWERED_SUBQUESTION
        return SubContext.DEFAULT
    if kind == InstructionKind.polite_close:
        if speaker_input.failed_signal_value:
            return SubContext.KNOCKOUT
        return SubContext.DEFAULT
    return SubContext.DEFAULT


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
        opener_library: OpenerLibrary,
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
        self._opener_library = opener_library
        self._recent_openers: deque[str] = deque(maxlen=5)

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

        turn_id = str(uuid.uuid4())
        self._turn_index += 1
        elapsed_ms = self._elapsed_ms()
        self._append(TURN_STARTED, TurnStartedPayload(
            turn_id=turn_id, turn_index=self._turn_index,
            stt_text_raw=candidate_text, stt_text_used=candidate_text,
        ).model_dump())

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
        self._append_judge_event(turn_id=turn_id, result=result)

        decision = self._state.process_judge_output(
            turn_id=turn_id, judge_output=result.judge_output,
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
    ) -> str:
        # Phase 9.8 — opener prefetch architecture.
        # 1. Pick opener from library based on (kind, sub_context).
        # 2. Update SpeakerInput with pre_spoken_opener so Speaker LLM
        #    knows to compose continuation content (no own opener).
        # 3. Kick off Speaker LLM in parallel with opener playback.
        # 4. Play opener audio (cache hit) or fall back to text TTS.
        # 5. After opener finishes, await Speaker stream and pipe to TTS.
        # 6. Cache ONLY the Speaker content for repeat replay (no opener).
        sub_ctx = _derive_sub_context(speaker_input)
        opener = self._opener_library.pick(
            kind=speaker_input.instruction_kind,
            sub_context=sub_ctx,
            recent_openers=self._recent_openers,
        )
        speaker_input_with_opener = speaker_input.model_copy(
            update={"pre_spoken_opener": opener.text},
        )

        try:
            # Kick off Speaker LLM call in parallel with opener playback.
            speaker_task = asyncio.create_task(
                self._speaker.stream(
                    turn_id=turn_id,
                    speaker_input=speaker_input_with_opener,
                    correlation_id=self._correlation_id,
                    tenant_id=self._tenant_id,
                ),
            )

            # Play the opener (text + cached audio if available, text-only
            # TTS as a fallback). Skip entirely when this kind has no
            # opener variants (deliver_first_question).
            cache_hit = False
            if opener.text is not None:
                say_kwargs: dict[str, Any] = {
                    "text": opener.text,
                    "allow_interruptions": True,
                    "add_to_chat_ctx": True,
                }
                if opener.audio_iter is not None:
                    say_kwargs["audio"] = opener.audio_iter()
                    cache_hit = True
                opener_handle = await agent.session.say(**say_kwargs)
                # Wait for opener playback to complete before piping
                # the Speaker content. Small audible gap (~150-300ms)
                # between the two say() calls is acceptable for v1.
                if opener_handle is not None and hasattr(opener_handle, "wait_for_playout"):
                    try:
                        await opener_handle.wait_for_playout()
                    except Exception:  # noqa: BLE001
                        # Mocks may not implement wait_for_playout cleanly; in
                        # production this is a real playout failure (room close
                        # mid-opener, TTS stall). Continue anyway so the Speaker
                        # content still gets piped — but surface the failure so
                        # it's diagnosable.
                        _log.warning(
                            "opener.playout_wait_failed",
                            turn_id=turn_id,
                            opener_text=opener.text[:40] if opener.text else None,
                        )
                self._recent_openers.append(opener.text)

                from app.modules.interview_engine.event_kinds import SPEAKER_OPENER_PLAYED
                from app.modules.interview_engine.audit_events import SpeakerOpenerPlayedPayload
                self._append(SPEAKER_OPENER_PLAYED, SpeakerOpenerPlayedPayload(
                    turn_id=turn_id,
                    instruction_kind=speaker_input.instruction_kind.value,
                    sub_context=sub_ctx.value,
                    opener_text=opener.text,
                    cache_hit=cache_hit,
                ).model_dump())

            # Speaker LLM result — kicked off above, may already be done.
            handle = await speaker_task
            stream = handle.stream()
            speech_handle = await agent.session.say(
                stream, allow_interruptions=True, add_to_chat_ctx=True,
            )
            final_text = await handle.final_text()

            if not final_text.strip():
                # Phase 9.4 — distinguish "candidate interrupted before
                # the LLM produced output" (don't talk back over them)
                # from "true empty output" (play a deterministic
                # fallback). LiveKit's SpeechHandle.interrupted is the
                # signal: True means the candidate's voice cancelled
                # the in-flight TTS pipeline mid-stream, which also
                # cancels the upstream LLM stream we were consuming.
                if _was_interrupted(speech_handle):
                    return await self._handle_interrupted_speaker(
                        turn_id=turn_id,
                        speaker_input=speaker_input, handle=handle,
                    )
                return await self._handle_empty_speaker_output(
                    agent=agent, turn_id=turn_id,
                    speaker_input=speaker_input, handle=handle,
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

            # Cache for repeat replay. The opener is NEVER part of the
            # Speaker content (the Speaker prompt was updated to skip
            # openers when pre_spoken_opener is set), so the cache is
            # clean by construction. The legacy cache_text strip path
            # (Phase 9.5/9.7) is now redundant — kept None here; will
            # be deleted entirely in Task 15.
            self._state.register_agent_utterance(
                turn_id=turn_id, text=final_text,
                instruction_kind=speaker_input.instruction_kind,
                cache_text=None,
            )
            return final_text

        except Exception as exc:
            # Cancel the in-flight Speaker LLM call if the opener path errored
            # before we reached `await speaker_task`. Without this the task is
            # orphaned: it keeps running, costs tokens, and Python 3.11+ logs
            # "Task exception was never retrieved" to stderr.
            if "speaker_task" in locals() and not speaker_task.done():
                speaker_task.cancel()
                try:
                    await speaker_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
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
            self._state.register_agent_utterance(
                turn_id=turn_id, text=self._RECOVERY_TEXT,
                instruction_kind=speaker_input.instruction_kind,
            )
            return self._RECOVERY_TEXT

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
        # Register an empty agent utterance so the State Engine's
        # transcript and repeat-cache don't see a phantom-success turn.
        # Empty text with the instruction_kind preserved keeps the
        # _question_utterances cache filter (in _QUESTION_KINDS)
        # working correctly: an interrupted deliver_question still
        # caches "" rather than the previous turn's question text.
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

    def _append(self, kind: str, payload: dict) -> None:
        wall_ms = int(time.time() * 1000)
        self._collector.append(kind=kind, payload=payload, wall_ms=wall_ms)

    def _elapsed_ms(self) -> int:
        if self._session_started_monotonic is None:
            return 0
        return int((time.monotonic() - self._session_started_monotonic) * 1000)

    def _append_judge_event(self, *, turn_id: str, result: Any) -> None:
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
                input_summary={},
                output=result.judge_output.model_dump(mode="json"),
                latency_ms=result.latency_ms,
                usage=result.usage,
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
