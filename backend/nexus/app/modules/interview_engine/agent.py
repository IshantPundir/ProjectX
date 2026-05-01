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
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.interviewer import InterviewerAgent
from app.modules.interview_runtime import build_session_config


log = structlog.get_logger("interview-engine")
# AgentServer with explicit health-check port. LiveKit Agents auto-spawns
# a health endpoint at host:port/ — 200 when registered with LiveKit,
# 503 otherwise. Locking the port (vs the default random in dev mode)
# so docker-compose's healthcheck: directive can probe a deterministic URL.
server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Load Silero VAD into shared process memory at worker startup.

    Tuning knobs (``activation_threshold``, ``min_speech_duration``,
    ``min_silence_duration``) come from ``InterviewEngineConfig`` so the
    VAD sensitivity can be tuned per-deploy without a code change.
    Lower ``activation_threshold`` makes VAD catch quieter speech at the
    cost of occasional false-positive triggers from background noise.
    """
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

    if settings.engine_log_audio_events:
        _wire_session_observability(
            session,
            log_verbose_content=settings.engine_log_user_transcripts,
        )

    _wire_close_handler(session, agent)

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
    session: AgentSession, *, log_verbose_content: bool
) -> None:
    """Attach structlog listeners covering every AgentSession event.

    Each record carries ``elapsed_ms`` (relative to the first observed
    event in this session) and ``wall_ms`` (event ``created_at`` rounded
    to ms) so per-turn latency waterfalls can be reconstructed by
    grepping for a session_id and sorting by ``elapsed_ms``.

    The ``contextvars`` (``session_id``, ``correlation_id``) bound in
    ``entrypoint`` flow through automatically, so every record is already
    scoped to its session.

    PII discipline:
    - Always-on fields are metadata only (state names, finality flags,
      character counts, token counts, latency numbers, error types).
    - Verbose content (verbatim STT transcripts, LLM message bodies,
      function-tool args/outputs) is gated behind ``log_verbose_content``
      and must never be enabled in production. See root CLAUDE.md PII
      discipline rule.
    """
    # Captured on the first event so elapsed_ms is meaningful for every
    # subsequent record. Mutable container so the closure can write it.
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

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        # VAD-driven. listening -> speaking = candidate started talking;
        # speaking -> listening = candidate stopped. If you never see this
        # transition while you're talking, VAD isn't picking up your voice.
        log.info(
            "audio.user.state",
            old_state=ev.old_state,
            new_state=ev.new_state,
            **_ts(ev.created_at),
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        # listening -> thinking = LLM call kicked off.
        # thinking -> speaking = TTS playback started.
        # The gap between thinking and speaking is the LLM TTFT + TTS TTFB.
        log.info(
            "audio.agent.state",
            old_state=ev.old_state,
            new_state=ev.new_state,
            **_ts(ev.created_at),
        )

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
        kwargs: dict[str, object] = {
            "is_final": ev.is_final,
            "transcript_chars": len(ev.transcript),
            "language": str(ev.language) if ev.language else None,
            "speaker_id": ev.speaker_id,
        }
        if log_verbose_content:
            kwargs["transcript"] = ev.transcript
        log.info("audio.stt.transcribed", **kwargs, **_ts(ev.created_at))

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        # Per-pipeline-stage metrics. The metrics object's ``type`` field
        # is one of: vad_metrics, eou_metrics, stt_metrics, llm_metrics,
        # tts_metrics, realtime_model_metrics. Discriminate on type so
        # the resulting log records are easy to grep
        # (e.g. ``grep audio.metrics.llm`` for LLM TTFT and tokens).
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        log.info(f"audio.metrics.{m.type}", **payload, **_ts(ev.created_at))

    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        # Fired every time a chat message lands in the conversation
        # history: user turns (post-STT-final) and assistant turns
        # (LLM response, including any function-tool calls). The role
        # + chars give shape; verbose content gives the actual body
        # for prompt tuning.
        item = ev.item
        role = getattr(item, "role", None) or getattr(item, "type", None)
        content_text = getattr(item, "text_content", None)
        if callable(content_text):
            try:
                content_text = content_text()
            except Exception:  # noqa: BLE001
                content_text = None
        kwargs: dict[str, object] = {
            "role": role,
            "item_type": getattr(item, "type", None),
        }
        if isinstance(content_text, str):
            kwargs["content_chars"] = len(content_text)
            if log_verbose_content:
                kwargs["content"] = content_text
        log.info("llm.message.added", **kwargs, **_ts(ev.created_at))

    @session.on("function_tools_executed")
    def _on_tools_executed(ev: FunctionToolsExecutedEvent) -> None:
        # The LLM called one or more @function_tools. For us the only
        # tool is record_observation, but log defensively in case more
        # are added. The args + output are the load-bearing fields for
        # debugging "did the LLM observe the right thing?" — they're
        # PII-gated behind log_verbose_content.
        for call, output in ev.zipped():
            kwargs: dict[str, object] = {
                "tool_name": call.name,
                "tool_call_id": getattr(call, "call_id", None),
                "has_output": output is not None,
                "output_is_error": (
                    bool(getattr(output, "is_error", False)) if output else None
                ),
            }
            if log_verbose_content:
                kwargs["arguments"] = getattr(call, "arguments", None)
                kwargs["output"] = (
                    getattr(output, "output", None) if output else None
                )
            log.info("llm.tool.executed", **kwargs, **_ts(ev.created_at))

    @session.on("agent_false_interruption")
    def _on_false_interruption(ev: AgentFalseInterruptionEvent) -> None:
        # Adaptive interruption decided a sound burst (cough, background
        # noise, brief candidate ack) wasn't a real interruption. resumed=
        # True means the agent's speech kept going. False means it was
        # cut off — adjust min_duration / interruption mode if you see
        # too many of these.
        log.info(
            "audio.interruption.false",
            resumed=ev.resumed,
            **_ts(ev.created_at),
        )

    @session.on("overlapping_speech")
    def _on_overlap(ev: OverlappingSpeechEvent) -> None:
        log.info(
            "audio.overlap",
            **_ts(getattr(ev, "created_at", time.time())),
        )

    @session.on("session_usage_updated")
    def _on_usage(ev: SessionUsageUpdatedEvent) -> None:
        try:
            usage = ev.usage.model_dump()
        except Exception:  # noqa: BLE001
            usage = {"raw": str(ev.usage)}
        log.info("session.usage", **usage, **_ts(ev.created_at))

    @session.on("speech_created")
    def _on_speech_created(ev: SpeechCreatedEvent) -> None:
        # source='generate_reply' = LLM-driven turn; source='say' = direct
        # TTS injection (we use this for the greeting). user_initiated=
        # True means our own code created it (vs an internal LiveKit-
        # driven generation).
        log.info(
            "audio.speech.created",
            source=ev.source,
            user_initiated=ev.user_initiated,
            **_ts(ev.created_at),
        )

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        log.error(
            "audio.pipeline.error",
            source=type(ev.source).__name__,
            error=str(ev.error),
            error_type=type(ev.error).__name__,
            **_ts(ev.created_at),
        )


def _wire_close_handler(
    session: AgentSession, agent: InterviewerAgent
) -> None:
    """Attach the close-event handler that persists a final result + publishes
    the session_outcome attribute when the AgentSession ends.

    Two paths reach close:

    1. State machine emits Action.CLOSE → ``record_observation`` already
       persisted + published ``session_outcome='completed'`` and set
       ``agent._persisted=True``. The close handler is a no-op in that case
       because ``_persist_result`` is idempotent on the Nexus side and we
       don't want to overwrite the prior completed outcome with another
       'completed' (harmless but noisy).
    2. Candidate disconnects mid-session — clicks End Call, refreshes the
       page (which closes their old room session), or network drops past
       the SDK's reconnect window. ``close_on_disconnect=True`` (default)
       fires the AgentSession close with ``reason=PARTICIPANT_DISCONNECTED``.
       The state machine never reached CLOSE; we persist a partial result
       here so Nexus transitions session.state → 'completed' and the
       candidate's wizard pre-check on next visit doesn't show
       'Rejoin your interview' for an effectively-ended session.

    ``ERROR`` reason (LLM/STT/TTS plugin error) routes outcome='error' so the
    frontend's ``useSessionOutcome`` hook surfaces ``DisconnectError`` with
    ``ENGINE_ERROR`` rather than ``CompletionScreen``.
    """

    @session.on("close")
    def _on_close(ev: CloseEvent) -> None:
        # Spawn an asyncio task — the close event is emitted before room IO
        # tears down, so the LiveKit room is still alive long enough to
        # publish the outcome attribute.
        asyncio.create_task(_handle_close(ev, agent))


async def _handle_close(ev: CloseEvent, agent: InterviewerAgent) -> None:
    """Async body of the close-event handler. See ``_wire_close_handler`` docstring."""
    log = structlog.get_logger("interview-engine")
    log.info(
        "session.close",
        reason=ev.reason.value,
        has_error=bool(ev.error),
        already_persisted=agent._persisted,
    )

    outcome = "error" if ev.reason == CloseReason.ERROR else "completed"

    # Persist a final/partial result if the state machine didn't already.
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

    # Publish session_outcome so the candidate's frontend
    # useSessionOutcome hook reads it on the Disconnected event.
    await agent._publish_session_outcome(outcome)
