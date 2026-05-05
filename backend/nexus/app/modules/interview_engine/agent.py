"""ProjectX Interview Engine — StructuredInterviewAgent entrypoint.

This file is the LiveKit Agent harness for the Phase B structured
interview agent:
  * Parses dispatch metadata (session_id, tenant_id, correlation_id).
  * Binds structlog contextvars so every log line carries them.
  * Fetches SessionConfig in-process via build_session_config.
  * Fetches per-tenant settings (engine_agent_name fallback).
  * Wires LedgerPersistence via app.pubsub._get_client() (Phase A
    close-out Flag 5).
  * Builds a StructuredInterviewAgent (deterministic-flow; llm_node
    overridden to emit zero chunks).
  * Builds an AgentSession with STT/TTS/LLM/VAD/turn-detector via the
    ``app.ai.realtime`` factories.
  * Wires the audit envelope (EventCollector + sink) so transcripts and
    audio metrics land on disk under ``engine-events/<session_id>.json``.
  * Persists a SessionResult on close and publishes ``session_outcome``
    for the candidate frontend.
  * preemptive_generation flipped to False (kept for clarity even though
    llm_node short-circuits).

Three-layer guardrail (spec §3.1):
  1. Hard — llm_node override emits zero chunks (Pattern 2).
  2. Defense in depth — inert system prompt.
  3. Single utterance entry point — _say gated by check_safety.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

import structlog
from livekit.agents import (
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
from livekit.agents.inference.interruption import OverlappingSpeechEvent
from livekit.agents.voice.events import (
    AgentFalseInterruptionEvent,
    CloseReason,
    ConversationItemAddedEvent,
    FunctionToolsExecutedEvent,
    SessionUsageUpdatedEvent,
    SpeechCreatedEvent,
)

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
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.ai.client import get_openai_raw_client
from app.ai.config import ai_config
from app.ai.otel import bootstrap_tracer_provider
from app.ai.realtime import (
    build_llm_plugin,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
)
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_engine.orchestrator import (
    ExitMode,
    InterviewPhase,
    LedgerPersistence,
)
from app.modules.interview_engine.speech import SpeechAgent, SpeechRenderError
from app.modules.interview_engine.structured_agent import (
    SessionOutcome,
    StructuredInterviewAgent,
)
from app.modules.interview_runtime import (
    SessionConfig,
    build_session_config,
)
from app.modules.tenant_settings import get_tenant_settings

# Reusing app.pubsub's process-level memoized Redis client per Phase A
# close-out Flag 5; LedgerPersistence is the second legitimate consumer.
# No separate engine pool needed.
from app.pubsub import _get_client as _get_redis_client

log = structlog.get_logger("interview-engine")
# SessionOutcome is the single source of truth — defined in
# structured_agent.py (above) so agent.py and the rest of the engine
# reference the same type binding. Drift would let a future phase add a
# new outcome value (e.g., Phase 5's "knockout_closed") to one binding
# but not the other; the shared definition prevents that.


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

    persistence = LedgerPersistence(
        client=_get_redis_client(),
        tenant_id=tenant_id_str,
        session_id=session_id,
    )

    speech_agent = SpeechAgent(
        client=get_openai_raw_client(),
        model=ai_config.speech_agent_model,
        effort=ai_config.speech_agent_effort or None,
        collector=event_collector,
    )

    agent = StructuredInterviewAgent(
        config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        persistence=persistence,
        speech_agent=speech_agent,
    )

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            # Phase B: structured agent drives every utterance via _say.
            preemptive_generation={"enabled": False},
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

    # Structured start — on_enter launches the main loop which speaks
    # first via _say. preemptive_generation is disabled; llm_node emits
    # zero chunks. The agent owns every utterance.
    await session.start(agent=agent, room=ctx.room)


def _build_system_prompt(*, config: SessionConfig, agent_name: str) -> str:
    """Inert system prompt for the structured agent.

    The realtime LLM is fully bypassed by StructuredInterviewAgent.llm_node;
    this prompt is defense in depth (Layer 2 of the three-layer guardrail).
    See spec §3.1.

    The string returned here is the same `INERT_SYSTEM_PROMPT` constant the
    agent passes to `super().__init__(instructions=...)`. We import it from
    structured_agent so a future edit to the inert wording can't drift the
    audit envelope's `controller_prompt_hash` away from the prompt actually
    sent to LiveKit.
    """
    _ = config  # unused — kept in signature for symmetry with future phases
    _ = agent_name  # unused — agent_name surfacing returns in Phase C
    # Lazy import to avoid a circular import (agent.py imports
    # StructuredInterviewAgent at module load; we only need the constant
    # at call time).
    from app.modules.interview_engine.structured_agent import (
        INERT_SYSTEM_PROMPT,
    )
    return INERT_SYSTEM_PROMPT


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
    agent: StructuredInterviewAgent,
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
    agent: StructuredInterviewAgent,
) -> None:
    """Mark the session as candidate_disconnected when the candidate leaves.

    Without this, the AgentSession close handler would default to
    ``candidate_disconnected`` for a hang-up anyway, but setting it
    explicitly here ensures the structured agent's main loop knows
    the session ended before CLOSED was reached.
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
    agent: StructuredInterviewAgent,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Persist the SessionResult, publish session_outcome, write envelope.

    Drives the final state.transition(CLOSED) for paths where the
    orchestrator main loop never reached CLOSED itself (disconnect,
    unhandled exception). Legal direct edges to CLOSED exist from
    every non-terminal phase per state.py::_LEGAL_TRANSITIONS.
    """
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    # Phase C — cancel pending pre-render Task if in flight.
    pending = getattr(agent, "_pending_next_render", None)
    if pending is not None and not pending.done():
        pending.cancel()
        try:
            # Brief await for cancellation propagation so the OpenAI httpx
            # connection actually closes. Bounded so we don't block close
            # indefinitely on a slow connection. Spec §3.4: the runtime
            # cap is 2s; the spike (Task 3) confirmed cancellation
            # latency p99 << 500ms, so the timeout is safety net only.
            await asyncio.wait_for(asyncio.shield(pending), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, SpeechRenderError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "structured_agent.pending_render.cancel_failed",
                error=str(exc),
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

    # Drive the final state-machine transition if the main loop didn't.
    state = agent.get_state()
    if state.phase != InterviewPhase.CLOSED:
        # Capture old_phase BEFORE state.transition() — afterward
        # state.phase is already CLOSED.
        old_phase_value = state.phase.value
        try:
            state.transition(InterviewPhase.CLOSED)
            collector.append(
                kind="orchestrator.phase_changed",
                payload={
                    "old_phase": old_phase_value,
                    "new_phase": "closed",
                    "reason": f"close_handler_{outcome}",
                },
                wall_ms=int(time.time() * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.transition_failed",
                error=str(exc),
                current_phase=state.phase.value,
            )

        exit_mode = (
            ExitMode.COMPLETED if outcome == "completed"
            else ExitMode.TECHNICAL_FAILURE
        )
        try:
            state.set_exit_mode(exit_mode, ended_at=datetime.now(UTC))
            collector.append(
                kind="orchestrator.exit",
                payload={
                    "exit_mode": exit_mode.value,
                    "reason": f"close_handler_{outcome}",
                },
                wall_ms=int(time.time() * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "session.close.set_exit_mode_failed",
                error=str(exc),
            )

    # Emit ledger snapshot + persistence-gap detection envelope events.
    ledger = agent.get_ledger()
    collector.append(
        kind="orchestrator.ledger.snapshot",
        payload={
            "signals": [s.model_dump() for s in ledger.signals.values()],
            "sequence_number": ledger.sequence_number,
        },
        wall_ms=int(time.time() * 1000),
    )

    persistence = agent.get_persistence()
    gaps = persistence.detect_gaps(
        current_state_seq=state.sequence_number,
        current_ledger_seq=ledger.sequence_number,
    )
    collector.append(
        kind="persistence.gaps_detected",
        payload=gaps,
        wall_ms=int(time.time() * 1000),
    )

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
    await agent._finalize_event_log(reason="close_handler", sink=sink)
