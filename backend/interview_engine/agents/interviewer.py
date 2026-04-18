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
        # Problem: on_observation and on_complete fire in the SAME async
        # generator invocation.  If on_observation sets a skip flag and
        # on_complete immediately clears it, the flag is True for
        # microseconds — useless.
        #
        # Solution: _pending_skips counts how many FUTURE streams should
        # have their observations skipped.  _skip_incremented_this_stream
        # prevents on_complete from decrementing the counter that was JUST
        # incremented by on_observation in the same stream.
        #
        # Starts at 1 so the greeting+first-question output is skipped.
        self._pending_skips: int = 1
        self._skip_incremented_this_stream: bool = False
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

        **Gating:** ``_pending_skips`` counts how many future streams should
        have their observations skipped.  When ``_pending_skips > 0``, this
        observation is from an agent-initiated ``generate_reply`` output and
        is ignored.  ``_on_stream_complete`` decrements the counter — but
        only on the NEXT stream, not the one that incremented it (tracked
        via ``_skip_incremented_this_stream``).
        """
        if self._pending_skips > 0:
            logger.debug(
                "observation.skipped",
                pending_skips=self._pending_skips,
                summary=observation.answer_summary[:80] if observation.answer_summary else "",
            )
            return

        logger.info(
            "observation.processing",
            summary=observation.answer_summary[:120],
            signals=observation.signals_demonstrated,
            wants_probe=observation.wants_to_probe,
        )

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
        logger.info(
            "generate_reply.firing",
            action=action.value,
            instruction=context_injection[:300],
        )
        self.session.generate_reply(instructions=context_injection)

        # The generate_reply we just fired will produce ONE output stream.
        # That stream's observation must be skipped (it's the agent speaking,
        # not the candidate answering).  Increment _pending_skips and mark
        # that we did it in THIS stream so on_complete doesn't decrement it
        # prematurely.
        self._pending_skips += 1
        self._skip_incremented_this_stream = True
        logger.debug("gate.skip_queued", pending_skips=self._pending_skips)

        # Safety timeout
        loop = asyncio.get_running_loop()
        if self._gate_safety_timeout:
            self._gate_safety_timeout.cancel()
        self._gate_safety_timeout = loop.call_later(
            15.0, self._force_reopen_gate
        )

    def _on_stream_complete(self, had_observation: bool) -> None:
        """Called by the output parser when each LLM output stream finishes.

        Decrements ``_pending_skips`` so the NEXT candidate-triggered stream
        gets processed.  But does NOT decrement if ``_pending_skips`` was
        incremented during THIS SAME stream (by ``_on_observation``), because
        ``on_observation`` and ``on_complete`` fire sequentially in the same
        generator — decrementing here would undo the increment immediately.
        """
        if self._skip_incremented_this_stream:
            # The skip was set IN this stream's on_observation.
            # Don't touch _pending_skips — let the NEXT stream's
            # on_complete decrement it.
            self._skip_incremented_this_stream = False
            logger.debug(
                "stream.complete_skip_preserved",
                pending_skips=self._pending_skips,
                had_observation=had_observation,
            )
            return

        if self._pending_skips > 0:
            self._pending_skips -= 1
            if self._gate_safety_timeout and self._pending_skips == 0:
                self._gate_safety_timeout.cancel()
                self._gate_safety_timeout = None
            logger.debug(
                "stream.complete_skip_consumed",
                pending_skips=self._pending_skips,
                had_observation=had_observation,
            )
        else:
            logger.debug(
                "stream.complete",
                pending_skips=self._pending_skips,
                had_observation=had_observation,
            )

    def _force_reopen_gate(self) -> None:
        """Safety fallback: clear the gate if on_stream_complete never fires."""
        logger.warning(
            "gate.force_cleared",
            pending_skips=self._pending_skips,
        )
        self._pending_skips = 0
        self._skip_incremented_this_stream = False
        self._gate_safety_timeout = None

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
