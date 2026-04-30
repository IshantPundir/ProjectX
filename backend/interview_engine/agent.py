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

import json

import structlog

from livekit.agents import (
    AgentServer,
    AgentSession,
    AgentStateChangedEvent,
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
            preemptive_generation={"enabled": False},
            endpointing={
                "min_delay": engine_cfg.endpointing_min_delay,
                "max_delay": engine_cfg.endpointing_max_delay,
            },
        ),
    )

    if engine_cfg.log_audio_events:
        _wire_audio_observability(session, log_transcripts=engine_cfg.log_user_transcripts)

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


if __name__ == "__main__":
    cli.run_app(server)
