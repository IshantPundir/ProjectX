"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Connects to LiveKit Cloud (or self-hosted), registers as an available
agent worker, and waits to be dispatched into interview rooms. Each
dispatch creates an InterviewerAgent that conducts a structured
technical interview driven by a deterministic state machine.

Uses direct provider API keys (Deepgram, OpenAI, Cartesia) instead of
the LiveKit inference gateway — cheaper, no shared credit pool, no
gateway rate limits. LiveKit is still used for WebRTC rooms/transport.
"""

import structlog
from dotenv import load_dotenv

from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    TurnHandlingOptions,
    cli,
    room_io,
)
from livekit.plugins import silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import ai_coustics
from livekit.plugins import deepgram
from livekit.plugins import openai
from livekit.plugins import cartesia

from config import InterviewEngineConfig
from context_loader import load_session_config
from agents.interviewer import InterviewerAgent

load_dotenv(".env")

logger = structlog.get_logger("interview-engine")

engine_config = InterviewEngineConfig()

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Prewarm: load Silero VAD at worker startup (not per-session)."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="Dakota-1785")
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint.

    1. Load session config (fixture or room metadata)
    2. Create InterviewerAgent (state machine + system prompt + tool)
    3. Start AgentSession with direct provider STT/LLM/TTS
    """
    session_config = await load_session_config(engine_config)

    logger.info(
        "session.dispatched",
        session_id=session_config.session_id,
        job_title=session_config.job_title,
        candidate=session_config.candidate.name,
        question_count=len(session_config.stage.questions),
    )

    agent = InterviewerAgent(
        session_config=session_config,
        engine_config=engine_config,
    )

    session = AgentSession(
        # Direct provider plugins — each reads its API key from env:
        #   DEEPGRAM_API_KEY, OPENAI_API_KEY, CARTESIA_API_KEY
        stt=deepgram.STT(
            model=engine_config.stt_model,
            language=engine_config.stt_language,
        ),
        llm=openai.LLM(
            model=engine_config.interview_llm_model,
        ),
        tts=cartesia.TTS(
            model=engine_config.tts_model,
            voice=engine_config.tts_voice,
            language=engine_config.tts_language,
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
            preemptive_generation={"enabled": False},
            endpointing={
                "min_delay": engine_config.endpointing_min_delay,
                "max_delay": engine_config.endpointing_max_delay,
            },
        ),
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L,
                ),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
