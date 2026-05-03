"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Per-session entrypoint:
  1. Parse dispatch metadata (session_id, tenant_id, correlation_id).
  2. Bind structlog contextvars so every log line carries them.
  3. Fetch SessionConfig in-process via build_session_config (no HTTP).
  4. Build InterviewController (per-task watchdog, idle-nudge state
     machine, end_interview_early intent + meta tools).
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
# of plugins that need local model files. Silero (VAD) and turn_detector
# (multilingual EOU) qualify. Session-time instantiation still goes through
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
from app.modules.interview_engine.controller import InterviewController
from app.modules.interview_runtime import build_session_config
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_engine.prompt_hash import hash_prompt_file
from app.modules.tenant_settings import get_tenant_settings


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
    fetched in-process via build_session_config; InterviewController
    persists the session result on close (or via _terminate before
    close, on the normal-end path).
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
        tenant_settings = await get_tenant_settings(db, tenant_uuid)
    log.info(
        "engine.config.fetched",
        question_count=len(config.stage.questions),
        stage_type=config.stage.stage_type,
        tenant_policy=tenant_settings.engine_knockout_policy,
        agent_name_override_active=tenant_settings.engine_agent_name is not None,
    )
    _log_session_setup(config)

    # Phase 1 — audit event log. Build the sink from settings (None when
    # ENGINE_EVENT_LOG_SINK=none) and a per-session collector that the
    # observability listeners feed via append().
    event_sink: EventLogSink | None = build_sink_from_settings()
    # Phase 2 — controller.txt is the live system prompt for InterviewController;
    # task_prompt_hashes is keyed by question_id because the controller dispatches
    # one QuestionTask per question. Phase 2 ships only TechnicalDepthTask, so
    # every question hashes the same task_technical_depth.txt body. When more
    # task kinds land (Phase 3+), build_task_for(...) will determine per-question
    # prompt selection and the hash dict here should mirror that mapping.
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        controller_prompt_hash=hash_prompt_file("interview/controller.txt"),
        task_prompt_hashes={
            q.id: hash_prompt_file("interview/task_technical_depth.txt")
            for q in config.stage.questions
        },
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "turn_detector_unlikely_threshold": str(
                ai_config.interview_turn_detector_unlikely_threshold
            ),
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )

    # Phase 2 — controller-and-tasks architecture. SessionBudget is seeded
    # with monotonic() at construction; on_enter() resets it to the actual
    # session-start monotonic before the question loop runs, so the value
    # passed here is just a placeholder.
    from app.modules.interview_engine.budget import SessionBudget
    from app.modules.interview_engine.idle_nudge import IdleNudgeConfig

    agent = InterviewController(
        session_config=config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
        collector=event_collector,
        idle_nudge_config=IdleNudgeConfig(
            first_nudge_seconds=settings.engine_idle_first_nudge_seconds,
            second_nudge_seconds=settings.engine_idle_second_nudge_seconds,
            give_up_seconds=settings.engine_idle_give_up_seconds,
        ),
        budget=SessionBudget(
            started_at_monotonic=time.monotonic(),
            duration_limit_seconds=config.stage.duration_minutes * 60.0,
            overhead_seconds=settings.engine_task_budget_overhead_seconds,
        ),
        tenant_settings=tenant_settings,
        event_sink=event_sink,
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
            # VAD-based interruption: the local Silero VAD's speech-start
            # signal triggers a barge-in once `min_duration` of speech is
            # observed. Adaptive (transcript-aware) interruption is the
            # higher-quality alternative — it distinguishes intent-to-
            # interrupt from passive backchannels ("uh-huh", "yeah") and
            # noise (cough, throat-clear) by reading the STT transcript
            # — but it requires LiveKit Cloud (the plugin connects to
            # `agent-gateway.livekit.cloud/v1/bargein`, which 401's on
            # self-hosted setups including local `livekit-server --dev`).
            # On a Cloud deployment, switch `mode` back to `"adaptive"`.
            # `resume_false_interruption` recovers the agent's speech if
            # the trigger turns out to be silence/noise within
            # `false_interruption_timeout` (default 2.0s) — works in
            # either mode.
            interruption={
                "mode": "vad",
                "min_duration": 0.5,
                "resume_false_interruption": True,
            },
        ),
    )

    _wire_session_observability(
        session,
        agent=agent,
        collector=event_collector,
        log_verbose_content=settings.engine_log_user_transcripts,
        log_audio_events=settings.engine_log_audio_events,
    )

    _wire_close_handler(session, agent, collector=event_collector, sink=event_sink)
    _wire_participant_disconnect(ctx, agent)

    await session.start(
        agent=agent,
        room=ctx.room,
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
        endpointing_min_delay=settings.engine_endpointing_min_delay,
        endpointing_max_delay=settings.engine_endpointing_max_delay,
        silero_activation_threshold=settings.engine_silero_activation_threshold,
        silero_min_speech_duration=settings.engine_silero_min_speech_duration,
        silero_min_silence_duration=settings.engine_silero_min_silence_duration,
        idle_first_nudge_seconds=settings.engine_idle_first_nudge_seconds,
        idle_second_nudge_seconds=settings.engine_idle_second_nudge_seconds,
        idle_give_up_seconds=settings.engine_idle_give_up_seconds,
        task_budget_overhead_seconds=settings.engine_task_budget_overhead_seconds,
        closing_drain_timeout_seconds=settings.engine_closing_drain_timeout_seconds,
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
    agent: InterviewController,
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
        # Phase 2: drive the InterviewController's idle-nudge state machine.
        agent.on_user_state_changed(ev.new_state)

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
    agent: InterviewController,
    *,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Attach the close-event handler that:
    1. persists the SessionResult on the disconnect/error path (the
       normal-end path persists earlier, in ``_terminate``)
    2. publishes the ``session_outcome`` attribute
    3. closes the EventCollector and writes the envelope to the sink

    Two paths reach close:

    1. Normal end — InterviewController's ``_terminate`` already persisted +
       published ``session_outcome`` and set ``agent._persisted=True``. The
       close handler is a no-op for the SessionResult path; the envelope is
       still written here.
    2. Candidate disconnects mid-session — clicks End Call, refreshes the
       page (which closes their old room session), or network drops past
       the SDK's reconnect window. ``close_on_disconnect=True`` (default)
       fires the AgentSession close with ``reason=PARTICIPANT_DISCONNECTED``.
       The controller's ``on_enter`` task is still running (or was cancelled
       by the disconnect). We attempt a persist via
       ``_persist_session_result(outcome)`` so a candidate that drops
       mid-session still has a partial row written.

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


def _wire_participant_disconnect(
    ctx: JobContext,
    agent: InterviewController,
) -> None:
    """Drive the controller's natural termination path on participant
    disconnect (End Call, page refresh, network drop past reconnect).

    Without this, ``RoomOptions.close_on_disconnect=True`` (LiveKit's
    default) cancels only the in-flight AgentTask. The controller catches
    the cancel, treats it as a forced task completion, and the question
    loop dispatches the *next* question — which queues new generate_reply
    calls onto a session that LiveKit is concurrently trying to drain.
    The drain blocks waiting for queued ops to complete; the queued ops
    block waiting for a session that's closing. Result: AgentSession
    never emits its ``close`` event, ``_handle_close`` never runs,
    ``record_session_result`` is never called, and the DB row stays
    ``state='active'`` — so the candidate's next page-load lands in
    Rejoin mode and waits for an interviewer that won't reappear.

    Mirrors the cancellation pattern used by the controller's
    ``end_interview_early`` tool: set ``_end_outcome='candidate_ended'``
    and cancel the in-flight task. The on_enter loop's next iteration
    sees ``_end_outcome`` is non-None, breaks, and runs ``_terminate``
    which persists, publishes ``session_outcome``, and cleanly shuts
    down the LiveKit session — at which point the AgentSession's close
    event fires and the envelope-writing path in ``_handle_close``
    runs idempotently (``_persisted=True`` short-circuits the persist).
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
            agent._end_outcome = "candidate_ended"
        agent._complete_inflight_task(reason="task_timeout")
        if agent._current_task_run is not None and not agent._current_task_run.done():
            agent._current_task_run.cancel()

    ctx.room.on("participant_disconnected", _on_participant_disconnected)


async def _handle_close(
    ev: CloseEvent,
    agent: InterviewController,
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

    # Append session.close to the audit envelope so the on-disk JSON has a
    # terminal event marking how the session ended. Surface
    # knockout_failures_count from the live controller record.
    knockout_count = len(getattr(agent, "_knockout_failures", []))
    collector.append(
        kind="session.close",
        payload={
            "reason": ev.reason.value,
            "persisted": agent._persisted,
            "knockout_failures_count": knockout_count,
            "has_error": bool(ev.error),
        },
        wall_ms=int(time.time() * 1000),
    )

    outcome = "error" if ev.reason == CloseReason.ERROR else "completed"

    # 1. Persist the SessionResult on the disconnect / error path.
    #
    # InterviewController._terminate() already persists + publishes BEFORE
    # the AgentSession close fires for the normal-end path, so this branch
    # is only meaningful when the controller never reached _terminate
    # (mid-session disconnect / error). SessionOutcome is a closed Literal
    # (completed / knockout_closed / time_expired / candidate_ended /
    # candidate_unresponsive / error). The close handler only fires for
    # paths the controller didn't reach _terminate on — disconnect / error.
    # Map ERROR to "error"; everything else to "completed" since we can't
    # know the controller's intended outcome at this point.
    if not agent._persisted:
        try:
            controller_outcome = (
                "error" if ev.reason == CloseReason.ERROR else "completed"
            )
            await agent._persist_session_result(controller_outcome)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "session.close.persist_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # 2. Publish session_outcome (existing behavior; both classes implement it).
    await agent._publish_session_outcome(outcome)

    # 3. Write the envelope. The controller's `_terminate` already wrote
    # it on the normal path (this is a no-op there). On the disconnect/
    # error path where _terminate didn't run, this is the safety net.
    # The write is fire-and-forget at the asyncio level — if the worker
    # process tears down before this completes, the envelope is lost.
    # That's acceptable for the safety-net case (controller crashed
    # mid-session); the normal path is covered by the inline write in
    # `_terminate`.
    await agent._finalize_event_log(reason="close_handler")
