"""InterviewOrchestrator — drives the per-turn pipeline.

This is the LiveKit hook surface. on_enter delivers the first question via a
synthesized JudgeOutput. on_user_turn_completed runs Judge → State Engine →
Speaker on each candidate turn. on_close builds the SessionResult.
"""
from __future__ import annotations

import asyncio
import time
import uuid
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
from app.modules.interview_engine.state.engine import StateEngine
from app.modules.interview_runtime import SessionConfig


@dataclass(slots=True)
class OrchestratorConfig:
    recent_turns_window: int = 8
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

    # --- Public accessors ---

    def lifecycle_snapshot(self) -> Any:
        """Public passthrough to the underlying StateEngine.

        The close handler in ``agent.py`` needs to read
        ``lifecycle.last_outcome`` to decide the persisted SessionOutcome
        without reaching into the orchestrator's private ``_state``.
        """
        return self._state.lifecycle_snapshot()

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
        recent = self._state.transcript_snapshot()[-self._config.recent_turns_window:]
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

        judge_input = build_judge_input(
            active_question=active_q_cfg,
            ledger_snapshot=ledger, queue_snapshot=queue, claims_snapshot=claims,
            recent_turns=recent, candidate_utterance=candidate_text,
            time_remaining_seconds=time_remaining,
            active_signal_metadata=active_signal_meta,
            active_remaining_probes=remaining_probes_dict,
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

        ``drain=True`` waits for any in-flight speech to finish playing
        before closing — the candidate hears the polite_close / canned
        terminal audio fully and then the connection terminates ~1s
        later, which is the user-visible behavior change this fixes.

        The shutdown call is intentionally fire-and-forget: we don't
        await it inside the turn handler because that would block the
        framework's post-turn pipeline. The background task handles
        drain semantics on its own.
        """
        if self._shutdown_scheduled:
            return
        self._shutdown_scheduled = True
        asyncio.create_task(agent.session.shutdown(drain=True))

    async def _stream_speaker_and_say(
        self, *, agent: Any, turn_id: str, speaker_input: Any,
    ) -> str:
        try:
            handle = await self._speaker.stream(
                turn_id=turn_id, speaker_input=speaker_input,
                correlation_id=self._correlation_id,
                tenant_id=self._tenant_id,
            )
            stream = handle.stream()
            await agent.session.say(
                stream, allow_interruptions=True, add_to_chat_ctx=True,
            )
            final_text = await handle.final_text()
            self._append(SPEAKER_CALL, SpeakerCallPayload(
                turn_id=turn_id, model="speaker", prompt_hash="sha256:speaker",
                instruction_kind=speaker_input.instruction_kind.value,
                bank_text_present=speaker_input.bank_text is not None,
                latency_ms_first_token=handle.latency_ms_first_token,
                latency_ms_total=handle.latency_ms_total,
                usage=handle.usage, final_utterance=final_text,
            ).model_dump())
            self._append(SPEAKER_OUTPUT, SpeakerOutputPayload(
                turn_id=turn_id, final_utterance=final_text,
            ).model_dump())
            self._state.register_agent_utterance(turn_id=turn_id, text=final_text)
            return final_text
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
            self._state.register_agent_utterance(
                turn_id=turn_id, text=self._RECOVERY_TEXT,
            )
            return self._RECOVERY_TEXT

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
