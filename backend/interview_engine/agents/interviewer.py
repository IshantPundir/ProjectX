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

        # Observation gating: prevents the runaway loop where
        # generate_reply → parser → observation → generate_reply cycles
        # without waiting for the candidate to speak.
        #
        # _skip_next_observation: set True when we call generate_reply.
        # The NEXT observation callback (from that generate_reply's output)
        # is skipped. Cleared by _on_stream_complete when the stream finishes.
        #
        # Starts True so the greeting+first-question output is skipped.
        self._skip_next_observation: bool = True
        self._gate_safety_timeout: asyncio.TimerHandle | None = None

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
        # _skip_next_observation is already True (set in __init__), so the
        # greeting output's observation will be skipped.  _on_stream_complete
        # clears the flag when the greeting stream finishes, readying the
        # gate for the candidate's first answer.
        self.session.generate_reply(
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
        observation → state machine decision → context injection → next turn

        **Gating:** ``_skip_next_observation`` prevents processing observations
        from the agent's own ``generate_reply`` output.  The flag is set True
        before each ``generate_reply`` call and cleared by ``_on_stream_complete``
        when that stream finishes.  This is event-driven (not timer-based), so
        it handles short probes and long questions equally well.
        """
        if self._skip_next_observation:
            logger.debug("observation.skipped_agent_initiated")
            return

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

        if action == Action.CLOSE:
            loop = asyncio.get_running_loop()
            loop.create_task(self._close_interview(context_injection))
            return

        # PROBE / ADVANCE / SKIP — fire the speech.
        # generate_reply() is synchronous, returns a SpeechHandle.
        self.session.generate_reply(instructions=context_injection)

        # Mark that the NEXT stream (from this generate_reply) should be
        # skipped.  _on_stream_complete will clear this when that stream
        # finishes, re-opening the gate for the candidate's next answer.
        self._skip_next_observation = True

        # Safety timeout: if _on_stream_complete never fires (e.g. LLM
        # error, stream dropped), force-clear after 15s so the interview
        # doesn't freeze.
        loop = asyncio.get_running_loop()
        if self._gate_safety_timeout:
            self._gate_safety_timeout.cancel()
        self._gate_safety_timeout = loop.call_later(
            15.0, self._force_reopen_gate
        )

    def _on_stream_complete(self, had_observation: bool) -> None:
        """Called by the output parser when each LLM output stream finishes.

        If ``_skip_next_observation`` is True, this stream was from an
        agent-initiated ``generate_reply``.  Clear the flag so the NEXT
        observation (from the candidate's answer) gets processed.
        """
        if self._skip_next_observation:
            self._skip_next_observation = False
            if self._gate_safety_timeout:
                self._gate_safety_timeout.cancel()
                self._gate_safety_timeout = None
            logger.debug(
                "observation.gate_cleared",
                stream_had_observation=had_observation,
            )

    def _force_reopen_gate(self) -> None:
        """Safety fallback: clear the gate if _on_stream_complete never fires."""
        self._skip_next_observation = False
        self._gate_safety_timeout = None
        logger.warning("observation.gate_force_cleared_by_timeout")

    # ------------------------------------------------------------------
    # Interview closure
    # ------------------------------------------------------------------

    async def _close_interview(self, closing_instruction: str) -> None:
        """Handle interview closure: farewell + result writing."""
        # generate_reply returns a SpeechHandle; await it to wait for
        # the farewell speech to finish before writing results.
        handle = self.session.generate_reply(
            instructions=closing_instruction,
            allow_interruptions=False,
        )
        await handle

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
