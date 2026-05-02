"""InterviewController — the outer Agent that hosts a structured interview.

Responsibilities:
  * Greet the candidate.
  * Dispatch a sequential chain of QuestionTask instances under per-task
    asyncio.wait_for watchdogs.
  * Skip questions whose signal_values are subsumed by the candidate's
    prior disclaims (with an LLM-authored bridge).
  * Run the idle-nudge state machine (1Hz tick + UserStateChangedEvent).
  * Classify end-of-interview intent via the @function_tool end_interview_early.
  * Terminate via _terminate: drain in-flight speech -> compose closing ->
    persist -> drain closing -> publish outcome -> retry-shutdown.

Phase 2 ships only TechnicalDepthTask. Phase 5 wires the close_polite
knockout policy (currently record_only — knockouts accumulated, loop
never breaks on them).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import structlog

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.voice import SpeechHandle

from app.config import settings
from app.database import get_bypass_session
from app.modules.interview_engine.budget import SessionBudget
from app.modules.interview_engine.event_log import EventCollector
from app.modules.interview_engine.idle_nudge import (
    IdleNudgeConfig,
    IdleNudgeOutput,
    IdleNudgeStateMachine,
)
from app.modules.interview_engine.outcome_close import (
    SessionOutcome,
    closing_instructions_for,
)
from app.modules.interview_engine.tasks import build_task_for
from app.modules.interview_engine.tasks.base import TaskResult
from app.modules.interview_runtime import (
    QuestionConfig,
    SessionConfig,
    SessionResult,
    record_session_result,
)


log = structlog.get_logger("interview-engine.controller")

KnockoutPolicy = Literal["record_only", "close_polite"]


@dataclass
class KnockoutFailureRecord:
    """In-memory record. Phase 5 introduces the persisted KnockoutFailure model."""

    question_id: str
    reason: str
    signal_values: list[str]
    occurred_at_ms: int


def now_ms() -> int:
    """Wall-clock milliseconds — used for audit-event wall_ms timestamps."""
    return int(time.time() * 1000)


def mandatory_first_then_optional(
    questions: list[QuestionConfig],
) -> list[QuestionConfig]:
    """Stable sort: mandatory before optional, each group ordered by position."""
    mandatory = sorted([q for q in questions if q.is_mandatory], key=lambda q: q.position)
    optional = sorted([q for q in questions if not q.is_mandatory], key=lambda q: q.position)
    return mandatory + optional


def build_controller_prompt(session_config: SessionConfig) -> str:
    """Load and substitute placeholders into the controller.txt prompt body."""
    from string import Template
    from app.ai.prompts import prompt_loader

    template = Template(prompt_loader.get("interview/controller"))
    questions = session_config.stage.questions
    return template.substitute(
        agent_name=settings.engine_agent_name,
        company_about=session_config.company.about,
        company_industry=session_config.company.industry,
        company_stage=session_config.company.company_stage,
        company_hiring_bar=session_config.company.hiring_bar,
        job_title=session_config.job_title,
        seniority_level=session_config.seniority_level,
        duration_minutes=session_config.stage.duration_minutes,
        total_questions=len(questions),
    )


class InterviewController(Agent):
    def __init__(
        self,
        *,
        session_config: SessionConfig,
        tenant_id: uuid.UUID,
        correlation_id: str,
        collector: EventCollector,
        idle_nudge_config: IdleNudgeConfig,
        budget: SessionBudget,
        tenant_policy: KnockoutPolicy,
    ) -> None:
        self._config: SessionConfig = session_config
        self._tenant_id = tenant_id
        self._correlation_id = correlation_id
        self._collector = collector
        self._budget = budget
        self._idle_nudge_state = IdleNudgeStateMachine(idle_nudge_config)
        self._tenant_policy: KnockoutPolicy = tenant_policy
        self._disqualified_signals: set[str] = set()
        self._knockout_failures: list[KnockoutFailureRecord] = []
        self._end_outcome: SessionOutcome | None = None
        self._current_task_run: asyncio.Task | None = None
        self._terminated: bool = False
        self._idle_nudge_tick_task: asyncio.Task | None = None
        self._session_start_ms: int = 0
        self._session_start_monotonic: float = 0.0
        self._persisted: bool = False  # mirrors Phase 1's InterviewerAgent attr
        super().__init__(instructions=build_controller_prompt(session_config))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_enter(self) -> None:
        self._session_start_ms = now_ms()
        self._session_start_monotonic = time.monotonic()
        self._budget.started_at_monotonic = self._session_start_monotonic
        await self._publish_progress_attributes()
        self._idle_nudge_tick_task = asyncio.create_task(self._idle_nudge_loop())

        # 1. Greeting — LLM-authored, await playout so first question doesn't overlap.
        greeting_handle = self.session.generate_reply(
            instructions=self._greeting_instruction(),
            allow_interruptions=False,
        )
        try:
            await greeting_handle.wait_for_playout()
        except Exception as exc:  # noqa: BLE001
            log.warning("controller.greeting.drain_failed", error=str(exc))

        # 2. Sequential task loop.
        sorted_questions = mandatory_first_then_optional(self._config.stage.questions)
        for q in sorted_questions:
            if self._end_outcome is not None:
                break
            if self._budget.is_expired(now=time.monotonic()):
                self._end_outcome = "time_expired"
                break

            # Signal-disclaim subsumption — cheap, no budget cost.
            if self._is_signal_disclaim_subsumed(q):
                self._collector.append(
                    kind="controller.intent.signal_disclaim_skip",
                    payload={
                        "question_id": q.id,
                        "subsumed_signals": sorted(set(q.signal_values) & self._disqualified_signals),
                    },
                    wall_ms=now_ms(),
                )
                bridge_handle = self.session.generate_reply(
                    instructions=self._signal_disclaim_bridge_instruction(q),
                    allow_interruptions=False,
                )
                try:
                    await bridge_handle.wait_for_playout()
                except Exception as exc:  # noqa: BLE001
                    log.warning("controller.bridge.drain_failed", error=str(exc))
                continue

            # Budget check.
            if not self._budget.has_remaining_for(q, now=time.monotonic()):
                if q.is_mandatory:
                    trimmed = self._budget.trim_to_remaining(q, now=time.monotonic())
                    if trimmed <= 0:
                        self._end_outcome = "time_expired"
                        break
                    await self._dispatch_task(q, watchdog_seconds=trimmed)
                else:
                    self._collector.append(
                        kind="controller.skip.budget",
                        payload={
                            "question_id": q.id,
                            "remaining_seconds": int(self._budget.remaining(now=time.monotonic())),
                        },
                        wall_ms=now_ms(),
                    )
                    continue
            else:
                await self._dispatch_task(
                    q,
                    watchdog_seconds=q.estimated_minutes * 60.0
                        + settings.engine_task_budget_overhead_seconds,
                )

        # 3. Single convergence point — terminate exactly once.
        await self._terminate(self._end_outcome or "completed")

    async def _idle_nudge_loop(self) -> None:
        """1Hz tick driver. Reacts to state-machine output."""
        try:
            while not self._terminated:
                await asyncio.sleep(1.0)
                output = self._idle_nudge_state.on_tick(now_seconds=time.monotonic())
                if output is IdleNudgeOutput.NUDGE_ONE:
                    self._collector.append(
                        kind="controller.intent.idle_nudge",
                        payload={"nudge_number": 1},
                        wall_ms=now_ms(),
                    )
                    self.session.generate_reply(
                        instructions=self._idle_nudge_instruction(1),
                        allow_interruptions=False,
                    )
                elif output is IdleNudgeOutput.NUDGE_TWO:
                    self._collector.append(
                        kind="controller.intent.idle_nudge",
                        payload={"nudge_number": 2},
                        wall_ms=now_ms(),
                    )
                    self.session.generate_reply(
                        instructions=self._idle_nudge_instruction(2),
                        allow_interruptions=False,
                    )
                elif output is IdleNudgeOutput.END_UNRESPONSIVE:
                    self._end_outcome = "candidate_unresponsive"
                    if self._current_task_run is not None and not self._current_task_run.done():
                        self._current_task_run.cancel()
                    return
        except asyncio.CancelledError:
            return  # _terminate cancelled us — clean exit

    # Method called from agent.py's _wire_session_observability when a
    # UserStateChangedEvent fires. Phase 1 already has the listener; we
    # add a one-line call into this method.
    def on_user_state_changed(self, new_state: str) -> None:
        self._idle_nudge_state.on_user_state(new_state, now_seconds=time.monotonic())

    # ------------------------------------------------------------------
    # Task dispatch + result handling
    # ------------------------------------------------------------------

    async def _dispatch_task(self, q: QuestionConfig, *, watchdog_seconds: float) -> None:
        task = build_task_for(
            q,
            controller=self,
            disqualified_signals=frozenset(self._disqualified_signals),
        )
        self._collector.append(
            kind="task.entered",
            payload={
                "question_id": q.id,
                "kind": task.kind,
                "watchdog_seconds": int(watchdog_seconds),
                "max_probes": task.max_probes,
            },
            wall_ms=now_ms(),
        )
        self._current_task_run = asyncio.create_task(task.run())
        try:
            result = await asyncio.wait_for(self._current_task_run, timeout=watchdog_seconds)
        except asyncio.TimeoutError:
            result = task.force_complete(reason="task_timeout")
            self._collector.append(
                kind="task.timeout",
                payload={"question_id": q.id, "elapsed_seconds": int(watchdog_seconds)},
                wall_ms=now_ms(),
            )
        except asyncio.CancelledError:
            return  # End-intent or idle-nudge cancelled us; outer loop converges via _end_outcome
        finally:
            self._current_task_run = None

        self._collector.append(
            kind="task.completed",
            payload={
                "question_id": q.id,
                "result_kind": result.kind,
                "forced": result.forced,
                "result": result.model_dump(),
            },
            wall_ms=now_ms(),
        )
        self._handle_task_result(q, result)

    def _handle_task_result(self, q: QuestionConfig, result: TaskResult) -> None:
        for signal in result.signals_lacked:
            self._disqualified_signals.add(signal)
        if result.knockout:
            self._knockout_failures.append(
                KnockoutFailureRecord(
                    question_id=q.id,
                    reason=result.knockout_reason or "",
                    signal_values=list(q.signal_values),
                    occurred_at_ms=now_ms() - self._session_start_ms,
                )
            )
            self._collector.append(
                kind="disqualify.knockout",
                payload={
                    "question_id": q.id,
                    "reason_chars": len(result.knockout_reason or ""),
                    "reason": result.knockout_reason or "",
                },
                wall_ms=now_ms(),
            )
            # Phase 5 will read self._tenant_policy here and break on close_polite.

    def _is_signal_disclaim_subsumed(self, q: QuestionConfig) -> bool:
        """True iff every signal in q.signal_values is in disqualified_signals.

        Set-intersection-equals-set semantics. Empty signal_values would
        return True trivially; the schema enforces min_length=1 so this
        edge case can't arise in practice.
        """
        if not q.signal_values:
            return False
        return set(q.signal_values).issubset(self._disqualified_signals)

    # ------------------------------------------------------------------
    # Termination
    # ------------------------------------------------------------------

    async def _terminate(self, outcome: SessionOutcome) -> None:
        if self._terminated:
            log.warning("controller.terminate.already_in_progress", outcome=outcome)
            return
        self._terminated = True

        # Stop the idle-nudge tick.
        if self._idle_nudge_tick_task is not None and not self._idle_nudge_tick_task.done():
            self._idle_nudge_tick_task.cancel()

        # Cancel any still-running task (defensive).
        if self._current_task_run is not None and not self._current_task_run.done():
            self._current_task_run.cancel()

        # Wait for any in-flight LLM/TTS turn (e.g. the LLM's tool-ack from
        # end_interview_early) to finish so we don't talk over it.
        try:
            in_flight = self.session.current_speech
            if in_flight is not None:
                await asyncio.wait_for(
                    in_flight.wait_for_playout(),
                    timeout=settings.engine_closing_drain_timeout_seconds,
                )
        except (asyncio.TimeoutError, Exception) as exc:
            log.warning("controller.close.in_flight_drain_failed", error=str(exc), outcome=outcome)

        # Compose the closing line.
        closing_handle: SpeechHandle | None = None
        try:
            closing_handle = self.session.generate_reply(
                instructions=closing_instructions_for(outcome, self._config),
                allow_interruptions=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("controller.close.compose_failed", error=str(exc), outcome=outcome)

        # Persist BEFORE drain — durable artifact must survive a stuck TTS.
        await self._persist_session_result(outcome)

        # Drain the closing line.
        if closing_handle is not None:
            try:
                await asyncio.wait_for(
                    closing_handle.wait_for_playout(),
                    timeout=settings.engine_closing_drain_timeout_seconds,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                log.warning("controller.close.drain_failed", error=str(exc), outcome=outcome)

        # Publish session_outcome for the candidate's frontend.
        await self._publish_session_outcome(outcome)

        # Shutdown with retry.
        await _safe_shutdown(self.session, max_attempts=3)

    async def _persist_session_result(self, outcome: SessionOutcome) -> None:
        """Persist the SessionResult exactly once."""
        if self._persisted:
            return
        result = self._build_session_result(outcome)
        async with get_bypass_session() as db:
            await record_session_result(
                db,
                session_id=uuid.UUID(self._config.session_id),
                tenant_id=self._tenant_id,
                result=result,
                correlation_id=self._correlation_id,
            )
            await db.commit()
        self._persisted = True
        log.info("controller.result.persisted", session_id=self._config.session_id, outcome=outcome)

    def _build_session_result(self, outcome: SessionOutcome) -> SessionResult:
        """Compile a SessionResult. Phase 2 keeps the existing shape;
        Phase 5 adds knockout_failures."""
        # Existing shape — copies the relevant subset of InterviewerAgent's
        # _build_session_result. We don't have full per-question observation
        # depth in Phase 2 (that lives inside the task; the controller only
        # sees aggregate TaskResult), so question_results is a thinner version.
        from app.modules.interview_runtime import QuestionResult

        question_results: list[QuestionResult] = []
        for q in self._config.stage.questions:
            question_results.append(
                QuestionResult(
                    question_id=q.id,
                    question_text=q.text,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                    was_skipped=False,  # Phase 5 wires real skip tracking
                    probes_fired=0,     # Phase 3 wires real probe counts via TaskResult
                    observations=[],
                    transcript_entries=[],
                )
            )
        return SessionResult(
            session_id=self._config.session_id,
            job_title=self._config.job_title,
            stage_id=self._config.stage.stage_id,
            stage_type=self._config.stage.stage_type,
            candidate_name=self._config.candidate.name,
            duration_seconds=time.monotonic() - self._session_start_monotonic,
            questions_asked=len(self._config.stage.questions),
            questions_skipped=0,
            total_probes_fired=0,
            question_results=question_results,
            full_transcript=[],
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Helper: prompt instructions for situational LLM turns
    # ------------------------------------------------------------------

    def _greeting_instruction(self) -> str:
        return (
            f"Greet the candidate {self._config.candidate.name} for the "
            f"{self._config.job_title} interview. Mention this will take about "
            f"{self._config.stage.duration_minutes} minutes and cover "
            f"{len(self._config.stage.questions)} questions. Keep it brief — "
            "two short sentences — then move to the first question."
        )

    def _signal_disclaim_bridge_instruction(self, q: QuestionConfig) -> str:
        return (
            f"The candidate already disclaimed every signal this question would "
            f"probe. Briefly acknowledge that and bridge to the next question "
            "naturally. One short sentence. Do not name the specific signal."
        )

    def _idle_nudge_instruction(self, nudge_number: int) -> str:
        if nudge_number == 1:
            return (
                "The candidate has been silent for a while. Briefly check if "
                "they're still there. Friendly, not pushy. One short sentence."
            )
        return (
            "The candidate hasn't responded to your check-in. Try once more "
            "warmly — confirm you can be heard. One short sentence."
        )

    # ------------------------------------------------------------------
    # Phase 1 progress / outcome publishing — preserved from InterviewerAgent
    # ------------------------------------------------------------------

    async def _publish_progress_attributes(self) -> None:
        """Best-effort publish of progress for the candidate's ProgressBanner."""
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes({
                "current_question_index": "0",
                "total_questions": str(len(self._config.stage.questions)),
                "time_remaining_seconds": str(int(self._budget.remaining(now=time.monotonic())))
                    if self._budget.started_at_monotonic > 0
                    else str(int(self._config.stage.duration_minutes * 60)),
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("controller.progress.publish_failed", error=str(exc))

    async def _publish_session_outcome(self, outcome: SessionOutcome) -> None:
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes({"session_outcome": outcome})
        except Exception as exc:  # noqa: BLE001
            log.warning("controller.outcome.publish_failed", outcome=outcome, error=str(exc))

    # ------------------------------------------------------------------
    # @function_tool surface
    # ------------------------------------------------------------------

    @function_tool()
    async def end_interview_early(
        self,
        ctx: RunContext,
        reason: Literal["candidate_request"],
    ) -> str:
        """Call ONLY when the candidate explicitly asks to stop the interview.

        Examples that DO trigger:
          - "I'd like to end the interview now."
          - "I have to go."
          - "Can we wrap this up?"

        Examples that do NOT trigger:
          - "I don't know this one."  (frustration; not end-intent)
          - "Can you repeat that?"
          - "Can we move on?"  (move past one question, not end the whole interview)

        Reply briefly with "Okay." after calling — the controller composes
        the actual closing.
        """
        self._collector.append(
            kind="controller.intent.end_early",
            payload={"reason": reason},
            wall_ms=now_ms(),
        )
        self._end_outcome = "candidate_ended"
        if self._current_task_run is not None and not self._current_task_run.done():
            self._current_task_run.cancel()
        return "Reply with a brief 'Okay.' — the interview will wrap up after this turn."

    @function_tool()
    async def flag_safety_concern(
        self,
        ctx: RunContext,
        category: Literal[
            "harassment",
            "threats_to_self",
            "threats_to_others",
            "inappropriate_request",
            "other",
        ],
        note: str,
    ) -> str:
        """Record a safety concern. Continue the interview after calling.

        Use this when the candidate makes statements that fit one of:
          - harassment: directed at you (the AI) or referencing harassment.
          - threats_to_self: self-harm statements or imminent danger.
          - threats_to_others: violent intent toward others.
          - inappropriate_request: e.g. asking you to engage in romantic talk,
            requesting answers to other interviews, etc.
          - other: anything else worth flagging for human review.

        The note should be a brief factual third-person summary — no
        commentary, no quotes longer than necessary.

        Calling this DOES NOT end the interview. Continue normally.
        """
        self._collector.append(
            kind="controller.intent.flag_safety_concern",
            payload={"category": category, "note_chars": len(note), "note": note},
            wall_ms=now_ms(),
        )
        return "Concern recorded. Continue the interview professionally."

    @function_tool()
    async def report_technical_issue(
        self,
        ctx: RunContext,
        description: str,
    ) -> str:
        """Record a candidate-reported technical problem with the call.

        Use this when the candidate says they can't hear you, the audio is
        choppy, the connection is bad, or similar. After calling, briefly
        acknowledge to the candidate ("Let me know if that's still an issue")
        and continue.
        """
        self._collector.append(
            kind="controller.intent.report_technical_issue",
            payload={"description_chars": len(description), "description": description},
            wall_ms=now_ms(),
        )
        return "Issue logged. Briefly acknowledge to the candidate and continue."


# ----------------------------------------------------------------------
# _safe_shutdown — module-level so it's straightforward to monkeypatch
# ----------------------------------------------------------------------

async def _safe_shutdown(session, *, max_attempts: int = 3) -> None:
    """Retry session.aclose with exponential backoff (0.5s, 1s, 2s)."""
    for attempt in range(max_attempts):
        try:
            await session.aclose()
            log.info("controller.shutdown.ok", attempt=attempt)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "controller.shutdown.retry",
                attempt=attempt,
                error=str(exc),
            )
            await asyncio.sleep(0.5 * (2 ** attempt))
    log.error("controller.shutdown.exhausted")
