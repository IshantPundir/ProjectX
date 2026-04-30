"""InterviewerAgent -- structured interview conductor.

Single LiveKit Agent that drives a structured technical interview.
Uses the InterviewStateMachine for deterministic question control
and @function_tool for mid-response observation capture + state
machine injection.

Flow per candidate answer:
  1. Candidate speaks → LLM auto-responds
  2. LLM acknowledges briefly ("Got it.")
  3. LLM calls record_observation tool with its observations
  4. Tool executes: state machine decides → returns next instruction
  5. LLM reads tool result → asks the next question/probe/closes
  6. Candidate hears ONE smooth response: "Got it. [next question]"

No generate_reply. No gating. No output parser. The tool gives the
state machine control at exactly the right moment — between the
acknowledgment and the next question.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from livekit.agents import Agent, RunContext, function_tool

from app.modules.interview_runtime.schemas import (
    QuestionResult,
    SessionConfig,
    SessionResult,
    SteeringObservation,
    TranscriptEntry,
)
from nexus_client import (
    ResultPostFailedError,
    ResultRejectedError,
    post_session_result,
)
from state_machine import InterviewStateMachine, Action
from prompt_builder import build_system_prompt
from config import InterviewEngineConfig

logger = structlog.get_logger(__name__)


class InterviewerAgent(Agent):
    """LiveKit Agent that conducts a structured AI-led interview.

    Owns the :class:`InterviewStateMachine` and exposes a single
    ``@function_tool`` (``record_observation``) that the LLM calls
    after each candidate answer.  The tool returns the state machine's
    next instruction, which the LLM incorporates into its continued
    response — producing one smooth reply per turn.
    """

    def __init__(
        self,
        *,
        session_config: SessionConfig,
        engine_config: InterviewEngineConfig,
        nexus_jwt: str,
        nexus_base_url: str,
    ) -> None:
        self.state_machine = InterviewStateMachine(
            session_config=session_config,
            max_probes_per_question=engine_config.max_probes_per_question,
            time_warning_threshold=engine_config.time_warning_threshold,
        )
        self.session_config = session_config
        self.engine_config = engine_config
        self.nexus_jwt = nexus_jwt
        self.nexus_base_url = nexus_base_url

        system_prompt = build_system_prompt(session_config, engine_config)

        self._transcript: list[TranscriptEntry] = []
        self._session_start_ms: int = 0

        super().__init__(instructions=system_prompt)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_enter(self) -> None:
        """Greet the candidate and ask the first question."""
        self.state_machine.state.start()
        self._session_start_ms = int(time.monotonic() * 1000)

        # Publish initial progress attributes so the candidate's
        # ProgressBanner ("Q1 of N · X min remaining") is present
        # before the agent's audio greeting starts.
        await self._publish_progress_attributes()

        greeting = self.state_machine.get_greeting_instruction()
        first_q = self.state_machine.get_first_question_context()

        self.session.generate_reply(
            instructions=(
                f"{greeting}\n\n"
                f"Then immediately ask the first question:\n{first_q}"
            ),
            allow_interruptions=False,
        )

        logger.info(
            "interview.started",
            session_id=self.session_config.session_id,
            candidate=self.session_config.candidate.name,
            question_count=len(self.state_machine.state.questions),
            duration_minutes=self.session_config.stage.duration_minutes,
        )

    # ------------------------------------------------------------------
    # Function tool — the core interview loop
    # ------------------------------------------------------------------

    @function_tool()
    async def record_observation(
        self,
        context: RunContext,
        answer_summary: str,
        signals_demonstrated: list[str],
        wants_to_probe: bool,
        candidate_disengaged: bool,
        notes: str,
    ) -> str:
        """Report your observation of the candidate's answer.

        You MUST call this after every candidate answer. Do NOT call it
        when you are asking a question, greeting, or rephrasing.

        Args:
            answer_summary: 2-3 sentence factual summary of what the
                candidate said. No judgment.
            signals_demonstrated: Signal values from the current question
                that the candidate demonstrated with concrete evidence.
                Empty list if none were evidenced.
            wants_to_probe: True if the answer was vague or lacked
                specifics. False if substantive regardless of quality.
            candidate_disengaged: True ONLY if the candidate explicitly
                says they want to stop (e.g. "I'm done"). NOT for
                "I don't know" — that is a weak answer, not disengagement.
            notes: Free-form observations for the post-session evaluator.

        Returns:
            Instruction for what to say next. Follow it exactly.
        """
        # Reject empty observations — the LLM sometimes calls the tool
        # during the greeting with blank fields, which would advance the
        # state machine before the candidate even speaks.
        if not answer_summary or not answer_summary.strip():
            logger.warning("observation.rejected_empty")
            return (
                "You called this tool without an answer to observe. "
                "Do NOT call record_observation during greetings, "
                "rephrases, or when the candidate has not spoken. "
                "Continue with the current question."
            )

        observation = SteeringObservation(
            answer_summary=answer_summary,
            signals_demonstrated=signals_demonstrated,
            wants_to_probe=wants_to_probe,
            candidate_disengaged=candidate_disengaged,
            notes=notes,
        )

        logger.info(
            "observation.received",
            summary=answer_summary[:120],
            signals=signals_demonstrated,
            wants_probe=wants_to_probe,
            disengaged=candidate_disengaged,
        )

        action = self.state_machine.decide_next_action(observation)
        context_injection = self.state_machine.execute_action(action)

        # Update participant attributes so the candidate's ProgressBanner
        # advances on each turn. Skipped on CLOSE (interview is wrapping
        # up; the next render will be the completion screen).
        if action != Action.CLOSE:
            await self._publish_progress_attributes()

        logger.info(
            "interview.turn",
            action=action.value,
            question_index=self.state_machine.state.current_question_index,
            probes_fired=self.state_machine.state.probes_fired_for_current,
            time_remaining=round(
                self.state_machine.state.time_remaining_seconds()
            ),
            phase=self.state_machine.state.phase.value,
        )

        if action == Action.CLOSE:
            result = self._build_session_result()
            await self._persist_result(result)
            await self._publish_session_outcome("completed")

        return context_injection

    # ------------------------------------------------------------------
    # Progress publishing — drives the candidate-facing ProgressBanner
    # ------------------------------------------------------------------

    async def _publish_progress_attributes(self) -> None:
        """Publish current interview progress as LiveKit participant
        attributes.

        The candidate's frontend ``useStageProgress`` hook reads these
        three string-valued attributes from the agent participant and
        renders ``Q{idx+1} of {total} · {min} min remaining`` in the
        sticky ProgressBanner. Missing or unparseable values cause the
        banner to hide cleanly, so this publish is best-effort: a
        failure here must not abort the turn — the state machine
        progression is the load-bearing thing.
        """
        state = self.state_machine.state
        attrs = {
            "current_question_index": str(state.current_question_index),
            "total_questions": str(len(state.questions)),
            "time_remaining_seconds": str(
                max(0, round(state.time_remaining_seconds()))
            ),
        }
        try:
            # AgentSession does not expose `room` directly; the room handle
            # lives on the `room_io` helper that AgentSession.start spins up
            # when started with a room. Going through `room_io.room` is the
            # supported path to the underlying `rtc.Room` and its
            # `local_participant` (the agent itself in this context).
            room = self.session.room_io.room
            await room.local_participant.set_attributes(attrs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview.progress.publish_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _publish_session_outcome(self, outcome: str) -> None:
        """Publish the final session outcome on the agent's local participant.

        The candidate's frontend ``useSessionOutcome`` hook reads this
        attribute on the Disconnected event to route between
        ``CompletionScreen`` (``outcome='completed'``) and
        ``DisconnectError`` with code ``ENGINE_ERROR`` (``outcome='error'``).

        Best-effort — a failure here must not abort shutdown; the frontend
        falls back to ``UNEXPECTED_DISCONNECT`` in that case, which is still
        better than crashing the agent on the way out.
        """
        try:
            room = self.session.room_io.room
            await room.local_participant.set_attributes(
                {"session_outcome": outcome},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview.outcome.publish_failed",
                outcome=outcome,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # Result compilation
    # ------------------------------------------------------------------

    def _build_session_result(self) -> SessionResult:
        """Compile the final session result from state machine data."""
        state = self.state_machine.state

        question_results: list[QuestionResult] = []
        for q in state.questions:
            was_skipped = q.id in state.questions_skipped
            observations = state.observations.get(q.id, [])
            q_transcript = [
                t for t in self._transcript if t.question_id == q.id
            ]

            question_results.append(
                QuestionResult(
                    question_id=q.id,
                    question_text=q.text,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                    was_skipped=was_skipped,
                    probes_fired=max(0, len(observations) - 1),
                    observations=observations,
                    transcript_entries=q_transcript,
                )
            )

        return SessionResult(
            session_id=self.session_config.session_id,
            job_title=self.session_config.job_title,
            stage_id=self.session_config.stage.stage_id,
            stage_type=self.session_config.stage.stage_type,
            candidate_name=self.session_config.candidate.name,
            duration_seconds=state.elapsed_seconds(),
            questions_asked=len(state.questions_asked),
            questions_skipped=len(state.questions_skipped),
            total_probes_fired=sum(
                max(0, len(obs) - 1)
                for obs in state.observations.values()
            ),
            question_results=question_results,
            full_transcript=self._transcript,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _persist_result(self, result: SessionResult) -> None:
        """Post the session result to nexus; fall back to local-disk write on
        permanent failure.

        Treats nexus's 204 (first success) and 409 (idempotent retry vs. an
        already-completed session) the same — both end the engine's
        persistence responsibility. The fallback path runs only when nexus
        rejected the POST (auth, validation) or all retries on 5xx/network
        were exhausted; in those cases we drop a JSON file in
        ``engine_config.results_fallback_dir`` so the result isn't lost.
        """
        try:
            await post_session_result(
                session_id=result.session_id,
                jwt=self.nexus_jwt,
                result=result,
                base_url=self.nexus_base_url,
            )
            logger.info("engine.result.posted", session_id=result.session_id)
            return
        except (ResultPostFailedError, ResultRejectedError) as exc:
            logger.critical(
                "engine.fallback.local_results_write",
                session_id=result.session_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # Fallback — drop a JSON file for forensics.
        fallback_dir = self.engine_config.results_fallback_dir
        fallback_dir.mkdir(parents=True, exist_ok=True)
        output_path = fallback_dir / f"{result.session_id}.json"
        output_path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.warning(
            "interview.result_fallback_written",
            path=str(output_path),
            session_id=result.session_id,
        )
