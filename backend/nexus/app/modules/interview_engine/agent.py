"""ProjectX Interview Engine — structured-agent entrypoint.

Phase 9.1 cutover. The placeholder ``GenericInterviewAgent`` (and its
``_build_system_prompt`` / ``_build_session_result`` helpers) is gone;
its job is now owned by :class:`InterviewOrchestrator`. This file is the
LiveKit harness around the orchestrator: dispatch wiring, plugin
construction, audio-tuning observability, and close-handler glue. The
orchestrator owns turn semantics, state, prompt-grounded speech, and
SessionResult composition.

What stays:
  * Audio pipeline (STT/LLM/TTS/VAD/turn-detector via app.ai.realtime).
  * Audit envelope (EventCollector + sink).
  * SessionConfig fetch + tenant_settings load on dispatch.
  * Audio tuning summary helper + close handler that persists
    SessionResult and publishes ``session_outcome`` to the candidate
    frontend.
  * ``preemptive_generation`` is **disabled**: the structured agent
    drives every turn through Judge → State → Speaker, so framework
    speculation would only race the orchestrator.
  * STT plugin construction goes through ``build_stt_plugin_for_session``
    so the per-session keyterm seam (Task 6.2) is wired and ready.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

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
    room_io,
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
# local model files. turn_detector (multilingual EOU) qualifies.
# VAD is now ai-coustics' built-in adapter — no separate model to prewarm.
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401
from openai import AsyncOpenAI
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.ai.config import ai_config
from app.ai.otel import bootstrap_tracer_provider
from app.ai.prompts import prompt_loader
from app.ai.realtime import (
    build_interruption_options,
    build_llm_plugin,
    build_noise_cancellation,
    build_tts_plugin,
    build_turn_detector,
    build_vad,
)
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_engine.frontend_attributes import AttributePublisher
from app.modules.interview_engine.judge.service import JudgeService
from app.modules.interview_engine.orchestrator import (
    InterviewOrchestrator,
    OrchestratorConfig,
)
from app.modules.interview_engine.speaker.persona import resolve_persona_name
from app.modules.interview_engine.speaker.service import SpeakerService
from app.modules.interview_engine.state.engine import StateEngine, StateEngineConfig
from app.modules.interview_engine.stt_factory import build_stt_plugin_for_session
from app.modules.interview_runtime import (
    SessionResult,
    build_session_config,
    record_session_result,
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

    Bootstrap a TracerProvider so livekit-agents' built-in spans ship
    to whatever aggregator the operator points OTLP at. Production-
    safe default: no env vars set -> spans go nowhere.

    VAD is no longer prewarmed — we use ai-coustics' built-in VAD
    adapter, which lives inside the ai-coustics process loaded for
    noise cancellation. No separate model weights to load.
    """
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)


server.setup_fnc = prewarm


class StructuredInterviewAgent(Agent):
    """LiveKit Agent subclass that delegates to InterviewOrchestrator.

    The orchestrator owns all turn semantics; this class is a thin LiveKit
    hook surface that forwards on_enter / on_user_turn_completed. The
    Speaker prompt (loaded by the orchestrator) carries every
    candidate-facing instruction, so the LiveKit-level ``instructions``
    string is informational only.
    """

    def __init__(
        self,
        *,
        orchestrator: InterviewOrchestrator,
        instructions: str,
    ) -> None:
        super().__init__(instructions=instructions)
        self._orchestrator = orchestrator
        # Mirrored on the agent so the close handler / disconnect listener
        # can mark + read the outcome regardless of which path closed
        # the session. Population is best-effort: the orchestrator's
        # SessionResult is always the source of truth for the persisted
        # session record.
        self._end_outcome: SessionOutcome | None = None
        self._persisted = False
        self._envelope_written = False
        self._audio_tuning_summary: dict[str, object] | None = None

    @property
    def orchestrator(self) -> InterviewOrchestrator:
        return self._orchestrator

    async def on_enter(self) -> None:
        await self._orchestrator.on_enter(self)

    async def on_user_turn_completed(
        self,
        turn_ctx: Any,
        new_message: Any,
    ) -> None:
        await self._orchestrator.on_user_turn_completed(
            self, turn_ctx, new_message,
        )


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
        session_config = await build_session_config(
            db,
            session_id=uuid.UUID(session_id),
            tenant_id=tenant_uuid,
        )
        tenant_settings = await get_tenant_settings(db, tenant_uuid)
    log.info(
        "engine.config.fetched",
        question_count=len(session_config.stage.questions),
        stage_type=session_config.stage.stage_type,
        candidate_name=session_config.candidate.name,
        job_title=session_config.job_title,
    )

    # --- StateEngine: ledger + queue + claims + lifecycle ---
    state_engine = StateEngine(
        session_config=session_config,
        config=StateEngineConfig(claims_pool_max=settings.engine_claims_pool_max),
    )
    state_engine.set_persona_name(
        resolve_persona_name(tenant_settings=tenant_settings, settings=settings),
    )

    # --- Judge + Speaker: load prompts (Phase 10 will author them) ---
    # Until the v1 prompt files land, use placeholder strings so the
    # entrypoint loads cleanly. Hashes still go into the audit envelope
    # (different placeholders → different hashes → distinguishable in
    # logs).
    try:
        judge_prompt = prompt_loader.get("engine/judge.system")
    except FileNotFoundError:
        judge_prompt = "(engine/judge.system prompt not yet authored)"
    try:
        speaker_prompt = prompt_loader.get("engine/speaker.system")
    except FileNotFoundError:
        speaker_prompt = "(engine/speaker.system prompt not yet authored)"

    judge_hash = "sha256:" + hashlib.sha256(judge_prompt.encode("utf-8")).hexdigest()
    speaker_hash = "sha256:" + hashlib.sha256(speaker_prompt.encode("utf-8")).hexdigest()

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    judge_service = JudgeService(
        openai_client=openai_client,
        model=settings.engine_judge_model,
        system_prompt=judge_prompt,
        system_prompt_hash=judge_hash,
        next_pending_mandatory_resolver=state_engine.next_pending_mandatory_id,
        total_budget_ms=settings.engine_judge_total_budget_ms,
        retry_wait_ms=settings.engine_judge_retry_wait_ms,
    )
    speaker_service = SpeakerService(
        openai_client=openai_client,
        model=settings.engine_speaker_model,
        system_prompt=speaker_prompt,
        system_prompt_hash=speaker_hash,
    )

    attr_pub = AttributePublisher(room=ctx.room)

    event_sink: EventLogSink | None = build_sink_from_settings()
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        # Controller prompt hash is no longer the placeholder system
        # prompt; the orchestrator is the controller now. We keep the
        # field stable in the envelope by hashing the judge prompt as a
        # proxy for the controller (judge is the controller's brain).
        controller_prompt_hash=judge_hash,
        task_prompt_hashes={
            "judge": judge_hash,
            "speaker": speaker_hash,
        },
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "judge": settings.engine_judge_model,
            "speaker": settings.engine_speaker_model,
            "turn_detector_unlikely_threshold": (
                f"{ai_config.interview_turn_detector_unlikely_threshold}"
                if ai_config.interview_turn_detector_unlikely_threshold is not None
                else "null"
            ),
            "noise_cancellation": ai_config.interview_noise_cancellation,
            "nc_enhancement_level": f"{ai_config.interview_nc_enhancement_level}",
            "vad_provider": "ai_coustics",
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )

    orchestrator = InterviewOrchestrator(
        session_config=session_config,
        tenant_settings=tenant_settings,
        state_engine=state_engine,
        judge=judge_service,
        speaker=speaker_service,
        attr_publisher=attr_pub,
        event_collector=event_collector,
        correlation_id=correlation_id,
        config=OrchestratorConfig(
            recent_turns_window=settings.engine_recent_turns_window,
            checkpoint_turns=settings.engine_checkpoint_turns,
            checkpoint_seconds=settings.engine_checkpoint_seconds,
        ),
        tenant_id=str(tenant_uuid),
    )

    agent = StructuredInterviewAgent(
        orchestrator=orchestrator,
        instructions="(see Speaker prompt — agent has no top-level instructions)",
    )

    session = AgentSession(
        stt=build_stt_plugin_for_session(session_config=session_config),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=build_vad(),
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            # Disabled: the structured agent drives every turn through
            # Judge → State → Speaker. Framework-level preemption would
            # only race the orchestrator.
            preemptive_generation={"enabled": False},
            endpointing={
                "mode": "dynamic",
                "min_delay": settings.engine_endpointing_min_delay,
                "max_delay": settings.engine_endpointing_max_delay,
            },
            interruption=build_interruption_options(),
        ),
    )

    _wire_session_observability(
        session,
        agent=agent,
        collector=event_collector,
        log_verbose_content=settings.engine_log_user_transcripts,
        log_audio_events=settings.engine_log_audio_events,
    )

    _wire_close_handler(
        session,
        agent,
        orchestrator=orchestrator,
        tenant_uuid=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        sink=event_sink,
    )
    _wire_participant_disconnect(ctx, agent)

    nc_filter = build_noise_cancellation()
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=nc_filter,
            ),
        ),
    )


def _wire_session_observability(
    session: AgentSession,
    *,
    agent: StructuredInterviewAgent,
    collector: EventCollector,
    log_verbose_content: bool,
    log_audio_events: bool,
) -> None:
    """Attach EventCollector + structlog listeners. PII gating preserved.

    The orchestrator owns the structured-engine transcript (it captures
    candidate utterances on-turn and agent utterances after Speaker
    completes), so the conversation_item_added handler no longer
    rebuilds a transcript list — it only writes to the audit envelope.
    """
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
        # detected_at is the canonical timestamp on OverlappingSpeechEvent per LK
        # docs (/reference/agents/events/). Fall back to time.time() defensively
        # if the SDK ever omits the field.
        ev_created = getattr(ev, "detected_at", None) or time.time()
        _emit(
            "audio.overlap",
            {
                "is_interruption": ev.is_interruption,
                "probability": getattr(ev, "probability", None),
                "detection_delay": getattr(ev, "detection_delay", None),
            },
            ev_created,
        )

    usage_emit_interval_s = 30.0

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
        last_at = state.get("last_usage_emit_at")
        if last_at is not None and (ev.created_at - last_at) < usage_emit_interval_s:
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
    orchestrator: InterviewOrchestrator,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    _bg_tasks: set[asyncio.Task[None]] = set()

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        task = asyncio.create_task(
            _handle_close(
                ev,
                agent,
                orchestrator=orchestrator,
                tenant_uuid=tenant_uuid,
                correlation_id=correlation_id,
                collector=collector,
                sink=sink,
            )
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


def _percentile_stats(values: list[int]) -> dict[str, int]:
    """Compute p50/p95/max/n stats for an int list (true median for even n)."""
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "n": 0}
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n % 2 == 0:
        p50 = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) // 2
    else:
        p50 = sorted_values[n // 2]
    p95 = sorted_values[min(n - 1, int(n * 0.95))]
    return {"p50": p50, "p95": p95, "max": sorted_values[-1], "n": n}


def _wire_participant_disconnect(
    ctx: JobContext,
    agent: StructuredInterviewAgent,
) -> None:
    """Mark the session as candidate_disconnected when the candidate leaves.

    The CloseEvent reason path already covers normal disconnects, but
    setting the outcome here makes the labeling deterministic when
    close fires due to participant disconnect rather than another path.
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


def _compute_audio_tuning_summary(
    *,
    events: list[dict[str, object]],
    config_snapshot: dict[str, object],
) -> dict[str, object]:
    """Aggregate the EventCollector's events into a tuning summary.

    Pure function — no side effects, no logging. Output shape matches the
    `audio.tuning_summary` event documented in the audio-pipeline spec
    (§7.1).

    Pause inputs come from `audio.user.state` transitions:
    listening→speaking deltas are between-utterance pauses (within a turn).
    For initial implementation, between_turn_ms uses the same
    listening→speaking deltas as a coarse proxy. A more refined
    derivation lands when we have real session data to validate against.
    """
    # Pauses (between-utterance proxy)
    pause_ms: list[int] = []
    last_listening_at: int | None = None
    for ev in events:
        if ev.get("kind") != "audio.user.state":
            continue
        payload = ev.get("payload", {})
        wall_ms = int(ev.get("wall_ms") or 0)
        if isinstance(payload, dict) and payload.get("new_state") == "listening":
            last_listening_at = wall_ms
        elif isinstance(payload, dict) and payload.get("new_state") == "speaking" and last_listening_at is not None:
            pause_ms.append(wall_ms - last_listening_at)
            last_listening_at = None

    pauses_block = {
        "between_utterance_ms": _percentile_stats(pause_ms),
        "between_turn_ms": _percentile_stats(pause_ms),  # proxy until refined
    }

    # Interruptions — derived from OverlappingSpeechEvent.is_interruption (the
    # adaptive classifier's per-event decision) plus AgentFalseInterruptionEvent
    # (post-hoc recovery when the agent yielded but no transcript followed).
    overlap_events = [ev for ev in events if ev.get("kind") == "audio.overlap"]
    true_count = sum(
        1 for ev in overlap_events
        if (ev.get("payload") or {}).get("is_interruption") is True
    )
    ignored_count = sum(
        1 for ev in overlap_events
        if (ev.get("payload") or {}).get("is_interruption") is False
    )
    false_recovered = sum(
        1 for ev in events if ev.get("kind") == "audio.interruption.false"
    )
    interruptions_block = {
        "total": len(overlap_events),
        "true": true_count,
        "ignored_as_backchannel": ignored_count,
        "false_recovered": false_recovered,
        "agent_yielded": true_count,
    }

    # Latency — pull per-component percentiles from the metrics events.
    # Each event's payload is the LiveKit SDK's metrics object (already
    # in seconds), so multiply by 1000 to get ms before percentile-ing.
    def _extract_ms(events_filter: list[dict[str, object]], field: str) -> list[int]:
        out: list[int] = []
        for ev in events_filter:
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            val = payload.get(field)
            if isinstance(val, (int, float)) and val > 0:
                out.append(int(val * 1000))
        return out

    eou_events = [ev for ev in events if ev.get("kind") == "audio.metrics.eou_metrics"]
    llm_events = [ev for ev in events if ev.get("kind") == "audio.metrics.llm_metrics"]
    tts_events = [ev for ev in events if ev.get("kind") == "audio.metrics.tts_metrics"]

    latency_block = {
        "end_of_utterance_delay_ms": _percentile_stats(_extract_ms(eou_events, "end_of_utterance_delay")),
        "transcription_delay_ms": _percentile_stats(_extract_ms(eou_events, "transcription_delay")),
        "llm_ttft_ms": _percentile_stats(_extract_ms(llm_events, "ttft")),
        "tts_ttfb_ms": _percentile_stats(_extract_ms(tts_events, "ttfb")),
    }

    return {
        "pauses": pauses_block,
        "interruptions": interruptions_block,
        "latency": latency_block,
        "config_snapshot": dict(config_snapshot),
    }


async def _handle_close(
    ev: CloseEvent,
    agent: StructuredInterviewAgent,
    *,
    orchestrator: InterviewOrchestrator,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Emit session.close, persist a SessionResult via the orchestrator,
    publish outcome, finalize the audit envelope.
    """
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
            "controller_end_outcome": agent._end_outcome,
        },
        wall_ms=int(time.time() * 1000),
    )

    config_snapshot = {
        "noise_cancellation": ai_config.interview_noise_cancellation,
        "nc_enhancement_level": ai_config.interview_nc_enhancement_level,
        "unlikely_threshold": ai_config.interview_turn_detector_unlikely_threshold,
        "endpointing_max_delay": settings.engine_endpointing_max_delay,
        "vad_provider": "ai_coustics",
    }
    audio_summary = _compute_audio_tuning_summary(
        events=[
            {"kind": e.kind, "payload": e.payload, "wall_ms": e.wall_ms}
            for e in collector.events
        ],
        config_snapshot=config_snapshot,
    )
    collector.append(
        kind="audio.tuning_summary",
        payload=audio_summary,
        wall_ms=int(time.time() * 1000),
    )
    agent._audio_tuning_summary = audio_summary

    if ev.reason == CloseReason.ERROR:
        outcome: SessionOutcome = "error"
    elif agent._end_outcome is not None:
        outcome = agent._end_outcome
    else:
        outcome = "candidate_disconnected"

    if not agent._persisted:
        try:
            await _persist_session_result(
                agent,
                orchestrator=orchestrator,
                tenant_uuid=tenant_uuid,
                correlation_id=correlation_id,
                audio_summary=audio_summary,
                collector=collector,
                sink=sink,
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    await _publish_session_outcome(agent, outcome)
    await _finalize_event_log(
        agent, reason="close_handler", collector=collector, sink=sink,
    )


async def _persist_session_result(
    agent: StructuredInterviewAgent,
    *,
    orchestrator: InterviewOrchestrator,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    audio_summary: dict[str, object],
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Build SessionResult via the orchestrator, finalize the audit
    envelope, attach its sink path/uri, then persist.

    Order matters: we close the audit envelope before recording the
    SessionResult so the persisted row can carry an
    ``audit_envelope_ref`` that points at the durable artifact.
    """
    if agent._persisted:
        return

    result: SessionResult = await orchestrator.on_close(
        agent, audio_tuning_summary=audio_summary,
    )

    envelope_ref: str | None = None
    if not agent._envelope_written and sink is not None:
        agent._envelope_written = True
        closed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            envelope = collector.close(closed_at=closed_at)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "interview_engine.event_log.envelope_validation_failed",
                reason="persist_session_result",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        else:
            try:
                envelope_ref = await asyncio.to_thread(sink.write, envelope)
                log.info(
                    "interview_engine.event_log.written",
                    reason="persist_session_result",
                    target=envelope_ref,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "interview_engine.event_log.sink_write_failed",
                    reason="persist_session_result",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    if envelope_ref is not None:
        result = result.model_copy(update={"audit_envelope_ref": envelope_ref})

    async with get_bypass_session() as db:
        await record_session_result(
            db,
            session_id=uuid.UUID(result.session_id),
            tenant_id=tenant_uuid,
            result=result,
            correlation_id=correlation_id,
        )
        await db.commit()
    agent._persisted = True
    log.info(
        "interview_engine.result.persisted",
        session_id=str(result.session_id),
    )


async def _publish_session_outcome(
    agent: StructuredInterviewAgent,
    outcome: SessionOutcome,
) -> None:
    try:
        room = agent.session.room_io.room
        await room.local_participant.set_attributes(
            {"session_outcome": outcome},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "interview_engine.outcome.publish_failed",
            outcome=outcome,
            error=str(exc),
        )


async def _finalize_event_log(
    agent: StructuredInterviewAgent,
    *,
    reason: str,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Best-effort fallback writer.

    The happy-path flow finalizes the envelope inside
    :func:`_persist_session_result` so the SessionResult can carry an
    ``audit_envelope_ref``. If persistence raised before that point — or
    sink was None — this fallback ensures the envelope still lands on
    disk / S3 when possible.
    """
    if agent._envelope_written or sink is None:
        return
    agent._envelope_written = True
    closed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        envelope = collector.close(closed_at=closed_at)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "interview_engine.event_log.envelope_validation_failed",
            reason=reason,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return
    try:
        target = await asyncio.to_thread(sink.write, envelope)
        log.info(
            "interview_engine.event_log.written",
            reason=reason,
            target=target,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "interview_engine.event_log.sink_write_failed",
            reason=reason,
            error=str(exc),
            error_type=type(exc).__name__,
        )
