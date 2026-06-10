"""Interview Engine — Gen-3 LiveKit entrypoint + worker bootstrap.

Gen-3 three-tier engine on LiveKit NATIVE turn detection (Path A+):
  - `run()` builds the `AgentSession` with the native turn detector
    (`MultilingualModel`) + dynamic endpointing. The detector reads the live
    STT stream to decide end-of-turn, then fires `on_user_turn_completed` with
    the FULL final transcript — EOU and the transcript arrive together (no
    commit/STT race, no partial / one-turn lag).
  - `_EngineAgent.on_user_turn_completed` submits each committed transcript to a
    per-session `CommittedTurnSource` and raises `StopResponse` (gen-3 owns all
    output; the built-in LLM reply never fires).
  - `_drive(session, turn_source, ...)` speaks the opener, then consumes
    committed turns → `SessionDriver.handle_turn` (bridge ∥ brain → mouth +
    NoteLog), and persists `SessionEvidence` on close.
  - `brain/`, `mouth/`, `notes.py`, `driver.py`, `loop.py` hold the
    LiveKit-free orchestration core.

Invariant (load-bearing): this module imports livekit and is ONLY ever
imported lazily via interview_engine.__getattr__('run' / 'server') inside the
engine container. The FastAPI/nexus process must never load this module.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime

import structlog
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    StopResponse,
    TurnHandlingOptions,
    room_io,
)
from livekit.plugins import silero as _silero_vad  # noqa: F401  — register for download-files
from livekit.plugins import (
    turn_detector as _turn_detector,  # noqa: F401  — register turn-detector weights for download-files (the native EOU model)
)
from opentelemetry.trace import set_tracer_provider as _otel_set_global_provider

# Bind the Dramatiq broker to settings.redis_url FIRST (before any @dramatiq.actor
# is imported). This MUST live in agent.py — not __main__.py — because LiveKit runs
# each interview in a SPAWNED job subprocess that imports this module for the
# entrypoint but never executes __main__. Without it, record_session_result's
# report-scoring .send() in the job process falls back to Dramatiq's default broker
# at localhost:6379 and fails. See app/brokers.py.
from app import brokers  # noqa: F401  — side-effect: dramatiq.set_broker(redis_url)
from app.ai.config import ai_config
from app.ai.otel import bootstrap_tracer_provider
from app.ai.realtime import (
    build_interruption_options,
    build_mouth_llm_plugin,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
    build_vad,
)
from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.turn_source import CommittedTurnSource
from app.modules.interview_runtime import (
    SessionConfig,
    build_session_config,
    record_engine_heartbeat,
)
from app.modules.session import classify_engine_exception, transition_to_error

log = structlog.get_logger("interview_engine")

_KEYTERM_CAP = 50


# ---------------------------------------------------------------------------
# Worker bootstrap
# ---------------------------------------------------------------------------

server = AgentServer(host="0.0.0.0", port=8081)


def prewarm(proc: JobProcess) -> None:
    """Process-startup hook: bootstrap an OTel TracerProvider so livekit-agents'
    built-in spans ship to whatever OTLP endpoint the operator configures.
    Production-safe default: no env vars set -> spans go nowhere."""
    provider = bootstrap_tracer_provider()
    _otel_set_global_provider(provider)
    proc.userdata["otel_provider"] = provider
    log.info("engine.otel.bootstrapped", service_name=settings.otel_service_name)
    proc.userdata["vad"] = build_vad()
    log.info("engine.vad.prewarmed", provider="silero")


server.setup_fnc = prewarm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def assemble_v2_keyterms(*, candidate_first_name: str, bank_keyterms: list[str]) -> list[str]:
    """v2-native keyterm pass (self-contained; no import of interview_engine/).

    Candidate first name + cached bank keyterms, case-insensitive dedup, capped.
    """
    terms: list[str] = []

    def _add(term: str) -> None:
        t = term.strip()
        if not t or len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == x.lower() for x in terms):
            return
        terms.append(t)

    if candidate_first_name.strip():
        _add(candidate_first_name.split()[0])
    for term in bank_keyterms:
        _add(term)
    return terms


# ---------------------------------------------------------------------------
# LiveKit native-turn-detection glue (Path A+) — validated in the Phase F talk-test
# ---------------------------------------------------------------------------

class _EngineAgent(Agent):
    """Gen-3 engine Agent under LiveKit NATIVE turn detection (Path A+).

    The ``AgentSession`` runs the native turn detector (``MultilingualModel``),
    which decides end-of-turn from the LIVE STT stream — so EOU and the final
    transcript are produced together, on the same clock. There is no
    ``commit_user_turn`` race, no partial transcript, and no one-turn lag (the
    failure mode of the retired manual Ear).

    The only customisation is ``on_user_turn_completed``: the framework calls it
    AFTER the turn detector confirms the turn ended and BEFORE the built-in
    reply, with ``new_message`` carrying the FULL final transcript. We submit
    that transcript to the per-session ``CommittedTurnSource`` (the drive loop
    consumes it and runs bridge ∥ brain → mouth) and raise ``StopResponse`` so
    the AgentSession's built-in LLM reply never fires — gen-3 owns ALL output
    via the SessionDriver → Mouth → ``session.say()``.

    An empty / whitespace-only transcript is dropped by the turn source, so a
    silent or empty STT final never produces a spurious no-op turn.
    """

    def __init__(self, *, turn_source: CommittedTurnSource, **kwargs) -> None:
        super().__init__(**kwargs)
        self._turn_source = turn_source

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:  # type: ignore[override]
        """Feed the committed turn to the drive loop; suppress the built-in reply."""
        text = (getattr(new_message, "text_content", "") or "").strip()
        accepted = self._turn_source.submit(text)
        log.info(
            "engine.turn.committed",
            accepted=accepted,
            transcript_len=len(text),
        )
        raise StopResponse()


class _InterruptAwareVoice:
    """Wraps the AgentSession so the driver can tell whether the line it just spoke
    was CUT OFF by the candidate (barge-in). ``session.say()`` returns a
    ``SpeechHandle``; we await it (playout) and record ``handle.interrupted``. The
    SessionDriver reads ``.last_interrupted`` after speaking a question to drive the
    P2 floor-interrupted recovery. Satisfies the driver's duck-typed Voice protocol
    (``say``) and never raises into the drive loop.
    """

    def __init__(self, session: "AgentSession") -> None:
        self._session = session
        self.last_interrupted: bool = False

    async def say(self, text: str, *, allow_interruptions: bool = True) -> None:
        maybe = self._session.say(text, allow_interruptions=allow_interruptions)
        # say() returns a SpeechHandle (awaitable); be robust if a version returns
        # a coroutine that resolves to the handle.
        handle = await maybe if asyncio.iscoroutine(maybe) else maybe
        try:
            await handle  # wait for playout (or interruption)
        except Exception:  # noqa: BLE001 — a speech error must not break the turn
            pass
        self.last_interrupted = bool(getattr(handle, "interrupted", False))


# ---------------------------------------------------------------------------
# Gen-3 drive-loop stub (Phase F fills this in)
# ---------------------------------------------------------------------------

async def _drive(
    session: AgentSession,
    turn_source: CommittedTurnSource,
    config: SessionConfig,
    ctx: JobContext,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Gen-3 three-tier drive loop (Path A+ — native turn detection).

    Builds a SessionDriver from the config + session, speaks the opener, then
    consumes committed candidate turns from the ``CommittedTurnSource`` until a
    terminal directive, a candidate disconnect, or an inactivity timeout.

    Turn flow (native turn detection):
      LiveKit turn detector confirms end-of-turn (off the live STT stream)
        → ``_EngineAgent.on_user_turn_completed(new_message)`` submits the FULL
          final transcript to ``turn_source`` and raises ``StopResponse``
        → the consume task here pulls it: ``on_commit(transcript)``
          → ``driver.handle_turn(...)`` → returns True when terminal
        → terminal_event set → _drive finalizes.

    Because the turn detector reads STT directly, the transcript handed to
    ``handle_turn`` is always complete — no ``commit_user_turn`` race, no
    partial/one-turn-lag (the failure mode of the retired manual Ear).

    Thin glue: all orchestration logic lives in driver.py (SessionDriver).
    """
    from datetime import UTC, datetime

    from app.database import get_bypass_session
    from app.modules.interview_engine.driver import build_session_driver
    from app.modules.interview_runtime import record_session_evidence
    from app.modules.interview_runtime.evidence import CompletionReason, TimeSpan

    started_at = datetime.now(UTC)

    # ── Build the persist callable ───────────────────────────────────────────
    async def _persist(evidence):  # type: ignore[no-untyped-def]
        """Open a bypass-RLS session and persist the SessionEvidence."""
        async with get_bypass_session() as db:
            await record_session_evidence(
                db,
                tenant_id=tenant_id,
                evidence=evidence,
                correlation_id=correlation_id,
            )
            # record_session_evidence commits internally; no extra commit needed.

    # ── Build the SessionDriver (assembles brain/mouth/bridge/notelog/projection) ──
    # Wrap the session so the driver can read whether a spoken question was cut off
    # (P2 floor-interrupted recovery) — still satisfies the Voice protocol.
    driver = build_session_driver(
        config,
        voice=_InterruptAwareVoice(session),
        persist=_persist,
        started_at=started_at,
    )

    # ── Turn counter + terminal signal ───────────────────────────────────────
    # The terminal_event is set by the consume task (via on_commit returning True)
    # or by the silence-timeout path below.
    terminal_event: asyncio.Event = asyncio.Event()
    _turn_id: list[int] = [0]
    _finalize_reason: list[CompletionReason] = [CompletionReason.completed]
    _handle_turn_error: list[bool] = [False]

    # ── on_commit: called by the consume task for each committed turn ─────────
    _last_activity_s: list[float] = [time.monotonic()]

    async def on_commit(transcript: str) -> bool:
        """Forward a committed transcript to the SessionDriver.

        Called by the consume task with the FULL final transcript of a committed
        candidate turn. Returns True when the session is terminal (the consume
        task then sets terminal_event and stops).
        """
        _last_activity_s[0] = time.monotonic()  # a committed turn = activity
        _turn_id[0] += 1
        turn_ref = f"t-{_turn_id[0]}"
        # The native turn detector does not hand us word-level start/end ms; use
        # a coarse wall-clock span anchored to "now" for the turn record. (Word
        # timing for the evidence contract comes from aligned transcripts later.)
        now_ms = _now_ms()
        span = TimeSpan(start_ms=max(0, now_ms - 1000), end_ms=now_ms)

        try:
            is_terminal = await driver.handle_turn(
                utterance=transcript,
                turn_ref=turn_ref,
                span=span,
            )
        except Exception:  # noqa: BLE001
            log.error(
                "engine.drive.handle_turn_error",
                turn_ref=turn_ref,
                exc_info=True,
            )
            _finalize_reason[0] = CompletionReason.error
            _handle_turn_error[0] = True
            return True  # terminal — error path

        if is_terminal:
            _finalize_reason[0] = CompletionReason.completed
        return is_terminal

    # ── Clean shutdown on candidate disconnect ───────────────────────────────
    # The candidate leaving — voluntarily, on a network drop, OR because proctoring
    # deleted the room — must end the screen PROMPTLY and finalize cleanly (record
    # the evidence), not wait out the inactivity window or rely on job cancellation.
    # The engine can't tell a proctoring kick from a voluntary leave (both are just
    # "the candidate is gone"); it records `candidate_ended` and the proctoring
    # detail lives on the session row. If a real brain-driven close already fired
    # (terminal_event set), we leave that completion reason untouched.
    def _on_participant_disconnected(_participant: object) -> None:  # noqa: ANN001
        if terminal_event.is_set():
            return
        log.info("engine.drive.participant_disconnected", session_id=config.session_id)
        if _finalize_reason[0] == CompletionReason.completed:
            _finalize_reason[0] = CompletionReason.candidate_ended
        terminal_event.set()
        turn_source.close()  # unblock the consume task's pending get()

    try:
        ctx.room.on("participant_disconnected", _on_participant_disconnected)
    except Exception:  # noqa: BLE001 — never let event wiring break the run
        log.warning("engine.drive.disconnect_hook_failed", exc_info=True)

    # ── Speak the warm intro (greeting + job brief), then the opener question ─
    # The intro is non-interruptible and ends on a statement that flows straight
    # into the first question (no "shall we?"). Opener sets the first active
    # question. Both are best-effort — a failure must not abort the session.
    try:
        await driver.intro()
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.intro_failed", exc_info=True)
    try:
        await driver.opener()
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.opener_failed", exc_info=True)

    # ── Consume committed turns from the turn source ─────────────────────────
    # _EngineAgent.on_user_turn_completed feeds the full final transcript here;
    # we forward each to the SessionDriver. A None means the source was closed
    # (session ending) → stop. Started AFTER the opener so handle_turn always
    # has an active question on the floor.
    async def _consume_turns() -> None:
        while not terminal_event.is_set():
            utterance = await turn_source.get()
            if utterance is None:
                break  # source closed — session ending
            is_terminal = await on_commit(utterance)
            if is_terminal:
                terminal_event.set()
                break

    consume_task: asyncio.Task = asyncio.create_task(_consume_turns())

    # ── Wait for terminal (turn-driven) or candidate INACTIVITY ──────────────
    # The timeout is an INACTIVITY window that RESETS on every committed turn —
    # NOT an absolute session cap. An actively-talking candidate runs as long as
    # they keep answering; the resolver closes the screen when it runs out of
    # questions/budget. Only genuine silence (no committed turn for this long)
    # closes as `unresponsive`. F3-tunable.
    _INACTIVITY_TIMEOUT_S = 180.0
    while not terminal_event.is_set():
        remaining = _INACTIVITY_TIMEOUT_S - (time.monotonic() - _last_activity_s[0])
        if remaining <= 0:
            log.warning(
                "engine.drive.candidate_inactivity_timeout",
                session_id=config.session_id,
                inactive_s=round(time.monotonic() - _last_activity_s[0], 1),
            )
            _finalize_reason[0] = CompletionReason.unresponsive
            terminal_event.set()
            turn_source.close()  # unblock the consume task
            break
        try:
            await asyncio.wait_for(terminal_event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            # Window elapsed — re-check; a turn committed meanwhile resets it.
            continue

    # ── Finalize (persist SessionEvidence) ───────────────────────────────────
    try:
        await driver.finalize(_finalize_reason[0])
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.finalize_error", exc_info=True)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    turn_source.close()  # idempotent — ensures the consume task is unblocked
    consume_task.cancel()
    try:
        await consume_task
    except asyncio.CancelledError:
        pass

    # Publish session_outcome='completed' to the room for the frontend.
    # F3-VALIDATE: confirm attribute path on real LiveKit room participant.
    try:
        outcome = "error" if _handle_turn_error[0] else "completed"
        await ctx.room.local_participant.set_attributes({"session_outcome": outcome})
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.outcome_publish_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Per-session engine run
# ---------------------------------------------------------------------------

async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Per-session engine run: connect, build the AgentSession (native turn detection),
    start the heartbeat, then delegate to the gen-3 drive loop (_drive).

    The AgentSession uses LiveKit's native turn detector (`MultilingualModel`),
    which decides end-of-turn off the live STT stream + dynamic endpointing — so
    EOU and the final transcript are produced together (no commit/STT race).
    `_EngineAgent.on_user_turn_completed` feeds each committed turn to the drive
    loop via a `CommittedTurnSource`. preemptive_generation is OFF
    (quality-before-latency lock, same as gen-2).
    """
    started_at = time.monotonic()

    log.info(
        "engine.session.start",
        session_id=config.session_id,
        job_title=config.job_title,
        question_count=len(config.stage.questions),
        correlation_id=correlation_id,
    )

    await ctx.connect()
    await ctx.wait_for_participant()

    keyterms = assemble_v2_keyterms(
        candidate_first_name=config.candidate.name,
        bank_keyterms=list(config.keyterms),
    )

    # Gen-3 (Path A+) uses LiveKit NATIVE turn detection: the MultilingualModel
    # turn detector reads the live STT stream + VAD to decide end-of-turn, then
    # fires on_user_turn_completed with the FULL final transcript — so EOU and
    # the transcript are produced together (no commit_user_turn race / partial /
    # one-turn lag). Dynamic endpointing adapts the post-speech wait to the
    # candidate's own pause statistics (patient with disfluent / thinking
    # speakers). All thresholds are env-driven (F3-tunable, config-only).
    session = AgentSession(
        stt=build_stt_plugin(keyterms=keyterms),
        llm=build_mouth_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        user_away_timeout=None,
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(
                unlikely_threshold=ai_config.engine_turn_detector_unlikely_threshold,
            ),
            endpointing={
                "mode": ai_config.engine_endpointing_mode,
                "min_delay": ai_config.engine_endpointing_min_delay_s,
                "max_delay": ai_config.engine_endpointing_max_delay_s,
            },
            preemptive_generation={"enabled": False},
            interruption=build_interruption_options(),
        ),
    )

    async def _heartbeat_loop() -> None:
        """Pulse last_engine_heartbeat_at every engine_heartbeat_interval_seconds so the
        stuck-session reaper treats this (possibly long) interview as alive. The first
        beat fires immediately; a missed beat is logged, never fatal; the loop ends when
        the session ends (the task is cancelled by the shutdown-callback path)."""
        session_uuid = uuid.UUID(config.session_id)
        while True:
            try:
                async with get_bypass_session() as hb_db:
                    await record_engine_heartbeat(
                        hb_db, session_id=session_uuid, tenant_id=tenant_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a missed beat must never crash the session
                log.warning("engine.heartbeat_failed", exc_info=True)
            await asyncio.sleep(settings.engine_heartbeat_interval_seconds)

    # Build the per-session turn source BEFORE starting the session so
    # _EngineAgent.on_user_turn_completed can submit committed turns to it from
    # the first turn. The same instance is passed to _drive, whose consume task
    # pulls each committed transcript into the SessionDriver.
    turn_source = CommittedTurnSource()

    # _EngineAgent extends Agent with on_user_turn_completed: it submits the full
    # final transcript to turn_source and raises StopResponse to suppress the
    # built-in LLM auto-reply — gen-3 drives all output via the SessionDriver.
    agent = _EngineAgent(turn_source=turn_source, instructions="")

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            delete_room_on_close=True,
        ),
    )

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    try:
        await _drive(
            session, turn_source, config, ctx,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    finally:
        heartbeat_task.cancel()
        log.info(
            "engine.session.end",
            session_id=config.session_id,
            duration_s=round(time.monotonic() - started_at, 2),
        )


# ---------------------------------------------------------------------------
# Per-session entrypoint + failure funnel
# ---------------------------------------------------------------------------

@server.rtc_session(agent_name=settings.engine_agent_name)
async def entrypoint(ctx: JobContext) -> None:
    """Per-session entrypoint. Parses dispatch metadata, binds the log context,
    then runs the engine; any pre-run crash is funneled to the failure handler."""
    metadata = json.loads(ctx.job.metadata or "{}")
    session_id_str = metadata["session_id"]
    tenant_id_str = metadata["tenant_id"]
    correlation_id = metadata.get("correlation_id", session_id_str)
    session_uuid = uuid.UUID(session_id_str)
    tenant_uuid = uuid.UUID(tenant_id_str)

    structlog.contextvars.bind_contextvars(
        session_id=session_id_str,
        tenant_id=tenant_id_str,
        correlation_id=correlation_id,
    )
    log.info("engine.dispatch.received", agent_name=settings.engine_agent_name)

    try:
        await _run_entrypoint(ctx, session_uuid, tenant_uuid, correlation_id)
    except Exception as exc:
        await _handle_entrypoint_failure(
            exc=exc,
            ctx=ctx,
            session_id=session_uuid,
            tenant_uuid=tenant_uuid,
            correlation_id=correlation_id,
        )
        raise  # preserves LiveKit's "job crashed" log


async def _run_entrypoint(
    ctx: JobContext,
    session_uuid: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Fetch the SessionConfig and run the engine (unconditional — single engine)."""
    async with get_bypass_session() as db:
        session_config = await build_session_config(
            db, session_id=session_uuid, tenant_id=tenant_uuid,
        )
    log.info(
        "engine.config.fetched",
        question_count=len(session_config.stage.questions),
        stage_type=session_config.stage.stage_type,
        candidate_name=session_config.candidate.name,
        job_title=session_config.job_title,
    )
    await run(
        ctx,
        session_config,
        tenant_id=tenant_uuid,
        correlation_id=correlation_id,
    )


async def _handle_entrypoint_failure(
    *,
    exc: Exception,
    ctx: JobContext,
    session_id: uuid.UUID,
    tenant_uuid: uuid.UUID,
    correlation_id: str,
) -> None:
    """Single failure handler for every pre-`run` crash path.

    Order: classify -> transition session row to state='error' (durable truth) ->
    best-effort publish session_outcome='error' to the room. DB transition is first
    so the candidate's HTTP fallback poll wins even if the room publish fails. Each
    step is independently guarded and neither re-raises (the caller re-raises the
    original exception)."""
    error_code = classify_engine_exception(exc)
    log.error(
        "engine.entrypoint.failed",
        error_code=error_code,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    try:
        async with get_bypass_session() as db:
            await transition_to_error(
                db,
                session_id=session_id,
                tenant_id=tenant_uuid,
                error_code=error_code,
                correlation_id=correlation_id,
                reason="engine_entrypoint",
            )
            await db.commit()
    except Exception as inner:  # noqa: BLE001
        log.error(
            "engine.entrypoint.db_transition_failed",
            error=str(inner),
            error_type=type(inner).__name__,
        )
    await _best_effort_publish_outcome_attribute(ctx)


async def _best_effort_publish_outcome_attribute(ctx: JobContext) -> None:
    """Publish session_outcome='error' to the room if possible (connecting first,
    since the failure may predate ctx.connect()). Swallows every exception."""
    try:
        if not ctx.room.isconnected():
            await ctx.connect()
        await ctx.room.local_participant.set_attributes(
            {"session_outcome": "error"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "engine.entrypoint.outcome_publish_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
