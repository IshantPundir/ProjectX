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
    JobContext,
    JobProcess,
    TurnHandlingOptions,
    cli,
    room_io,
)
# silero is the only `livekit.plugins.*` import allowed in this file —
# VAD prewarm runs once at process startup, not per-session, so it does
# not go through the app.ai.realtime factory layer.
from livekit.plugins import silero

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

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=build_noise_cancellation(),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
