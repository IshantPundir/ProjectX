"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Per-session entrypoint:
  1. Parse dispatch metadata (session_id, engine_jwt, correlation_id).
  2. Bind structlog contextvars so every log line carries them.
  3. Fetch SessionConfig from nexus's /api/internal/sessions/{id}/config.
  4. Build InterviewerAgent (state machine + system prompt + tool).
  5. Build AgentSession using app.ai.realtime factories — the engine
     never imports livekit.plugins.* directly except for VAD prewarm,
     which is process-startup-only and not a session-time AI plugin.
  6. Start the session.
"""

from __future__ import annotations

import asyncio
import json

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
from livekit.agents.voice.events import CloseReason
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
from agents.interviewer import InterviewerAgent
from config import InterviewEngineConfig
from nexus_client import fetch_session_config


log = structlog.get_logger("interview-engine")
engine_cfg = InterviewEngineConfig()
server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Load Silero VAD into shared process memory at worker startup."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=engine_cfg.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint.

    Reads dispatch metadata Nexus injected at /start time. The engine_jwt
    is single-use per (jti, endpoint) — fetch_session_config consumes the
    'config' slot; InterviewerAgent (Task 5.8) consumes the 'results' slot
    on close.
    """
    metadata = json.loads(ctx.job.metadata or "{}")

    # Required keys. Missing keys will raise KeyError, which surfaces in
    # logs as a worker error — desired loud failure if Nexus's dispatcher
    # ever sends a malformed payload.
    session_id = metadata["session_id"]
    engine_jwt = metadata["engine_jwt"]
    correlation_id = metadata.get("correlation_id", session_id)

    structlog.contextvars.bind_contextvars(
        session_id=session_id,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=engine_cfg.agent_name)

    config = await fetch_session_config(
        session_id=session_id,
        jwt=engine_jwt,
        base_url=engine_cfg.nexus_internal_base_url,
    )
    log.info(
        "engine.config.fetched",
        question_count=len(config.stage.questions),
        stage_type=config.stage.stage_type,
    )

    agent = InterviewerAgent(
        session_config=config,
        engine_config=engine_cfg,
        nexus_jwt=engine_jwt,
        nexus_base_url=engine_cfg.nexus_internal_base_url,
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
                "min_delay": engine_cfg.endpointing_min_delay,
                "max_delay": engine_cfg.endpointing_max_delay,
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

    if engine_cfg.log_audio_events:
        _wire_audio_observability(session, log_transcripts=engine_cfg.log_user_transcripts)

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


def _wire_audio_observability(
    session: AgentSession, *, log_transcripts: bool
) -> None:
    """Attach structlog listeners to the AgentSession so an operator can see
    what the audio pipeline is doing turn-by-turn.

    The listeners are scoped to a single session (registered fresh each
    entrypoint call). Each callback runs synchronously inside the
    AgentSession event loop and contextvars (``session_id``,
    ``correlation_id``) are still bound, so every emitted record carries
    them automatically.

    PII discipline: ``audio.stt.transcribed`` always logs character count
    + finality flag, but the actual transcript text is gated behind
    ``log_transcripts=True``. Raw transcripts are PII per the root
    CLAUDE.md and must never be enabled outside dev / local.
    """

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        # VAD-driven. listening -> speaking = candidate started talking;
        # speaking -> listening = candidate stopped. If you never see this
        # transition while you're talking, VAD isn't picking up your voice.
        log.info(
            "audio.user.state",
            old_state=ev.old_state,
            new_state=ev.new_state,
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        # listening -> thinking = LLM call kicked off.
        # thinking -> speaking = TTS playback started.
        log.info(
            "audio.agent.state",
            old_state=ev.old_state,
            new_state=ev.new_state,
        )

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: UserInputTranscribedEvent) -> None:
        kwargs: dict[str, object] = {
            "is_final": ev.is_final,
            "transcript_chars": len(ev.transcript),
            "language": str(ev.language) if ev.language else None,
        }
        if log_transcripts:
            kwargs["transcript"] = ev.transcript
        log.info("audio.stt.transcribed", **kwargs)

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        # Per-pipeline-stage metrics. The metrics object's ``type`` field
        # is one of: vad_metrics, eou_metrics, stt_metrics, llm_metrics,
        # tts_metrics. Discriminate on type so the resulting log records
        # are easy to grep (e.g. ``grep audio.metrics.eou`` for EOU delays).
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        log.info(f"audio.metrics.{m.type}", **payload)

    @session.on("error")
    def _on_error(ev: ErrorEvent) -> None:
        log.error(
            "audio.pipeline.error",
            source=type(ev.source).__name__,
            error=str(ev.error),
            error_type=type(ev.error).__name__,
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


if __name__ == "__main__":
    cli.run_app(server)
