"""Interview Engine v2 — LiveKit entrypoint (M1 proof-of-life).

M1 scope: confirm the v2 route is taken, connect, write the v2 audit envelope, and
speak ONE canned line (proof-of-life via the existing TTS plugin). Turn-taking, the
mouth, and the brain land in M3-M5. This module imports livekit; it is only ever
imported lazily (via interview_engine_v2.__getattr__('run')) inside the engine
container, so the FastAPI/nexus process never loads livekit.
"""

from __future__ import annotations

import time
import uuid

import structlog
from livekit.agents import Agent, AgentSession, JobContext

from app.ai.realtime import build_tts_plugin
from app.modules.interview_engine_v2.event_log.collector import EventCollector
from app.modules.interview_runtime import SessionConfig

log = structlog.get_logger("interview_engine_v2")


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """v2 per-session run. M1: proof-of-life only."""
    collector = EventCollector(
        session_id=config.session_id,
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    collector.record(
        "engine.v2.dispatched",
        {"job_title": config.job_title, "question_count": len(config.stage.questions)},
        t_ms=0,
        wall_ms=_now_ms(),
    )
    log.info("engine.v2.boot", job_title=config.job_title,
             question_count=len(config.stage.questions))

    await ctx.connect()
    participant = await ctx.wait_for_participant()
    log.info("engine.v2.participant.joined",
             participant_identity=getattr(participant, "identity", None))

    session = AgentSession(tts=build_tts_plugin())
    await session.start(room=ctx.room, agent=Agent(instructions=""))
    name = config.candidate.name or "there"
    await session.say(
        f"Hi {name}. This is the version two interview engine, just confirming the "
        f"connection works. We'll continue shortly."
    )
    collector.record("engine.v2.proof_of_life_spoken", {}, t_ms=_now_ms(), wall_ms=_now_ms())

    # Dev-only: publish session_outcome so the candidate screen shows the ended state
    # instead of hanging. Set the attribute directly (no dependency on v1's
    # frontend_attributes.AttributePublisher — keeps v2 self-contained for the M6 v1
    # deletion). frontend/session reads this attr to render the ended screen.
    try:
        await ctx.room.local_participant.set_attributes({"session_outcome": "completed"})
    except Exception:  # never let a cosmetic attr failure crash the proof-of-life
        log.warning("engine.v2.session_outcome.publish_failed", exc_info=True)

    # NOTE: M1 does NOT call record_session_result (the full SessionResult contract is
    # produced by the brain/coverage path in M5 — see master plan CMI-1). The session
    # row therefore stays 'active'; run against throwaway test jobs only. The envelope
    # is held in memory (sink wiring lands in M6).
    log.info("engine.v2.proof_of_life.complete",
             events=len(collector.envelope().events))
    await session.aclose()
