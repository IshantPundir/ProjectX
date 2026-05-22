"""Interview Engine v2 — LiveKit entrypoint (M3 canned listen-respond harness).

M3 scope: the floor-control SUBSTRATE, talk-testable with NO brain and NO mouth.
A real AgentSession (STT+keyterms / VAD / tuned turn-detector / v2 dynamic
endpointing / adaptive interruption, preemptive generation OFF) listens; on each
completed user turn a _CannedBankAgent captures the utterance, records the
barge-in scaffold + audit, then speaks the NEXT bank question verbatim via
session.say() and raises StopResponse() (so no LLM is ever invoked). A silence
timer ticks the pure HoldSpacePacer + UnresponsiveLadder and voices cues via
session.say(add_to_chat_ctx=False). At close, the v2 audio summary is logged
(CMI-3). The mouth lands in M4, the brain in M5 — both reuse this wiring.

Imports livekit; only ever imported lazily via interview_engine_v2.__getattr__('run')
inside the engine container, so the FastAPI/nexus process never loads livekit.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    MetricsCollectedEvent,
    StopResponse,
    TurnHandlingOptions,
    UserStateChangedEvent,
    room_io,
)

from app.ai.config import ai_config
from app.ai.realtime import (
    build_interruption_options,
    build_noise_cancellation,
    build_stt_plugin,
    build_tts_plugin,
    build_turn_detector,
    build_vad,
)
from app.config import settings
from app.modules.interview_engine_v2.audio_metrics import compute_audio_summary
from app.modules.interview_engine_v2.event_log.collector import EventCollector
from app.modules.interview_engine_v2.turn_taking.eou import (
    EouConfig,
    LadderAction,
    UnresponsiveLadder,
    is_backchannel,
)
from app.modules.interview_engine_v2.turn_taking.floor import (
    ResumptionSignals,
    classify_resumption,
    should_yield,
)
from app.modules.interview_engine_v2.turn_taking.pacing import (
    EndpointingSettings,
    HoldSpacePacer,
    build_endpointing_options,
)
from app.modules.interview_runtime import SessionConfig

log = structlog.get_logger("interview_engine_v2")

_KEYTERM_CAP = 50


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


@dataclass
class BankScript:
    """Linear canned script: intro -> each bank question -> closing (no brain)."""

    intro: str
    questions: list[str]
    closing: str
    _idx: int = field(default=0, init=False)
    is_terminal_line: bool = field(default=False, init=False)

    def next_line(self) -> str | None:
        lines = [self.intro, *self.questions, self.closing]
        if self._idx >= len(lines):
            return None
        line = lines[self._idx]
        self._idx += 1
        self.is_terminal_line = self._idx >= len(lines)
        return line


def _now_ms() -> int:
    return int(time.time() * 1000)


class _CannedBankAgent(Agent):
    """Answers each completed turn with the next bank line. No LLM is invoked."""

    def __init__(self, *, script: BankScript, collector: EventCollector,
                 ladder: UnresponsiveLadder, started_at: float,
                 state: dict[str, object],
                 pose_question: Callable[[float], None]) -> None:
        super().__init__(instructions="")
        self._script = script
        self._collector = collector
        self._ladder = ladder
        self._started_at = started_at
        self._state = state               # shared behavioral-timer state (fix #2)
        self._pose_question = pose_question

    def _t_ms(self) -> int:
        return int((time.monotonic() - self._started_at) * 1000)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Fix #2: hold the floor against the silence timer while we deliver the
        # next line, so a ladder/hold cue can never overlap the canned question.
        self._state["responding"] = True
        try:
            text = new_message.text_content or ""
            word_count = len([w for w in text.split() if w])
            backchannel = is_backchannel(
                text, min_words=settings.engine_v2_backchannel_min_words)

            # A real response resets the unresponsive ladder (no-response count -> 0).
            if should_yield(word_count=word_count, is_backchannel=backchannel):
                self._ladder.on_candidate_responded()

            # Barge-in SCAFFOLD: record a provisional label for the M5 brain.
            # Advisory only — never used to decide whether to yield (AI always yields).
            label = classify_resumption(ResumptionSignals(
                prior_utterance_complete=True,        # best-effort in M3; refined in M5
                gap_ms=0,
                ai_prompt_fully_delivered=True,
                word_count=word_count,
                is_backchannel=backchannel,
            ))
            self._collector.record(
                "turn.captured",
                {"word_count": word_count, "is_backchannel": backchannel,
                 "resumption_label": label.value},
                t_ms=self._t_ms(), wall_ms=_now_ms(),
            )

            # Deliver the next canned bank line, then re-arm the ladder for the NEW
            # question via _pose_question (resets started_answering + the pacer too).
            # The terminal closing line poses nothing -> leave the ladder disarmed.
            line = self._script.next_line()
            if line is not None:
                await self.session.say(line, add_to_chat_ctx=True)
                if not self._script.is_terminal_line:
                    self._pose_question(time.monotonic())
        finally:
            self._state["responding"] = False
        raise StopResponse()


async def run(
    ctx: JobContext,
    config: SessionConfig,
    *,
    tenant_id: uuid.UUID,
    correlation_id: str,
) -> None:
    """v2 per-session run. M3: canned listen-respond floor-control harness."""
    started_at = time.monotonic()
    collector = EventCollector(
        session_id=config.session_id,
        tenant_id=str(tenant_id),
        correlation_id=correlation_id,
    )
    collector.record(
        "engine.v2.dispatched",
        {"job_title": config.job_title, "question_count": len(config.stage.questions)},
        t_ms=0, wall_ms=_now_ms(),
    )

    await ctx.connect()
    await ctx.wait_for_participant()

    keyterms = assemble_v2_keyterms(
        candidate_first_name=config.candidate.name,
        bank_keyterms=list(config.keyterms),
    )
    endpointing = build_endpointing_options(EndpointingSettings(
        mode=ai_config.engine_v2_endpointing_mode,
        min_delay=ai_config.engine_v2_endpointing_min_delay,
        max_delay=ai_config.engine_v2_endpointing_max_delay,
    ))
    session = AgentSession(
        stt=build_stt_plugin(keyterms=keyterms),
        tts=build_tts_plugin(),
        vad=build_vad(),
        # Fix #3: own unresponsive behavior in ONE place. The manual ladder gives
        # the multi-rung + close-after-N semantics that LiveKit's away timeout
        # (a single state flip) cannot express, so disable the framework timeout
        # (docs: "Set to None to turn off") and let UnresponsiveLadder run it.
        # (R3: confirm None disables it cleanly in the installed version.)
        user_away_timeout=None,
        turn_handling=TurnHandlingOptions(
            turn_detection=build_turn_detector(
                unlikely_threshold=ai_config.engine_v2_turn_detector_unlikely_threshold,
            ),
            preemptive_generation={"enabled": False},   # quality-before-latency lock
            endpointing=endpointing,
            interruption=build_interruption_options(),
        ),
    )

    # --- metrics collection (CMI-3): mirror v1's audio.metrics.* events ---
    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        try:
            payload = m.model_dump(exclude={"timestamp", "metadata"})
        except Exception:  # noqa: BLE001
            payload = {"raw": str(m)}
        collector.record(f"audio.metrics.{m.type}", payload,
                         t_ms=int((time.monotonic() - started_at) * 1000),
                         wall_ms=_now_ms())

    # --- behavioral layer: one silence timer ticks the pure pacer + ladder ---
    ladder = UnresponsiveLadder(EouConfig(
        prompt_1_s=ai_config.engine_v2_unresponsive_prompt_1_s,
        prompt_2_s=ai_config.engine_v2_unresponsive_prompt_2_s,
        max_no_responses=ai_config.engine_v2_unresponsive_max_no_responses,
    ))
    pacer = HoldSpacePacer(
        enabled=ai_config.engine_v2_hold_space_enabled,
        delay_s=ai_config.engine_v2_hold_space_delay_s,
    )
    # started_answering: has the candidate begun answering the CURRENT question?
    #   reset False by _pose_question; set True on the first 'speaking' after it.
    #   Routes silence (fix #1): PRE-answer silence -> unresponsive ladder;
    #   MID-answer pause (started_answering) -> hold-space pacer.
    # responding (fix #2): the agent is delivering a canned line -> the silence
    #   loop must not speak a cue over it.
    state: dict[str, object] = {
        "started_answering": False, "responding": False,
        "closing": False, "silence_task": None,
    }

    def _pose_question(at_s: float) -> None:
        """Arm the ladder for a freshly-posed question and reset turn state."""
        ladder.on_question_posed(at_s=at_s)
        pacer.on_resume()                  # no open hold-space window yet
        state["started_answering"] = False

    async def _silence_watch() -> None:
        """Tick the pacer (mid-answer) OR the ladder (pre-answer) while silent."""
        while not state["closing"]:
            await asyncio.sleep(0.5)
            try:
                if state["responding"] or state["closing"]:
                    continue                      # fix #2: don't speak over the agent
                now = time.monotonic()
                if state["started_answering"]:
                    # Mid-answer think-pause -> at most one hold-space cue.
                    if pacer.cue_due(now_s=now):
                        pacer.mark_cued()
                        state["responding"] = True
                        try:
                            await session.say(settings.engine_v2_hold_space_message,
                                              add_to_chat_ctx=False)
                        finally:
                            state["responding"] = False
                    continue
                # Pre-answer silence -> the unresponsive ladder owns it (fix #1/#3).
                action = ladder.action(now_s=now)
                if action is LadderAction.NONE:
                    continue
                state["responding"] = True
                try:
                    if action is LadderAction.PROMPT_1:
                        await session.say(settings.engine_v2_unresponsive_message_1,
                                          add_to_chat_ctx=False)
                    elif action is LadderAction.PROMPT_2:
                        await session.say(settings.engine_v2_unresponsive_message_2,
                                          add_to_chat_ctx=False)
                        # Re-pose so a STILL-silent candidate accrues a 2nd
                        # no-response and the ladder can reach CLOSE (the pure
                        # ladder counts per-question; one permanently-silent
                        # question alone never closes — re-posing here drives the
                        # 2nd cycle without needing a completed turn).
                        ladder.on_question_posed(at_s=now)
                    elif action is LadderAction.CLOSE_UNRESPONSIVE:
                        state["closing"] = True
                        collector.record("engine.v2.candidate_unresponsive", {},
                                         t_ms=int((now - started_at) * 1000),
                                         wall_ms=_now_ms())
                        await session.say(settings.engine_v2_unresponsive_message_2,
                                          add_to_chat_ctx=False)
                        await session.aclose()
                finally:
                    state["responding"] = False
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # One bad tick (e.g. a transient TTS/network failure on say())
                # must not kill the behavioral layer for the rest of the session.
                log.warning("engine.v2.silence_watch.tick_failed", exc_info=True)
                state["responding"] = False

    @session.on("user_state_changed")
    def _on_user_state(ev: UserStateChangedEvent) -> None:
        now = time.monotonic()
        if ev.new_state == "speaking":
            state["started_answering"] = True   # fix #1: this turn is now an answer
            pacer.on_resume()                    # any speech clears a hold-space window
        elif ev.new_state == "listening":
            # Open a hold-space window ONLY if the candidate has begun answering;
            # pre-answer silence belongs to the ladder, not the pacer (fix #1).
            if state["started_answering"]:
                pacer.on_pause_started(at_s=now)
        collector.record("audio.user.state",
                         {"old_state": ev.old_state, "new_state": ev.new_state},
                         t_ms=int((now - started_at) * 1000), wall_ms=_now_ms())

    script = BankScript(
        intro=(f"Hi {config.candidate.name or 'there'}. I'm {settings.engine_agent_name}, "
               f"and I'll be running this screening. Let's get started."),
        questions=[q.text for q in config.stage.questions],
        closing="That's everything from my side. Thanks for your time today.",
    )
    agent = _CannedBankAgent(script=script, collector=collector, ladder=ladder,
                             started_at=started_at, state=state,
                             pose_question=_pose_question)

    nc_filter = build_noise_cancellation()
    await session.start(
        agent=agent, room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=nc_filter),
            # Match v1: when the session actually closes (candidate hangs up,
            # unresponsive close, or end-of-script), delete the room so the
            # candidate is disconnected cleanly instead of left alone.
            delete_room_on_close=True,
        ),
    )

    # Deliver the intro + first question, then arm the ladder on the first question.
    await session.say(script.next_line() or "", add_to_chat_ctx=True)   # intro
    first_q = script.next_line()
    if first_q is not None:
        await session.say(first_q, add_to_chat_ctx=True)
    # Arm the ladder + reset started_answering for the first question. (If the
    # candidate barged in on the intro, this resets that stray 'speaking' flag,
    # so the ladder governs pre-answer silence on the real first question.)
    _pose_question(time.monotonic())

    state["silence_task"] = asyncio.create_task(_silence_watch())

    @session.on("close")
    def _on_close(_ev: object) -> None:
        state["closing"] = True
        task = state.get("silence_task")
        if isinstance(task, asyncio.Task):
            task.cancel()
        env = collector.envelope()
        summary = compute_audio_summary(
            events=[e.model_dump(mode="json") for e in env.events],
            config_snapshot={
                "endpointing_mode": ai_config.engine_v2_endpointing_mode,
                "endpointing_min_delay": ai_config.engine_v2_endpointing_min_delay,
                "endpointing_max_delay": ai_config.engine_v2_endpointing_max_delay,
                "turn_detector_unlikely_threshold":
                    ai_config.engine_v2_turn_detector_unlikely_threshold,
            },
        )
        # CMI-3: the talk-test reads these numbers from the engine logs.
        log.info("engine.v2.audio_tuning_summary", **summary)

    # NOTE: do NOT publish session_outcome here. run() returns right after
    # session.start() (same as v1 _run_entrypoint) and the AgentSession keeps the
    # conversation alive on its own — the candidate answers, on_user_turn_completed
    # delivers the next question, etc. Publishing session_outcome='completed' at
    # this point (an M1 one-shot leftover) told the frontend the interview was over
    # the instant Q1 finished, so the candidate disconnected after one question.
    # The real outcome + record_session_result land with the brain in M5; for the
    # M3 floor-control talk-test the session ends on candidate hang-up or the
    # unresponsive-ladder aclose (delete_room_on_close cleans up the room).
