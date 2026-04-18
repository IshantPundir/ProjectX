"""ProjectX Interview Engine — LiveKit Agent entrypoint.

Connects to LiveKit Cloud (or self-hosted), registers as an available
agent worker, and waits to be dispatched into interview rooms. Each
dispatch creates an InterviewerAgent that conducts a structured
technical interview driven by a deterministic state machine.
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
    inference,
    room_io,
)
from livekit.plugins import silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import ai_coustics

from config import InterviewEngineConfig
from context_loader import load_session_config
from output_parser import create_output_processor
from agents.interviewer import InterviewerAgent

load_dotenv(".env")

logger = structlog.get_logger("interview-engine")

# Load engine config once at module level
engine_config = InterviewEngineConfig()

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Prewarm: load Silero VAD at worker startup (not per-session)."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="Dakota-1785")
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint. Called when LiveKit dispatches this agent to a room.

    1. Load session config (from fixture or room metadata)
    2. Create the InterviewerAgent (builds state machine + system prompt)
    3. Wire the structured output parser into tts_text_transforms
    4. Start the AgentSession
    """
    # 1. Load session context
    session_config = await load_session_config(engine_config)

    logger.info(
        "session.dispatched",
        session_id=session_config.session_id,
        job_title=session_config.job_title,
        candidate=session_config.candidate.name,
        question_count=len(session_config.stage.questions),
    )

    # 2. Create the interview agent
    agent = InterviewerAgent(
        session_config=session_config,
        engine_config=engine_config,
    )

    # 3. Create the output processor wired to the agent's observation callback.
    #    The processor is a Callable[[AsyncIterable[str]], AsyncIterable[str]]
    #    which matches the TextTransforms callable variant accepted by
    #    tts_text_transforms. It streams the "response" field to TTS while
    #    accumulating the full JSON to extract SteeringObservation.
    output_processor = create_output_processor(
        on_observation=agent._on_observation,
        on_complete=agent._on_stream_complete,
    )

    # 4. Build the AgentSession with config-driven model strings
    session = AgentSession(
        stt=inference.STT(
            model=engine_config.stt_model,
            language=engine_config.stt_language,
        ),
        llm=inference.LLM(
            model=engine_config.interview_llm_model,
            extra_kwargs={"reasoning_effort": engine_config.interview_reasoning_effort},
        ),
        tts=inference.TTS(
            model=engine_config.tts_model,
            voice=engine_config.tts_voice,
            language=engine_config.tts_language,
        ),
        tts_text_transforms=[output_processor],
        turn_handling=TurnHandlingOptions(
            turn_detection=MultilingualModel(),
        ),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # 5. Start the session
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
