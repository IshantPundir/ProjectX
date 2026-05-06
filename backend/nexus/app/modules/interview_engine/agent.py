"""ProjectX Interview Engine — generic LLM chatbot entrypoint.

Clean-slate rewrite (post-2026-05-06). The structured Phase A/B/C agent
(state machine + signal scoreboard + templated speech layer + evaluators)
has been removed. This file is now the LiveKit Agent harness for a
basic LLM-driven chatbot, primed with the job context (title, role
summary, question bank as inspiration, target duration). The framework's
default conversation loop drives every turn — STT → LLM → TTS — with no
state machine in front of it.

What stays from the prior phases:
  * Audio pipeline (STT/LLM/TTS/VAD/turn-detector via app.ai.realtime).
  * Audit envelope (EventCollector + sink) capturing audio events,
    transcripts, conversation messages, and session.close.
  * SessionConfig fetch on dispatch and SessionResult write on close
    via the in-process app.modules.interview_runtime service.
  * session_outcome participant attribute publication for the
    candidate frontend.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Literal

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
# local model files. Silero (VAD) and turn_detector (multilingual EOU)
# qualify. Session-time instantiation still goes through
# `app.ai.realtime.build_*`.
from livekit.plugins import silero
from livekit.plugins.turn_detector import multilingual as _turn_detector_multilingual  # noqa: F401
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

from app.ai.config import ai_config
from app.ai.otel import bootstrap_tracer_provider
from app.ai.realtime import (
    build_llm_plugin,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
)
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.event_log import (
    EventCollector,
    EventLogSink,
    build_sink_from_settings,
)
from app.modules.interview_runtime import (
    QuestionResult,
    SessionConfig,
    SessionResult,
    TranscriptEntry,
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

    1. Bootstrap a TracerProvider so livekit-agents' built-in spans ship
       to whatever aggregator the operator points OTLP at. Production-
       safe default: no env vars set -> spans go nowhere.
    2. Load Silero VAD into shared process memory.
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


class GenericInterviewAgent(Agent):
    """Basic LLM-driven chatbot agent.

    No state machine, no per-question scoring, no follow-up logic. The
    framework's default conversation loop (STT → LLM → TTS) drives every
    turn; this subclass exists only to (a) override on_enter so the agent
    speaks first, and (b) hold a typed reference to the session config
    that the close handler needs to compose the SessionResult.
    """

    def __init__(
        self,
        *,
        config: SessionConfig,
        instructions: str,
    ) -> None:
        self._config = config
        self._end_outcome: SessionOutcome | None = None
        self._persisted = False
        self._envelope_written = False
        self._session_start_monotonic: float = time.monotonic()
        super().__init__(instructions=instructions)

    async def on_enter(self) -> None:
        """LiveKit calls this on session.start().

        Reset the session-start monotonic anchor so transcript timestamps
        are measured from the actual conversation start, not the agent
        construction. Then trigger the first agent utterance — the
        framework runs the LLM with our system prompt + an
        "introduce yourself" nudge and streams the result into TTS.
        From that point, the framework drives the STT → LLM → TTS
        conversation loop on its own.
        """
        self._session_start_monotonic = time.monotonic()
        candidate_first_name = (
            self._config.candidate.name.split(" ")[0]
            if self._config.candidate.name else "there"
        )
        log.info(
            "interview_engine.on_enter",
            session_id=self._config.session_id,
            candidate_name=self._config.candidate.name,
            job_title=self._config.job_title,
            question_count=len(self._config.stage.questions),
        )
        # Fire-and-forget. The framework owns playout; we don't await
        # so on_enter returns promptly and the framework can move on.
        self.session.generate_reply(
            instructions=(
                f"Greet the candidate {candidate_first_name} by their first "
                f"name and begin the interview naturally. Don't recite the "
                f"role description back to them — just start the conversation."
            ),
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
        candidate_name=config.candidate.name,
        job_title=config.job_title,
    )

    agent_name = tenant_settings.engine_agent_name or settings.engine_agent_name
    system_prompt = _build_system_prompt(config=config, agent_name=agent_name)
    prompt_hash = "sha256:" + hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    event_sink: EventLogSink | None = build_sink_from_settings()
    event_collector = EventCollector(
        session_id=session_id,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
        controller_prompt_hash=prompt_hash,
        task_prompt_hashes={},
        model_versions={
            "llm": ai_config.interview_llm_model,
            "stt": ai_config.interview_stt_model,
            "tts": ai_config.interview_tts_model,
            "turn_detector_unlikely_threshold": (
                f"{ai_config.interview_turn_detector_unlikely_threshold}"
                if ai_config.interview_turn_detector_unlikely_threshold is not None
                else "null"
            ),
        },
        redaction_mode=settings.engine_event_log_redaction,
    )
    log.info(
        "engine.event_log.opened",
        sink=settings.engine_event_log_sink,
        redaction=settings.engine_event_log_redaction,
    )

    agent = GenericInterviewAgent(config=config, instructions=system_prompt)

    # Mutable list captured by the conversation_item_added handler and
    # consumed by the close handler. Built up incrementally as the
    # framework appends items to the LLM's chat context.
    transcript_entries: list[TranscriptEntry] = []

    session = AgentSession(
        stt=build_stt_plugin(),
        llm=build_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(),
            preemptive_generation={"enabled": True},
            endpointing={
                "mode": "dynamic",
                "min_delay": settings.engine_endpointing_min_delay,
                "max_delay": settings.engine_endpointing_max_delay,
            },
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
        transcript_entries=transcript_entries,
        log_verbose_content=settings.engine_log_user_transcripts,
        log_audio_events=settings.engine_log_audio_events,
    )

    _wire_close_handler(
        session,
        agent,
        tenant_uuid=tenant_uuid,
        correlation_id=correlation_id,
        transcript_entries=transcript_entries,
        collector=event_collector,
        sink=event_sink,
    )
    _wire_participant_disconnect(ctx, agent)

    await session.start(agent=agent, room=ctx.room)


def _build_system_prompt(*, config: SessionConfig, agent_name: str) -> str:
    """Build the chatbot's system prompt from SessionConfig (Option B).

    Knows the job title, role summary, question bank (as inspiration,
    not as a script), candidate first name, and the target duration.
    No state machine, no rubric-grounded scoring — just framing for a
    natural conversation.
    """
    if config.stage.questions:
        question_lines = "\n".join(
            f"{i + 1}. {q.text}"
            for i, q in enumerate(config.stage.questions)
        )
    else:
        question_lines = "(no specific questions provided)"

    candidate_first_name = (
        config.candidate.name.split(" ")[0]
        if config.candidate.name else "the candidate"
    )

    return f"""You are {agent_name}, an AI conducting a brief technical \
screening interview for the {config.job_title} role.

# About the role
{config.role_summary}

# Topics you can draw from
Use these as inspiration. Stay conversational — you don't have to ask \
all of them or follow this order:

{question_lines}

# Goal
Have a natural conversation with {candidate_first_name} for about \
{config.stage.duration_minutes} minutes. Listen actively, ask follow-ups \
when something is unclear or interesting, and keep the conversation \
flowing. Be friendly but professional.

# Hard rules
- Do not promise outcomes, salary figures, or next steps — the \
recruiting team handles that.
- Do not give the candidate examples of what a good answer looks like.
- Speak naturally, as a human interviewer would. Plain spoken English. \
No markdown, no formatting, no lists.
- Keep your turns short. The candidate should be doing most of the \
talking.
"""


def _wire_session_observability(
    session: AgentSession,
    *,
    agent: GenericInterviewAgent,
    collector: EventCollector,
    transcript_entries: list[TranscriptEntry],
    log_verbose_content: bool,
    log_audio_events: bool,
) -> None:
    """Attach EventCollector + structlog listeners. PII gating preserved."""
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
            # Build SessionResult.full_transcript inline. The wire format
            # only carries "agent" / "candidate"; map LiveKit's assistant
            # / user roles. Skip system, tool_call, etc. — those land in
            # the audit envelope but not in the candidate-readable
            # transcript.
            if role in ("assistant", "user"):
                wire_role: Literal["agent", "candidate"] = (
                    "agent" if role == "assistant" else "candidate"
                )
                ts_ms = max(
                    0,
                    int(
                        (time.monotonic() - agent._session_start_monotonic)
                        * 1000
                    ),
                )
                transcript_entries.append(
                    TranscriptEntry(
                        role=wire_role,
                        text=content_text,
                        timestamp_ms=ts_ms,
                    )
                )
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
        ev_created = getattr(ev, "created_at", time.time())
        _emit("audio.overlap", {}, ev_created)

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
    agent: GenericInterviewAgent,
    *,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    transcript_entries: list[TranscriptEntry],
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
                tenant_uuid=tenant_uuid,
                correlation_id=correlation_id,
                transcript_entries=transcript_entries,
                collector=collector,
                sink=sink,
            )
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)


def _wire_participant_disconnect(
    ctx: JobContext,
    agent: GenericInterviewAgent,
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
        payload = ev.get("payload") or {}
        wall_ms = int(ev.get("wall_ms") or 0)
        if isinstance(payload, dict) and payload.get("new_state") == "listening":
            last_listening_at = wall_ms
        elif isinstance(payload, dict) and payload.get("new_state") == "speaking" and last_listening_at is not None:
            pause_ms.append(wall_ms - last_listening_at)
            last_listening_at = None

    def _pct(values: list[int]) -> dict[str, int]:
        if not values:
            return {"p50": 0, "p95": 0, "max": 0, "n": 0}
        sorted_values = sorted(values)
        n = len(sorted_values)
        # True median: average two middle values when n is even
        if n % 2 == 0:
            p50 = (sorted_values[n // 2 - 1] + sorted_values[n // 2]) // 2
        else:
            p50 = sorted_values[n // 2]
        p95 = sorted_values[min(n - 1, int(n * 0.95))]
        return {"p50": p50, "p95": p95, "max": sorted_values[-1], "n": n}

    pauses_block = {
        "between_utterance_ms": _pct(pause_ms),
        "between_turn_ms": _pct(pause_ms),  # proxy until refined
    }

    # Interruptions
    false_count = sum(1 for ev in events if ev.get("kind") == "audio.interruption.false")
    overlap_count = sum(1 for ev in events if ev.get("kind") == "audio.overlap")
    total = false_count + overlap_count
    true_count = max(0, overlap_count - false_count)
    interruptions_block = {
        "total": total,
        "true": true_count,
        "false": false_count,
        "agent_yielded": false_count,
    }

    # Latency: leave at zero in initial implementation; tighten when
    # validated against real telemetry shape.
    latency_block = {
        "stt_to_eou_ms": {"p50": 0, "p95": 0},
        "eou_to_first_audio_ms": {"p50": 0, "p95": 0},
    }

    return {
        "pauses": pauses_block,
        "interruptions": interruptions_block,
        "latency": latency_block,
        "config_snapshot": dict(config_snapshot),
    }


async def _handle_close(
    ev: CloseEvent,
    agent: GenericInterviewAgent,
    *,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    transcript_entries: list[TranscriptEntry],
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
    """Emit session.close, persist a SessionResult, publish outcome,
    finalize the audit envelope.
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
                tenant_uuid=tenant_uuid,
                correlation_id=correlation_id,
                transcript_entries=transcript_entries,
                outcome=outcome,
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


def _build_session_result(
    agent: GenericInterviewAgent,
    *,
    transcript_entries: list[TranscriptEntry],
    outcome: SessionOutcome,
) -> SessionResult:
    """Compose a minimal SessionResult.

    The generic chatbot doesn't structure per-question evidence — each
    QuestionConfig becomes a placeholder QuestionResult with
    was_skipped=True. The real conversation lives in full_transcript;
    a future structured agent will populate question_results properly.
    """
    config = agent._config
    question_results = [
        QuestionResult(
            question_id=q.id,
            question_text=q.text,
            position=q.position,
            is_mandatory=q.is_mandatory,
            was_skipped=True,
            probes_fired=0,
            observations=[],
            transcript_entries=[],
        )
        for q in config.stage.questions
    ]

    return SessionResult(
        session_id=config.session_id,
        job_title=config.job_title,
        stage_id=config.stage.stage_id,
        stage_type=config.stage.stage_type,
        candidate_name=config.candidate.name,
        duration_seconds=time.monotonic() - agent._session_start_monotonic,
        questions_asked=0,
        questions_skipped=len(config.stage.questions),
        total_probes_fired=0,
        question_results=question_results,
        full_transcript=list(transcript_entries),
        completed_at=datetime.now(UTC).isoformat(),
        knockout_failures=[],
    )


async def _persist_session_result(
    agent: GenericInterviewAgent,
    *,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
    transcript_entries: list[TranscriptEntry],
    outcome: SessionOutcome,
) -> None:
    if agent._persisted:
        return
    result = _build_session_result(
        agent,
        transcript_entries=transcript_entries,
        outcome=outcome,
    )
    async with get_bypass_session() as db:
        await record_session_result(
            db,
            session_id=uuid.UUID(agent._config.session_id),
            tenant_id=tenant_uuid,
            result=result,
            correlation_id=correlation_id,
        )
        await db.commit()
    agent._persisted = True
    log.info(
        "interview_engine.result.persisted",
        session_id=agent._config.session_id,
        outcome=outcome,
    )


async def _publish_session_outcome(
    agent: GenericInterviewAgent,
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
    agent: GenericInterviewAgent,
    *,
    reason: str,
    collector: EventCollector,
    sink: EventLogSink | None,
) -> None:
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
