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

from models import (
    SessionConfig,
    SessionResult,
    QuestionResult,
    SteeringObservation,
    TranscriptEntry,
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
        session_config: SessionConfig,
        engine_config: InterviewEngineConfig,
    ) -> None:
        self.state_machine = InterviewStateMachine(
            session_config=session_config,
            max_probes_per_question=engine_config.max_probes_per_question,
            time_warning_threshold=engine_config.time_warning_threshold,
        )
        self.session_config = session_config
        self.engine_config = engine_config

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
            self._write_result(result)

        return context_injection

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

    def _write_result(self, result: SessionResult) -> None:
        """Write session result to local JSON file."""
        results_dir = Path(self.engine_config.results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)

        output_path = results_dir / f"{result.session_id}.json"
        output_path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logger.info(
            "interview.result_written",
            path=str(output_path),
            session_id=result.session_id,
        )
