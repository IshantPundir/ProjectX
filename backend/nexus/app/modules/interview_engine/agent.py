"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Per-session entrypoint:
  1. Parse dispatch metadata (session_id, tenant_id, correlation_id).
  2. Bind structlog contextvars so every log line carries them.
  3. Fetch SessionConfig in-process via build_session_config (no HTTP).
  4. Build InterviewerAgent (state machine + system prompt + tool).
  5. Build AgentSession using app.ai.realtime factories — the engine
     never imports livekit.plugins.* directly except for VAD prewarm,
     which is process-startup-only and not a session-time AI plugin.
  6. Start the session.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

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
    cli,
    room_io,
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
# Process-startup plugin imports. Each of these calls Plugin.register_plugin()
# at module load time, which is what `python agent.py download-files`
# discovers to prewarm model files at container build time. Per the
# `app.ai.realtime` carve-out (see backend/nexus/CLAUDE.md): direct vendor
# SDK imports are forbidden EXCEPT for process-startup model registration
# of plugins that need local model files. Silero (VAD), turn_detector
# (multilingual EOU), and ai_coustics (noise cancellation) all qualify.
# Session-time instantiation still goes through `app.ai.realtime.build_*`.
from livekit.plugins import ai_coustics  # noqa: F401  (download-files registration)
from livekit.plugins import silero
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401

from app.ai.realtime import (
    build_llm_plugin,
    build_noise_cancellation,
    build_stt_plugin,
    build_turn_detector,
    build_tts_plugin,
)
from app.ai.otel import bootstrap_tracer_provider
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.interviewer import InterviewerAgent
from app.modules.interview_runtime import build_session_config
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_engine.prompt_hash import hash_prompt_file


log = structlog.get_logger("interview-engine")
# AgentServer with explicit health-check port. LiveKit Agents auto-spawns
# a health endpoint at host:port/ — 200 when registered with LiveKit,
# 503 otherwise. Locking the port (vs the default random in dev mode)
# so docker-compose's healthcheck: directive can probe a deterministic URL.
server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Process-startup hook.

    1. Bootstrap a TracerProvider so livekit-agents' built-in spans plus
       any explicit spans we add later (Phase 2 tasks) actually ship to
       an aggregator. Production-safe default: no env vars set -> spans
       go nowhere. Setting OTEL_EXPORTER_OTLP_ENDPOINT (Langfuse / Sentry
       / generic OTLP) flips the engine on.
    2. Load Silero VAD into shared process memory.

    Tuning knobs (``activation_threshold``, ``min_speech_duration``,
    ``min_silence_duration``) come from ``InterviewEngineConfig`` so the
    VAD sensitivity can be tuned per-deploy without a code change.
    Lower ``activation_threshold`` makes VAD catch quieter speech at the
    cost of occasional false-positive triggers from background noise.
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
    """Per-session entrypoint.

    Reads dispatch metadata Nexus injected at /start time. Config is
    fetched in-process via build_session_config; InterviewerAgent
    persists the session result on close.
    """
    metadata = json.loads(ctx.job.metadata or "{}")

    # Phase 3 metadata shape: session_id + tenant_id + correlation_id.
    # engine_jwt is gone — RLS + explicit-tenant-filter at the
    # application layer is the new defense.
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

    # In-process config fetch. build_session_config runs on a bypass-RLS
    # session and filters every query by tenant_id explicitly — see
    # app/modules/interview_runtime/service.py docstring.
    async with get_bypass_session() as db:
        config = await build_session_config(
            db,
            session_id=uuid.UUID(session_id),
            tenant_id=tenant_uuid,
        )
    log.info(
        "engine.config.fetched",
        question_count=len(config.stage.questions),
        stage_type=config.stage.stage_type,
    )
    _log_session_setup(config)

    # Phase 1 — audit event log. Build the sink from settings (None when
    # ENGINE_EVENT_LOG_SINK=none) and a per-session collector that the
    # observability listeners feed via append().
    event_sink: EventLogSink | None = build_sink_from_settings()
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        controller_prompt_hash=hash_prompt_file("interview/interviewer.txt"),
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "turn_detector_unlikely_threshold": str(
                ai_config.interview_turn_detector_unlikely_threshold
            ),
            "noise_cancellation_model": ai_config.interview_noise_cancellation_model,
            "noise_cancellation_level": str(
                ai_config.interview_noise_cancellation_level
            ),
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )

    agent = InterviewerAgent(
        session_config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
    )

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            # Preemptive generation = LLM starts inferring before EOU is
            # confirmed, then commits/discards based on final transcript.
            # LiveKit's Agent Builder doc recommends this for all voice
            # agents — the first-token latency drop is significant
            # (often 200-500ms) on conversational turns. Recommended
            # default per docs/agents/multimodality/audio § preemptive
            # generation.
            preemptive_generation={"enabled": True},
            # Dynamic endpointing adapts the delay between min_delay and
            # max_delay based on the candidate's pause statistics across
            # the session. Interview-tuned: candidates pause to think
            # mid-answer, but we don't want to wait the full 2.5s after
            # every short reply. Dynamic mode learns the candidate's
            # rhythm and shortens snappy turns while still tolerating
            # longer thinking pauses on hard questions.
            endpointing={
                "mode": "dynamic",
                "min_delay": settings.engine_endpointing_min_delay,
                "max_delay": settings.engine_endpointing_max_delay,
            },
            # Adaptive interruption is the LiveKit-recommended mode when
            # a turn detector + STT with aligned transcripts are present
            # (Deepgram nova-3 qualifies). Resumes the agent's speech if
            # the interruption turns out to be a false trigger (cough,
            # background noise) within the timeout window.
            interruption={
                "mode": "adaptive",
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

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=build_noise_cancellation(),
            ),
        ),
    )


def _log_session_setup(config) -> None:
    """One-shot startup log written immediately after SessionConfig fetch.

    Captures everything you'd want to compare across runs: model IDs (from
    nexus's AIConfig), engine mechanics (probe budget, time-warning,
    endpointing min/max), and the full question list (id, position,
    mandatory flag, signals, estimated minutes). Without this log you
    can't tell whether a regression came from a model change, an engine
    knob change, or a content change in the question bank.
    """
    log.info(
        "engine.setup.models",
        llm_model=ai_config.interview_llm_model,
        llm_reasoning_effort=ai_config.interview_reasoning_effort,
        stt_model=ai_config.interview_stt_model,
        stt_language=ai_config.interview_stt_language,
        tts_model=ai_config.interview_tts_model,
        tts_voice=ai_config.interview_tts_voice,
        tts_language=ai_config.interview_tts_language,
        turn_detector_unlikely_threshold=ai_config.interview_turn_detector_unlikely_threshold,
    )
    log.info(
        "engine.setup.tuning",
        max_probes_per_question=settings.engine_max_probes_per_question,
        time_warning_threshold=settings.engine_time_warning_threshold,
        endpointing_min_delay=settings.engine_endpointing_min_delay,
        endpointing_max_delay=settings.engine_endpointing_max_delay,
        silero_activation_threshold=settings.engine_silero_activation_threshold,
        silero_min_speech_duration=settings.engine_silero_min_speech_duration,
        silero_min_silence_duration=settings.engine_silero_min_silence_duration,
        noise_cancellation_model=ai_config.interview_noise_cancellation_model,
        noise_cancellation_level=ai_config.interview_noise_cancellation_level,
    )
    log.info(
        "engine.setup.session",
        session_id=config.session_id,
        job_title=config.job_title,
        seniority_level=config.seniority_level,
        candidate_name=config.candidate.name,
        company_industry=config.company.industry,
        stage_type=config.stage.stage_type,
        stage_name=config.stage.name,
        duration_minutes=config.stage.duration_minutes,
        difficulty=config.stage.difficulty,
        total_questions=len(config.stage.questions),
        mandatory_count=sum(1 for q in config.stage.questions if q.is_mandatory),
        optional_count=sum(1 for q in config.stage.questions if not q.is_mandatory),
        signals_total=len(config.signals),
    )
    for q in config.stage.questions:
        log.info(
            "engine.setup.question",
            question_id=q.id,
            position=q.position,
            is_mandatory=q.is_mandatory,
            estimated_minutes=q.estimated_minutes,
            signal_values=q.signal_values,
            text_chars=len(q.text),
        )


def _wire_session_observability(
    session: AgentSession,
    *,
    collector: EventCollector,
    log_verbose_content: bool,
    log_audio_events: bool,
) -> None:
    """Attach structlog + EventCollector listeners covering every AgentSession event.

    Two destinations:
    1. structlog stdout — live debugging, gated behind ``log_audio_events``
       so production can quiet it without losing the durable artifact.
    2. EventCollector — durable per-session audit envelope, always fed
       so a session that crashes mid-flight still has a partial record
       on disk (whatever was written before the crash).

    PII discipline:
    - Always-on payload fields are metadata only (state names, finality
      flags, character counts, token counts, latency numbers, error types).
    - Verbose content (verbatim STT transcripts, LLM message bodies,
      function-tool args/outputs) is gated TWICE: structlog by
      ``log_verbose_content``, and the EventCollector by its own
      ``redaction_mode``. Production runs both at minimum (audio events
      on, verbose off, metadata redaction).
    """
    state: dict[str, float | None] = {"t0_monotonic": None}

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
            # Always include the *keys* of arguments (no values) so audit
            # replay can see which args the LLM produced without leaking
            # their content.
            try:
                arg_keys = list(getattr(call, "arguments", {}) or {})
            except Exception:  # noqa: BLE001
                arg_keys = []
            payload["argument_keys"] = arg_keys
            if log_verbose_content:
                payload["arguments"] = getattr(call, "arguments", None)
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

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
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
        # Errors bypass the log_audio_events gate — always log.
        log.error("audio.pipeline.error", **payload, **_ts(ev.created_at))
        collector.append(
            kind="audio.pipeline.error",
            payload=payload,
            wall_ms=int(ev.created_at * 1000),
        )


def _wire_close_handler(
    session: AgentSession,
    agent: InterviewerAgent,
    *,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Attach the close-event handler that:
    1. persists the SessionResult (existing behavior)
    2. publishes the session_outcome attribute (existing behavior)
    3. closes the EventCollector and writes the envelope to the sink (Phase 1 NEW)

    Two paths reach close:

    1. State machine emits Action.CLOSE → ``record_observation`` already
       persisted + published ``session_outcome='completed'`` and set
       ``agent._persisted=True``. The close handler is a no-op for the
       SessionResult path; the envelope is still written here.
    2. Candidate disconnects mid-session — clicks End Call, refreshes the
       page (which closes their old room session), or network drops past
       the SDK's reconnect window. ``close_on_disconnect=True`` (default)
       fires the AgentSession close with ``reason=PARTICIPANT_DISCONNECTED``.
       The state machine never reached CLOSE; we persist a partial result
       here, AND we write a partial envelope.

    ``ERROR`` reason (LLM/STT/TTS plugin error) routes outcome='error' so the
    frontend's ``useSessionOutcome`` hook surfaces ``DisconnectError`` with
    ``ENGINE_ERROR`` rather than ``CompletionScreen``. The envelope is still
    written for forensic review.
    """

    _bg_tasks: set[asyncio.Task[None]] = set()

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        task = asyncio.create_task(_handle_close(ev, agent, collector, sink))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


async def _handle_close(
    ev: CloseEvent,
    agent: InterviewerAgent,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Async body of the close-event handler. See ``_wire_close_handler`` docstring."""
    log = structlog.get_logger("interview-engine")
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    # Phase 1 — also append session.close to the audit envelope so the
    # on-disk JSON has a terminal event marking how the session ended.
    # knockout_failures_count is 0 in Phase 1; Phase 2's controller will
    # populate it with the real count.
    collector.append(
        kind="session.close",
        payload={
            "reason": ev.reason.value,
            "persisted": agent._persisted,
            "knockout_failures_count": 0,
            "has_error": bool(ev.error),
        },
        wall_ms=int(time.time() * 1000),
    )

    outcome = "error" if ev.reason == CloseReason.ERROR else "completed"

    # 1. Persist the SessionResult (existing behavior).
    if not agent._persisted:
        try:
            result = agent._build_session_result()
            await agent._persist_result(result)
            agent._persisted = True
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # 2. Publish session_outcome (existing behavior).
    await agent._publish_session_outcome(outcome)

    # 3. Phase 1 — close the EventCollector and write the envelope.
    if sink is not None:
        envelope = collector.close(
            closed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        try:
            target = await asyncio.to_thread(sink.write, envelope)
            log.info("session.close.event_log_written", target=target)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.event_log_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
