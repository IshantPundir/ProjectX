"""InterviewerAgent -- structured interview conductor.

Single LiveKit Agent that drives a structured technical interview.
Uses the InterviewStateMachine for deterministic question control
and structured JSON output for streaming TTS + observation capture.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from livekit.agents import Agent, AgentSession

from models import (
    SessionConfig,
    SessionResult,
    QuestionResult,
    SteeringObservation,
    TranscriptEntry,
)
from state_machine import InterviewStateMachine, Action, InterviewPhase
from prompt_builder import build_system_prompt
from config import InterviewEngineConfig

logger = structlog.get_logger(__name__)


class InterviewerAgent(Agent):
    """LiveKit Agent that conducts a structured AI-led interview.

    Owns the :class:`InterviewStateMachine` and drives question
    progression via ``session.generate_reply(instructions=...)``.
    Exposes ``_on_observation`` as the callback for the structured
    output parser (wired at the session level in ``agent.py``).
    """

    def __init__(
        self,
        session_config: SessionConfig,
        engine_config: InterviewEngineConfig,
    ) -> None:
        # Build the state machine
        self.state_machine = InterviewStateMachine(
            session_config=session_config,
            max_probes_per_question=engine_config.max_probes_per_question,
            time_warning_threshold=engine_config.time_warning_threshold,
        )
        self.session_config = session_config
        self.engine_config = engine_config

        # Build the system prompt
        system_prompt = build_system_prompt(session_config, engine_config)

        # Transcript accumulator
        self._transcript: list[TranscriptEntry] = []
        self._session_start_ms: int = 0

        # Initialize the LiveKit Agent with the system prompt
        super().__init__(instructions=system_prompt)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_enter(self) -> None:
        """Called when the agent becomes active in the session.

        1. Start the state machine timer
        2. Generate a brief greeting
        3. Inject the first question context
        """
        self.state_machine.state.start()
        self._session_start_ms = int(time.monotonic() * 1000)

        # Generate greeting -- the greeting instruction tells the LLM what to say
        greeting_instruction = self.state_machine.get_greeting_instruction()

        # Get the first question context to inject after greeting
        first_q_context = self.state_machine.get_first_question_context()

        # Generate the greeting, then the first question.
        # The LLM will greet, then immediately ask the first question.
        await self.session.generate_reply(
            instructions=(
                f"{greeting_instruction}\n\n"
                f"Then immediately ask the first question:\n{first_q_context}"
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
    # Observation callback -- the core interview loop
    # ------------------------------------------------------------------

    def _on_observation(self, observation: SteeringObservation) -> None:
        """Called by the output parser when a complete observation is captured.

        This is the interview control loop:
        observation -> state machine decision -> context injection -> next turn
        """
        # Let the state machine decide
        action = self.state_machine.decide_next_action(observation)

        # Execute the action and get the context injection
        context_injection = self.state_machine.execute_action(action)

        logger.info(
            "interview.turn",
            action=action.value,
            question_index=self.state_machine.state.current_question_index,
            probes_fired=self.state_machine.state.probes_fired_for_current,
            time_remaining=round(self.state_machine.state.time_remaining_seconds()),
            phase=self.state_machine.state.phase.value,
        )

        # Schedule the async operation on the running event loop.
        # _on_observation is a sync callback invoked from the output parser.
        loop = asyncio.get_running_loop()

        if action == Action.CLOSE:
            loop.create_task(self._close_interview(context_injection))
            return

        # For PROBE, ADVANCE, SKIP -- inject the next instruction
        loop.create_task(
            self.session.generate_reply(instructions=context_injection)
        )

    # ------------------------------------------------------------------
    # Interview closure
    # ------------------------------------------------------------------

    async def _close_interview(self, closing_instruction: str) -> None:
        """Handle interview closure: farewell + result writing."""
        await self.session.generate_reply(
            instructions=closing_instruction,
            allow_interruptions=False,
        )

        # Write results
        result = self._build_session_result()
        self._write_result(result)

        logger.info(
            "interview.completed",
            session_id=self.session_config.session_id,
            questions_asked=result.questions_asked,
            questions_skipped=result.questions_skipped,
            duration_seconds=round(result.duration_seconds, 1),
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
            q_transcript = [t for t in self._transcript if t.question_id == q.id]

            question_results.append(
                QuestionResult(
                    question_id=q.id,
                    question_text=q.text,
                    position=q.position,
                    is_mandatory=q.is_mandatory,
                    was_skipped=was_skipped,
                    # First observation is the initial answer; probes are the rest
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
                max(0, len(obs) - 1) for obs in state.observations.values()
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
