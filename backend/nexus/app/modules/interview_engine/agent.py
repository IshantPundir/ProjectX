"""ProjectX Interview Engine — clean-slate generic-LLM entrypoint.

The previous structured-interview brain (InterviewController +
per-question QuestionTasks + prompts + idle-nudge state machine +
outcome composition + closing scripts) was removed on 2026-05-04.

This file is the bare LiveKit Agent harness:
  * Parses dispatch metadata (session_id, tenant_id, correlation_id).
  * Binds structlog contextvars so every log line carries them.
  * Fetches SessionConfig in-process via build_session_config.
  * Fetches per-tenant settings (currently unused; future agent will read).
  * Builds a generic ``Agent`` whose system prompt names the candidate,
    the job title, and the company's about text — nothing structured.
  * Builds an AgentSession with STT/TTS/LLM/VAD/turn-detector via the
    ``app.ai.realtime`` factories.
  * Wires the audit envelope (EventCollector + sink) so transcripts and
    audio metrics land on disk under ``engine-events/<session_id>.json``.
  * Persists a minimal SessionResult on close and publishes
    ``session_outcome`` for the candidate frontend.
  * Cold start — does not greet, does not speak first. The candidate
    speaks first and the LLM responds.

The plumbing is intentionally preserved so a new structured agent can
re-attach without re-discovering it:
  * SessionConfig is fetched and bound at session scope.
  * tenant_settings is fetched (only consumed via ``engine_agent_name``
    fallback in the system prompt today; everything else, including
    ``engine_knockout_policy``, is pass-through).
  * EventCollector + sink format is unchanged so the envelope schema
    remains forward-compatible.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AgentStateChangedEvent,
    CloseEvent,
    ErrorEvent,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    TurnHandlingOptions,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.agents.voice.events import (
    AgentFalseInterruptionEvent,
    CloseReason,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    SessionUsageUpdatedEvent,
    SpeechCreatedEvent,
)
from livekit.agents.inference.interruption import OverlappingSpeechEvent

from app.ai.config import ai_config
# Process-startup plugin imports. Each call registers the plugin so that
# `python agent.py download-files` discovers and prewarms model files at
# container build time. Per the `app.ai.realtime` carve-out (see
# backend/nexus/CLAUDE.md), direct vendor SDK imports are forbidden
# EXCEPT for process-startup model registration of plugins that need
# local model files. Silero (VAD) and turn_detector (multilingual EOU)
# qualify. Session-time instantiation still goes through
# `app.ai.realtime.build_*`.
from livekit.plugins import silero
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401

from app.ai.realtime import (
    build_llm_plugin,
    build_stt_plugin,
    build_turn_detector,
    build_tts_plugin,
)
from app.ai.otel import bootstrap_tracer_provider
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_runtime import (
    QuestionResult,
    SessionConfig,
    SessionResult,
    build_session_config,
    record_session_result,
)
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.tenant_settings import get_tenant_settings


log = structlog.get_logger("interview-engine")
SessionOutcome = Literal[
    "completed",
    "candidate_ended",
    "candidate_disconnected",
    "error",
]


server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Process-startup hook.

    1. Bootstrap a TracerProvider so livekit-agents' built-in spans ship
       to whatever aggregator the operator points OTLP at. Production-
       safe default: no env vars set -> spans go nowhere.
    2. Load Silero VAD into shared process memory.
    """
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)

    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=settings.engine_silero_activation_threshold,
        min_speech_duration=settings.engine_silero_min_speech_duration,
        min_silence_duration=settings.engine_silero_min_silence_duration,
    )
    log.info(
        "engine.vad.prewarmed",
        activation_threshold=settings.engine_silero_activation_threshold,
        min_speech_duration=settings.engine_silero_min_speech_duration,
        min_silence_duration=settings.engine_silero_min_silence_duration,
    )


server.setup_fnc = prewarm


@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint."""
    metadata = json.loads(ctx.job.metadata or "{}")

    session_id = metadata["session_id"]
    tenant_id_str = metadata["tenant_id"]
    correlation_id = metadata.get("correlation_id", session_id)
    tenant_uuid = uuid.UUID(tenant_id_str)

    structlog.contextvars.bind_contextvars(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

    async with get_bypass_session() as db:
        config = await build_session_config(
            db,
            session_id=uuid.UUID(session_id),
            tenant_id=tenant_uuid,
        )
        tenant_settings = await get_tenant_settings(db, tenant_uuid)
    log.info(
        "engine.config.fetched",
        question_count=len(config.stage.questions),
        stage_type=config.stage.stage_type,
        candidate_name=config.candidate.name,
        job_title=config.job_title,
    )

    agent_name = tenant_settings.engine_agent_name or settings.engine_agent_name
    system_prompt = _build_system_prompt(config=config, agent_name=agent_name)
    prompt_hash = "sha256:" + hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    event_sink: EventLogSink | None = build_sink_from_settings()
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        controller_prompt_hash=prompt_hash,
        task_prompt_hashes={},
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "turn_detector_unlikely_threshold": (
                f"{ai_config.interview_turn_detector_unlikely_threshold}"
                if ai_config.interview_turn_detector_unlikely_threshold is not None
                else "null"
            ),
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )

    agent = GenericInterviewAgent(
        instructions=system_prompt,
        config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        event_sink=event_sink,
    )

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            preemptive_generation={"enabled": True},
            endpointing={
                "mode": "dynamic",
                "min_delay": settings.engine_endpointing_min_delay,
                "max_delay": settings.engine_endpointing_max_delay,
            },
            interruption={
                "mode": "vad",
                "min_duration": 0.5,
                "resume_false_interruption": True,
            },
        ),
    )

    _wire_session_observability(
        session,
        collector=event_collector,
        log_verbose_content=settings.engine_log_user_transcripts,
        log_audio_events=settings.engine_log_audio_events,
    )

    _wire_close_handler(session, agent, collector=event_collector, sink=event_sink)
    _wire_participant_disconnect(ctx, agent)

    # Cold start — no greeting, no preemptive say. The candidate speaks
    # first; the AgentSession routes their utterance through STT, the
    # generic LLM responds, TTS speaks the reply.
    await session.start(agent=agent, room=ctx.room)


def _build_system_prompt(*, config: SessionConfig, agent_name: str) -> str:
    """Render the generic-assistant system prompt.

    Substitutes candidate name, job title, and company.about so the LLM
    has something to riff on. No structure, no rubric, no tools.
    """
    return (
        f"You are {agent_name}, an AI assistant having a voice conversation with "
        f"{config.candidate.name}.\n\n"
        f"They are speaking with you in the context of a {config.job_title} role.\n\n"
        f"About the company:\n{config.company.about}\n\n"
        "Have a natural, warm, concise conversation. Let the candidate lead. "
        "Speak in plain text only — no markdown, no lists, no emojis. "
        "Keep replies to two short sentences unless they ask for more."
    )


class GenericInterviewAgent(Agent):
    """Bare LiveKit Agent — no tools, no structured loop.

    Carries enough state for the close handler to persist a minimal
    SessionResult and publish ``session_outcome``. ``_end_outcome`` is
    settable from the participant-disconnect path.
    """

    def __init__(
        self,
        *,
        instructions: str,
        config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        event_sink: EventLogSink | None,
    ) -> None:
        self._config = config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._event_sink = event_sink
        self._envelope_written: bool = False
        self._persisted: bool = False
        self._end_outcome: SessionOutcome | None = None
        self._session_start_monotonic: float = time.monotonic()
        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        """No-op. Cold start — wait for the candidate to speak first."""
        self._session_start_monotonic = time.monotonic()
        log.info(
            "agent.on_enter",
            candidate_name=self._config.candidate.name,
            job_title=self._config.job_title,
        )

    async def _persist_session_result(self, outcome: SessionOutcome) -> None:
        if self._persisted:
            return
        result = self._build_session_result(outcome)
        async with get_bypass_session() as db:
            await record_session_result(
                db,
                session_id=uuid.UUID(self._config.session_id),
                tenant_id=self._tenant_id,
                result=result,
                correlation_id=self._correlation_id,
            )
            await db.commit()
        self._persisted = True
        log.info(
            "agent.result.persisted",
            session_id=self._config.session_id,
            outcome=outcome,
        )

    def _build_session_result(self, outcome: SessionOutcome) -> SessionResult:
        """Compose a minimal SessionResult.

        The clean-slate agent doesn't ask structured questions, so every
        question in the bank is reported as ``was_skipped=True`` with no
        observations. Transcripts live in the audit envelope, not on the
        DB row.
        """
        question_results: list[QuestionResult] = [
            QuestionResult(
                question_id=q.id,
                question_text=q.text,
                position=q.position,
                is_mandatory=q.is_mandatory,
                was_skipped=True,
                probes_fired=0,
                observations=[],
                transcript_entries=[],
            )
            for q in self._config.stage.questions
        ]
        return SessionResult(
            session_id=self._config.session_id,
            job_title=self._config.job_title,
            stage_id=self._config.stage.stage_id,
            stage_type=self._config.stage.stage_type,
            candidate_name=self._config.candidate.name,
            duration_seconds=time.monotonic() - self._session_start_monotonic,
            questions_asked=0,
            questions_skipped=len(question_results),
            total_probes_fired=0,
            question_results=question_results,
            full_transcript=[],
            completed_at=datetime.now(timezone.utc).isoformat(),
            knockout_failures=[],
        )

    async def _publish_session_outcome(self, outcome: SessionOutcome) -> None:
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes({"session_outcome": outcome})
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "agent.outcome.publish_failed", outcome=outcome, error=str(exc)
            )

    async def _finalize_event_log(self, *, reason: str) -> None:
        """Idempotent write of the audit envelope to the configured sink."""
        if self._envelope_written or self._event_sink is None:
            return
        self._envelope_written = True
        closed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            envelope = self._collector.close(closed_at=closed_at)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "agent.event_log.envelope_validation_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            target = await asyncio.to_thread(self._event_sink.write, envelope)
            log.info("agent.event_log.written", reason=reason, target=target)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "agent.event_log.sink_write_failed",
                reason=reason,
                error=str(exc),
                error_type=type(exc).__name__,
            )


def _wire_session_observability(
    session: AgentSession,
    *,
    collector: EventCollector,
    log_verbose_content: bool,
    log_audio_events: bool,
) -> None:
    """Attach EventCollector + structlog listeners. PII gating preserved."""
    state: dict[str, float | None] = {
        "t0_monotonic": None,
        "last_usage_emit_at": None,
    }

    def _ts(ev_created_at: float) -> dict[str, int]:
        now = time.monotonic()
        if state["t0_monotonic"] is None:
            state["t0_monotonic"] = now
        elapsed_ms = int((now - state["t0_monotonic"]) * 1000)
        return {
            "elapsed_ms": elapsed_ms,
            "wall_ms": int(ev_created_at * 1000),
        }

    def _emit(kind: str, payload: dict[str, object], ev_created_at: float) -> None:
        wall_ms = int(ev_created_at * 1000)
        collector.append(kind=kind, payload=dict(payload), wall_ms=wall_ms)
        if log_audio_events:
            log.info(kind, **payload, **_ts(ev_created_at))

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        _emit(
            "audio.user.state",
            {"old_state": ev.old_state, "new_state": ev.new_state},
            ev.created_at,
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        _emit(
            "audio.agent.state",
            {"old_state": ev.old_state, "new_state": ev.new_state},
            ev.created_at,
        )

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
        payload: dict[str, object] = {
            "is_final": ev.is_final,
            "transcript_chars": len(ev.transcript),
            "language": str(ev.language) if ev.language else None,
            "speaker_id": ev.speaker_id,
        }
        if log_verbose_content:
            payload["transcript"] = ev.transcript
        _emit("audio.stt.transcribed", payload, ev.created_at)

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        _emit(f"audio.metrics.{m.type}", payload, ev.created_at)

    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", None) or getattr(item, "type", None)
        content_text = getattr(item, "text_content", None)
        if callable(content_text):
            try:
                content_text = content_text()
            except Exception:  # noqa: BLE001
                content_text = None
        payload: dict[str, object] = {
            "role": role,
            "item_type": getattr(item, "type", None),
        }
        if isinstance(content_text, str):
            payload["content_chars"] = len(content_text)
            if log_verbose_content:
                payload["content"] = content_text
        _emit("llm.message.added", payload, ev.created_at)

    @session.on("function_tools_executed")
    def _on_tools_executed(ev: FunctionToolsExecutedEvent) -> None:
        for call, output in ev.zipped():
            payload: dict[str, object] = {
                "tool_name": call.name,
                "tool_call_id": getattr(call, "call_id", None),
                "has_output": output is not None,
                "output_is_error": (
                    bool(getattr(output, "is_error", False)) if output else None
                ),
            }
            raw_args = getattr(call, "arguments", None)
            arg_keys: list[str] = []
            if isinstance(raw_args, dict):
                arg_keys = list(raw_args.keys())
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    arg_keys = list(parsed.keys())
            payload["argument_keys"] = arg_keys
            if log_verbose_content:
                payload["arguments"] = raw_args
                payload["output"] = (
                    getattr(output, "output", None) if output else None
                )
            _emit("llm.tool.executed", payload, ev.created_at)

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev: AgentFalseInterruptionEvent) -> None:
        _emit("audio.interruption.false", {"resumed": ev.resumed}, ev.created_at)

    @session.on("overlapping_speech")
    def _on_overlap(ev: OverlappingSpeechEvent) -> None:
        ev_created = getattr(ev, "created_at", time.time())
        _emit("audio.overlap", {}, ev_created)

    USAGE_EMIT_INTERVAL_S = 30.0

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
        last_at = state.get("last_usage_emit_at")
        if last_at is not None and (ev.created_at - last_at) < USAGE_EMIT_INTERVAL_S:
            return
        state["last_usage_emit_at"] = ev.created_at
        try:
            usage = ev.usage.model_dump()
        except Exception:  # noqa: BLE001
            usage = {"raw": str(ev.usage)}
        _emit("session.usage", usage, ev.created_at)

    @session.on("speech_created")
    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        _emit(
            "audio.speech.created",
            {"source": ev.source, "user_initiated": ev.user_initiated},
            ev.created_at,
        )

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        payload = {
            "source": type(ev.source).__name__,
            "error": str(ev.error),
            "error_type": type(ev.error).__name__,
        }
        log.error("audio.pipeline.error", **payload, **_ts(ev.created_at))
        collector.append(
            kind="audio.pipeline.error",
            payload=payload,
            wall_ms=int(ev.created_at * 1000),
        )


def _wire_close_handler(
    session: AgentSession,
    agent: GenericInterviewAgent,
    *,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    _bg_tasks: set[asyncio.Task[None]] = set()

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        task = asyncio.create_task(_handle_close(ev, agent, collector, sink))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


def _wire_participant_disconnect(
    ctx: JobContext,
    agent: GenericInterviewAgent,
) -> None:
    """Mark the session as candidate_disconnected when the candidate leaves.

    Without this, the AgentSession close handler would default to
    ``completed`` for a hang-up, which lies. The bare agent has no
    end_interview_early intent path, so candidate_disconnected is the
    only non-error termination that comes from the candidate side.
    """

    def _on_participant_disconnected(participant: object) -> None:
        identity = getattr(participant, "identity", "<unknown>")
        reason = getattr(participant, "disconnect_reason", None)
        log.info(
            "session.participant_disconnected",
            participant_identity=identity,
            disconnect_reason=str(reason) if reason is not None else None,
            already_terminating=agent._end_outcome is not None,
        )
        if agent._end_outcome is None:
            agent._end_outcome = "candidate_disconnected"

    ctx.room.on("participant_disconnected", _on_participant_disconnected)


async def _handle_close(
    ev: CloseEvent,
    agent: GenericInterviewAgent,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Persist the SessionResult, publish session_outcome, write envelope."""
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    collector.append(
        kind="session.close",
        payload={
            "reason": ev.reason.value,
            "persisted": agent._persisted,
            "has_error": bool(ev.error),
            "controller_end_outcome": getattr(agent, "_end_outcome", None),
        },
        wall_ms=int(time.time() * 1000),
    )

    if ev.reason == CloseReason.ERROR:
        outcome: SessionOutcome = "error"
    elif agent._end_outcome is not None:
        outcome = agent._end_outcome
    else:
        outcome = "candidate_disconnected"

    if not agent._persisted:
        try:
            await agent._persist_session_result(outcome)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    await agent._publish_session_outcome(outcome)
    await agent._finalize_event_log(reason="close_handler")
