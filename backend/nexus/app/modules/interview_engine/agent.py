"""Interview Engine — Gen-3 skeleton (LiveKit entrypoint + worker bootstrap).

Gen-3 engine rewrite. The conversation/control loop (`_drive`) is a stub that
raises NotImplementedError — behavior arrives in later phases (B–F):
  - Phase B: Ear (manual turn control + VAD/SmartTurn/MultilingualModel fusion)
  - Phase C: Drive loop (bridge ∥ brain → mouth + NoteLog accumulation)
  - Phase D: Brain (BrainTurnInput → BrainTurnOutput + Directive resolver)
  - Phase E: Mouth (persona rendering, DirectiveAct → natural spoken Indian English)
  - Phase F: Wire end-to-end + persist SessionEvidence + manual talk-test

Architecture:
  - `run()` builds the LiveKit `AgentSession` with manual turn detection
    (the Ear owns turn commits via `session.commit_user_turn()` — Phase B).
  - `_drive(session, ...)` is the stub for the three-tier loop; Phase F fills it in.
  - `ear/`, `brain/`, `mouth/` are empty skeleton packages (behavior in B/D/E).
  - `notes.py` is the NoteLog skeleton (behavior in Phase C).

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
from app.modules.interview_engine.ear.ladder import ladder_config_from_ai_config
from app.modules.interview_engine.ear.orchestrator import Ear
from app.modules.interview_engine.ear.smart_turn import TurnAudioBuffer
from app.modules.interview_engine.ear.vad_gate import SpeechActivity
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
# LiveKit Ear glue — wired here; real-event behavior validated in Phase F talk-test
# ---------------------------------------------------------------------------

# Poll interval while the candidate is paused (state=listening) and the agent
# is not speaking. We re-evaluate the fusion ladder at this cadence.
# F3-VALIDATE: tune empirically alongside the ladder thresholds.
_EAR_POLL_INTERVAL_S: float = 0.120  # 120ms


class _EarAgent(Agent):
    """Gen-3 engine Agent — extends the base Agent with:

    1. ``stt_node`` override that tees every incoming ``rtc.AudioFrame`` into
       the ``Ear``'s ``TurnAudioBuffer`` so the Smart Turn model can predict
       end-of-utterance probability on live audio. The INPUT audio stream is
       wrapped in an async generator that observes each frame before handing
       the (unchanged) stream to the default STT node. This is the correct
       pattern: ``stt_node`` CONSUMES the audio AsyncIterable[AudioFrame] and
       YIELDS speech events — the tee must wrap the INPUT, not iterate the
       OUTPUT events.

    2. ``on_user_turn_completed`` override that raises ``StopResponse`` to
       suppress the AgentSession's built-in auto-reply. Gen-3 drives all
       output itself via the SessionDriver → Mouth → ``session.say()``; the
       built-in LLM reply node must never fire or the agent double-speaks.

    Design rules:
    - The tee MUST NEVER block or drop the audio passthrough. Any buffer error
      is caught and logged; the yield always continues regardless.
    - Conversion from LiveKit's int16 PCM to float32 normalised [-1, 1] is
      done here so ``TurnAudioBuffer.append()`` always receives float32 mono.
    - Resampling to 16kHz is done when the frame's ``sample_rate != 16000``.
      In practice LiveKit delivers 48kHz frames from the browser; the
      conversion is a lightweight linear downsample via numpy.
    """

    def __init__(self, ear: Ear, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ear = ear

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:  # type: ignore[override]
        """Suppress the built-in auto-reply.

        Gen-3 drives all agent output via the SessionDriver → Mouth →
        ``session.say()``. Raising ``StopResponse`` here prevents the
        AgentSession's LLM node from generating a reply automatically,
        which would cause double-speak (the built-in LLM reply AND the
        driver's ``say()`` would both fire for the same turn).
        """
        raise StopResponse()

    async def stt_node(self, audio, model_settings):  # type: ignore[override]
        """Tee each incoming AudioFrame into the Ear buffer, then pass onward.

        The INPUT ``audio`` AsyncIterable[AudioFrame] is wrapped in a local
        async generator that observes each frame (converting int16→float32 mono
        and appending to the Ear buffer) before yielding it unchanged. The
        wrapped stream is then passed to ``Agent.default.stt_node`` which
        consumes the audio and yields speech events — those events are yielded
        through to the caller.

        This is the correct tee pattern: we wrap the INPUT stream, not iterate
        the OUTPUT speech events (which are STT results, not AudioFrames).

        F3-VALIDATE: verify the float32/resampling conversion on real 48kHz
        LiveKit frames during the Phase F talk-test. Confirm the stt_node
        async generator signature (audio: AsyncIterable[AudioFrame], yields
        speech events) against the current livekit-agents version.
        """
        import numpy as _np

        async def _tee_audio():
            """Wrap audio stream: observe each frame for the Ear, then yield it."""
            async for frame in audio:
                # Tee into the Ear — wrapped in try/except so any buffer
                # error can never break the STT audio passthrough.
                try:
                    # Extract raw samples — LiveKit AudioFrame stores int16 PCM.
                    # F3-VALIDATE: confirm attribute names on real livekit AudioFrame.
                    raw = getattr(frame, "data", None)
                    sr: int = getattr(frame, "sample_rate", 16000)
                    num_channels: int = getattr(frame, "num_channels", 1)

                    if raw is not None:
                        # Convert int16 bytes → float32 numpy [-1, 1]
                        pcm = _np.frombuffer(raw, dtype=_np.int16).astype(_np.float32) / 32768.0

                        # Mix down to mono if needed
                        if num_channels > 1:
                            pcm = pcm.reshape(-1, num_channels).mean(axis=1)

                        # Resample to 16kHz if the frame rate differs
                        if sr != 16000 and len(pcm) > 0:
                            target_len = int(len(pcm) * 16000 / sr)
                            if target_len > 0:
                                indices = _np.linspace(0, len(pcm) - 1, target_len)
                                pcm = _np.interp(
                                    indices, _np.arange(len(pcm)), pcm
                                ).astype(_np.float32)

                        self._ear.append_audio(pcm)
                except Exception:  # noqa: BLE001 — tee error must never break STT
                    log.warning("engine.ear.audio_tee_error", exc_info=True)

                yield frame

        # Pass the tee-wrapped audio stream to the default STT node and yield
        # its speech events through to the AgentSession.
        async for ev in Agent.default.stt_node(self, _tee_audio(), model_settings):
            yield ev


def build_ear() -> Ear:
    """Assemble a fresh ``Ear`` from env-driven config.

    Called once per session inside ``run()``. Composes:
    - ``EarLadderConfig`` from ``AIConfig`` (env-driven thresholds)
    - ``TurnAudioBuffer`` at 16kHz with lazy Smart Turn detector
    - ``SpeechActivity`` (pure pause clock)
    - ``MultilingualModel`` (text-EOU) built via ``build_turn_detector``

    Falls back to ``eou_model=None`` (Smart-Turn-only mode) if the
    MultilingualModel raises — the ladder has an explicit ST-only path.

    F3-VALIDATE: confirm the MultilingualModel loads correctly in the
    engine container and that ``predict_end_of_turn`` is compatible with
    the ``text_eou_probability`` wrapper in ``ear/vad_gate.py``.
    """
    cfg = ladder_config_from_ai_config()
    buffer = TurnAudioBuffer(sample_rate=16000, max_seconds=8.0)  # lazy ONNX
    activity = SpeechActivity()

    eou_model: object | None = None
    try:
        eou_model = build_turn_detector(unlikely_threshold=ai_config.engine_v2_turn_detector_unlikely_threshold)
        log.info("engine.ear.eou_model.built", model="MultilingualModel")
    except Exception:  # noqa: BLE001
        # F3-VALIDATE: if MultilingualModel is unavailable in dev, ST-only is fine.
        log.warning(
            "engine.ear.eou_model.unavailable — falling back to Smart-Turn-only",
            exc_info=True,
        )

    return Ear(
        cfg=cfg,
        buffer=buffer,
        activity=activity,
        eou_model=eou_model,
    )


def setup_ear(
    session: "AgentSession",
    ear: Ear,
    *,
    clock,
    on_commit,
    terminal_event: "asyncio.Event",
) -> "asyncio.Task":  # type: ignore[name-defined]
    """Register LiveKit event hooks and start the background Ear poll task.

    This wires the ``Ear`` into a running ``AgentSession``:

    1. ``user_state_changed`` → ``ear.on_user_state(ev.new_state, clock())``
       The Ear updates ``SpeechActivity`` and resets the buffer on speaking-start.

    2. Barge-in: when the candidate starts speaking while the agent is talking,
       call ``session.interrupt()`` so the Mouth stops mid-sentence.

    3. A background async poll task runs while the user is paused
       (state=listening) and the agent is not speaking. Every
       ``_EAR_POLL_INTERVAL_S`` it calls ``ear.evaluate()``. On
       ``EarDecision.commit``, it calls the OFFICIAL manual-turn commit:
       ``transcript = await session.commit_user_turn(...)`` which returns the
       full committed transcript directly (no event subscription needed). The
       transcript is then forwarded to ``on_commit(transcript)`` which feeds it
       to the SessionDriver. On ``EarDecision.hold_cue`` the patience cue is
       played. On ``EarDecision.wait`` nothing happens.

    Parameters
    ----------
    session:
        The running LiveKit ``AgentSession`` (turn_detection="manual").
    ear:
        The per-session ``Ear`` instance (must already be constructed).
    clock:
        Callable that returns the current time in milliseconds (``_now_ms``).
    on_commit:
        Async callable ``(transcript: str) -> bool``. Called after each
        committed turn with the full transcript text. Returns ``True`` if the
        session has reached a terminal state (the poll loop should stop).
    terminal_event:
        ``asyncio.Event`` that is set when the session is terminal. The poll
        loop watches this to exit cleanly alongside the drive loop.

    Returns the background ``asyncio.Task`` so the caller (``_drive``) can
    cancel it on session close.

    F3-VALIDATE: the ``agent_state`` / ``is_speaking`` attribute on the session
    for detecting whether the agent is talking. Confirm event name
    ``"user_state_changed"`` and ``UserStateChangedEvent.new_state`` attribute
    on real LiveKit sessions during the Phase F talk-test.
    """
    # Mutable state shared by the hook and the poll task.
    _user_state: list[str] = ["away"]  # start as away until first speaking event

    def _on_user_state_changed(ev) -> None:
        new_state: str = ev.new_state  # F3-VALIDATE: attribute name on real event
        now_ms: int = clock()
        _user_state[0] = new_state

        ear.on_user_state(new_state, now_ms)

        # Barge-in: candidate spoke while agent was talking → interrupt the agent.
        # F3-VALIDATE: confirm the agent-speaking predicate on AgentSession.
        if new_state == "speaking":
            try:
                agent_is_speaking = getattr(session, "agent_state", None) == "speaking"
            except Exception:  # noqa: BLE001
                agent_is_speaking = False
            if agent_is_speaking:
                session.interrupt()
                log.info("engine.ear.barge_in")

    # F3-VALIDATE: confirm event name on real LiveKit AgentSession.
    session.on("user_state_changed", _on_user_state_changed)

    async def _poll_loop() -> None:
        """While paused (state=listening), tick the Ear and act on decisions.

        On ``commit``: calls ``session.commit_user_turn()`` (the OFFICIAL
        manual-turn API — returns the transcript directly), then forwards the
        transcript to ``on_commit``. If ``on_commit`` signals terminal, sets
        the terminal_event so the drive loop can exit cleanly.

        On ``hold_cue``: delegates to ``ear.act`` (plays patience cue once).
        On ``wait``: no-op.
        """
        while not terminal_event.is_set():
            await asyncio.sleep(_EAR_POLL_INTERVAL_S)

            if terminal_event.is_set():
                break

            if _user_state[0] != "listening":
                # Candidate is speaking or away — nothing to do.
                continue

            # F3-VALIDATE: confirm agent-speaking predicate on AgentSession.
            try:
                agent_is_speaking = getattr(session, "agent_state", None) == "speaking"
            except Exception:  # noqa: BLE001
                agent_is_speaking = False

            if agent_is_speaking:
                # Don't commit while the agent is mid-sentence.
                continue

            # F3-VALIDATE: build chat_ctx from session.history for text-EOU.
            # Passing None is safe — the ladder falls back to Smart-Turn-only.
            chat_ctx = getattr(session, "history", None)

            try:
                decision, _ = await ear.evaluate(
                    now_ms=clock(),
                    chat_ctx=chat_ctx,
                )
            except Exception:  # noqa: BLE001 — eval errors must never crash the session
                log.warning("engine.ear.poll_error", exc_info=True)
                continue

            if decision.name == "commit":
                # OFFICIAL manual-turn pattern: commit_user_turn() RETURNS the
                # transcript directly. No event subscription needed.
                # F3-VALIDATE: confirm transcript_timeout / stt_flush_duration
                # keyword args and return type on the current livekit-agents version.
                try:
                    transcript = await session.commit_user_turn(
                        transcript_timeout=5.0,
                        stt_flush_duration=2.0,
                    )
                    ear._buffer.reset()  # clear after commit
                    log.info("engine.ear.committed", transcript_len=len(transcript or ""))
                    if transcript and transcript.strip():
                        is_terminal = await on_commit(transcript)
                        if is_terminal:
                            terminal_event.set()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.warning("engine.ear.commit_error", exc_info=True)

            elif decision.name == "hold_cue":
                # Delegate to ear.act for idempotent patience-cue playback.
                # act() is a no-op for commit (commit is handled above).
                try:
                    await ear.act(session, decision)
                except Exception:  # noqa: BLE001
                    log.warning("engine.ear.hold_cue_error", exc_info=True)

            # EarDecision.wait → no-op; poll again next tick.

    poll_task = asyncio.create_task(_poll_loop())
    return poll_task


# ---------------------------------------------------------------------------
# Gen-3 drive-loop stub (Phase F fills this in)
# ---------------------------------------------------------------------------

async def _drive(
    session: AgentSession,
    ear: Ear,
    config: SessionConfig,
    ctx: JobContext,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """Gen-3 three-tier drive loop.

    Builds a SessionDriver from the config + session, wires the Ear event
    hooks + poll task (via ``setup_ear``), speaks the opener, then waits for
    the terminal event (set by the Ear poll loop when ``on_commit`` returns
    True) or a 5-minute silence timeout.

    Commit flow (OFFICIAL manual-turn pattern):
      Ear poll loop detects EarDecision.commit
        → ``session.commit_user_turn(...)`` (returns transcript directly)
        → ``on_commit(transcript)`` → ``driver.handle_turn(...)``
        → returns True when terminal
        → poll loop sets terminal_event
        → _drive awaits terminal_event and calls finalize(completed)

    No ``user_input_transcribed`` event subscription — the transcript is
    captured synchronously from ``commit_user_turn()``'s return value.

    Thin glue: all orchestration logic lives in driver.py (SessionDriver).
    Live event attribute names are marked # F3-VALIDATE.
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
    driver = build_session_driver(
        config,
        voice=session,         # AgentSession satisfies the Voice protocol (has .say())
        persist=_persist,
        started_at=started_at,
    )

    # ── Turn counter + terminal signal ───────────────────────────────────────
    # The terminal_event is set by the poll loop (via on_commit returning True)
    # or by the silence-timeout path below.
    terminal_event: asyncio.Event = asyncio.Event()
    _turn_id: list[int] = [0]
    _finalize_reason: list[CompletionReason] = [CompletionReason.completed]
    _handle_turn_error: list[bool] = [False]

    # ── on_commit: called by the poll loop after each successful commit ───────
    async def on_commit(transcript: str) -> bool:
        """Forward a committed transcript to the SessionDriver.

        Called by the Ear poll loop after ``session.commit_user_turn()``
        returns a non-empty transcript. Returns True when the session is
        terminal (the poll loop will set terminal_event and stop).
        """
        _turn_id[0] += 1
        turn_ref = f"t-{_turn_id[0]}"
        # F3-VALIDATE: timing — commit_user_turn does not return start/end ms;
        # use a coarse wall-clock span anchored to "now" for the turn record.
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

    # ── Wire the Ear (event hooks + poll task) ───────────────────────────────
    poll_task = setup_ear(
        session,
        ear,
        clock=_now_ms,
        on_commit=on_commit,
        terminal_event=terminal_event,
    )

    # ── Speak the opener ─────────────────────────────────────────────────────
    try:
        await driver.opener()
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.opener_failed", exc_info=True)

    # ── Wait for terminal (commit-driven) or silence timeout ─────────────────
    try:
        await asyncio.wait_for(
            terminal_event.wait(),
            timeout=300.0,  # 5-minute silence without a committed turn → close
        )
    except asyncio.TimeoutError:
        log.warning(
            "engine.drive.candidate_silence_timeout",
            session_id=config.session_id,
        )
        terminal_event.set()
        _finalize_reason[0] = CompletionReason.unresponsive

    # ── Finalize (persist SessionEvidence) ───────────────────────────────────
    try:
        await driver.finalize(_finalize_reason[0])
    except Exception:  # noqa: BLE001
        log.warning("engine.drive.finalize_error", exc_info=True)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    poll_task.cancel()
    try:
        await poll_task
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
    """Per-session engine run: connect, build the AgentSession (manual turn detection),
    start the heartbeat, then delegate to the gen-3 drive loop (_drive stub in Phase A).

    The AgentSession is built with `turn_detection="manual"` — the Ear (Phase B) will
    own all turn commits via `session.commit_user_turn()`. preemptive_generation is OFF
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

    # Gen-3 uses manual turn detection: the Ear owns when to commit the user's
    # turn via session.commit_user_turn(). No endpointing= arg — manual mode
    # owns timing entirely; the turn detector is not used here.
    session = AgentSession(
        stt=build_stt_plugin(keyterms=keyterms),
        llm=build_mouth_llm_plugin(),
        tts=build_tts_plugin(),
        vad=ctx.proc.userdata["vad"],
        user_away_timeout=None,
        turn_handling=TurnHandlingOptions(
            turn_detection="manual",
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

    # Build the Ear BEFORE starting the session so _EarAgent can wire it into
    # the stt_node tee from the first frame. The same ear instance is passed
    # to _drive → setup_ear for the event hooks + poll task.
    ear = build_ear()

    # _EarAgent extends Agent with:
    #   1. stt_node: tees INPUT audio frames into ear._buffer for Smart Turn.
    #   2. on_user_turn_completed: raises StopResponse to suppress the built-in
    #      LLM auto-reply — gen-3 drives all output via the SessionDriver.
    agent = _EarAgent(ear, instructions="")

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
            session, ear, config, ctx,
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
