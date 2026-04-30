"""Deterministic interview state machine.

Controls question progression, probe limits, time management, and
skip decisions. The LLM is conversational talent — this module is
the controller. Pure logic, no LLM or LiveKit dependency.
"""

from __future__ import annotations

import math
import time
from enum import StrEnum

from app.modules.interview_runtime.schemas import (
    QuestionConfig,
    SessionConfig,
    SteeringObservation,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InterviewPhase(StrEnum):
    GREETING = "greeting"
    ASKING = "asking"
    LISTENING = "listening"
    PROCESSING = "processing"
    PROBING = "probing"
    ADVANCING = "advancing"
    SKIPPING = "skipping"
    CLOSING = "closing"
    ENDED = "ended"


class Action(StrEnum):
    PROBE = "probe"
    ADVANCE = "advance"
    SKIP = "skip"
    CLOSE = "close"


# ---------------------------------------------------------------------------
# Mutable interview state
# ---------------------------------------------------------------------------


class InterviewState:
    """Mutable state tracked throughout the interview.

    Plain class — not a Pydantic model — because this is runtime state,
    not a serialization boundary.
    """

    def __init__(
        self,
        session_id: str,
        questions: list[QuestionConfig],
        duration_limit_seconds: float,
        time_warning_threshold: float = 0.8,
    ) -> None:
        self.session_id = session_id
        self.questions = questions
        self.current_question_index: int = 0
        self.probes_fired_for_current: int = 0
        self.questions_asked: list[str] = []
        self.questions_skipped: list[str] = []
        self.observations: dict[str, list[SteeringObservation]] = {}
        self.started_at: float | None = None
        self.duration_limit_seconds = duration_limit_seconds
        self.phase: InterviewPhase = InterviewPhase.GREETING
        self._time_warning_threshold = time_warning_threshold

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Record the session start time and set phase to GREETING."""
        self.started_at = time.monotonic()
        self.phase = InterviewPhase.GREETING

    # -- time helpers --------------------------------------------------------

    def elapsed_seconds(self) -> float:
        """Seconds since the interview started."""
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at

    def time_remaining_seconds(self) -> float:
        """Seconds remaining in the interview."""
        return self.duration_limit_seconds - self.elapsed_seconds()

    def is_time_expired(self) -> bool:
        """True when the interview has reached or exceeded its duration."""
        return self.elapsed_seconds() >= self.duration_limit_seconds

    def is_time_critical(self) -> bool:
        """True when elapsed time has passed the warning threshold."""
        return self.elapsed_seconds() >= (
            self.duration_limit_seconds * self._time_warning_threshold
        )

    # -- question navigation -------------------------------------------------

    def current_question(self) -> QuestionConfig | None:
        """Return the current question, or None if all questions exhausted."""
        if self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    def peek_next_question(self) -> QuestionConfig | None:
        """Return the next question without advancing, or None."""
        next_idx = self.current_question_index + 1
        if next_idx < len(self.questions):
            return self.questions[next_idx]
        return None

    def mandatory_remaining(self) -> int:
        """Count of mandatory questions not yet asked."""
        asked = set(self.questions_asked)
        return sum(
            1
            for q in self.questions
            if q.is_mandatory and q.id not in asked
        )

    def should_skip_optional(self) -> bool:
        """True when time is critical and mandatory questions remain."""
        return self.is_time_critical() and self.mandatory_remaining() > 0


# ---------------------------------------------------------------------------
# State machine controller
# ---------------------------------------------------------------------------

_GENERIC_FOLLOW_UP = "Can you elaborate on that with a specific example?"


class InterviewStateMachine:
    """Deterministic controller for an AI-led interview session.

    Takes a ``SessionConfig``, sorts questions (mandatory first, then
    optional — each group ordered by position), and exposes
    ``decide_next_action`` / ``execute_action`` for the agent loop.
    """

    def __init__(
        self,
        session_config: SessionConfig,
        max_probes_per_question: int = 2,
        time_warning_threshold: float = 0.8,
    ) -> None:
        self.session_config = session_config
        self.max_probes_per_question = max_probes_per_question

        # Sort: mandatory first (by position), then optional (by position).
        all_questions = list(session_config.stage.questions)
        mandatory = sorted(
            [q for q in all_questions if q.is_mandatory],
            key=lambda q: q.position,
        )
        optional = sorted(
            [q for q in all_questions if not q.is_mandatory],
            key=lambda q: q.position,
        )
        sorted_questions = mandatory + optional

        self.state = InterviewState(
            session_id=session_config.session_id,
            questions=sorted_questions,
            duration_limit_seconds=session_config.stage.duration_minutes * 60,
            time_warning_threshold=time_warning_threshold,
        )

    # -- public API ----------------------------------------------------------

    def decide_next_action(self, observation: SteeringObservation) -> Action:
        """Decide what to do after the LLM reports an observation.

        This is the core decision function. Called after each candidate
        answer has been summarised by the LLM.
        """
        # Record the observation against the current question.
        q = self.state.current_question()
        if q:
            self.state.observations.setdefault(q.id, []).append(observation)

        # 1. Hard stop — time expired.
        if self.state.is_time_expired():
            return Action.CLOSE

        # 1b. Candidate explicitly wants to end the interview.
        if observation.candidate_disengaged:
            return Action.CLOSE

        # 2. Probe — LLM wants deeper AND probes remaining AND time permits
        #    AND the candidate actually gave a substantive (even if weak)
        #    answer.  If the answer is a flat "I don't know" or similar
        #    non-answer, probing is pointless — the follow-up questions
        #    assume the candidate has SOME experience to dig into.
        if (
            observation.wants_to_probe
            and self.state.probes_fired_for_current < self.max_probes_per_question
            and not self.state.is_time_critical()
            and not self._is_non_answer(observation)
        ):
            return Action.PROBE

        # 3. All questions done?
        next_q = self.state.peek_next_question()
        if next_q is None:
            return Action.CLOSE

        # 4. Skip optional under time pressure.
        if not next_q.is_mandatory and self.state.should_skip_optional():
            return Action.SKIP

        # 5. Normal advance.
        return Action.ADVANCE

    def execute_action(self, action: Action) -> str:
        """Apply *action* to state and return a context string for the LLM."""
        match action:
            case Action.PROBE:
                follow_up = self._get_follow_up()
                self.state.probes_fired_for_current += 1
                self.state.phase = InterviewPhase.PROBING
                remaining = (
                    self.max_probes_per_question
                    - self.state.probes_fired_for_current
                )
                return (
                    f"The answer needs more depth. Ask this follow-up naturally: "
                    f"'{follow_up}'\n"
                    f"Probes remaining for this question: {remaining}"
                )
            case Action.ADVANCE:
                q = self._advance_to_next()
                self.state.phase = InterviewPhase.ASKING
                return self._format_question_context(q)
            case Action.SKIP:
                skipped, next_q = self._skip_and_advance()
                self.state.phase = InterviewPhase.ASKING
                if next_q is None:
                    self.state.phase = InterviewPhase.CLOSING
                    return self._closing_instruction()
                return self._format_question_context(next_q)
            case Action.CLOSE:
                self.state.phase = InterviewPhase.CLOSING
                return self._closing_instruction()

    def get_greeting_instruction(self) -> str:
        """Return the opening greeting instruction for the LLM."""
        candidate = self.session_config.candidate.name
        role = self.session_config.job_title
        duration = self.session_config.stage.duration_minutes
        return (
            f"Greet the candidate. Their name is {candidate}. "
            f"You are interviewing them for the {role} position. "
            f"The interview will take about {duration} minutes and cover "
            f"{len(self.state.questions)} questions. "
            f"Keep the greeting brief — 2-3 sentences — then move to the first question."
        )

    def get_first_question_context(self) -> str:
        """Return the context injection for the first question."""
        q = self.state.current_question()
        if q is None:
            return self._closing_instruction()
        self.state.questions_asked.append(q.id)
        self.state.phase = InterviewPhase.ASKING
        return self._format_question_context(q)

    # -- private helpers -----------------------------------------------------

    def _format_question_context(self, q: QuestionConfig) -> str:
        """Build the context string injected into the LLM for a question."""
        total = len(self.state.questions)
        # questions_asked may already contain this question's id
        asked_count = len(self.state.questions_asked)
        mandatory_label = "Mandatory" if q.is_mandatory else "Optional"
        time_remaining_min = max(
            0, math.ceil(self.state.time_remaining_seconds() / 60)
        )
        probes_used = self.state.probes_fired_for_current
        signals = ", ".join(q.signal_values)
        positives = ", ".join(q.positive_evidence)
        red_flags = ", ".join(q.red_flags)
        return (
            f"Q{asked_count} of {total} | {mandatory_label} | "
            f"{time_remaining_min} min remaining | "
            f"{probes_used}/{self.max_probes_per_question} probes used\n"
            f'Ask: "{q.text}"\n'
            f"Signals probed: {signals}\n"
            f"Listen for: {positives}\n"
            f"Watch for: {red_flags}\n"
            f'Hint: "{q.evaluation_hint}"'
        )

    @staticmethod
    def _is_non_answer(observation: SteeringObservation) -> bool:
        """Detect flat non-answers where probing would be pointless.

        Follow-up probes assume the candidate gave SOME answer to dig
        into.  If the candidate said "I don't know" or "no experience",
        firing a probe like "how long did it take from first alert..."
        is nonsensical and frustrating.
        """
        s = observation.answer_summary.lower()
        non_answer_phrases = (
            "don't know",
            "do not know",
            "don't have experience",
            "do not have experience",
            "no experience",
            "never worked",
            "can't answer",
            "cannot answer",
            "not sure",
            "no idea",
            "could not provide",
            "could not answer",
            "did not provide",
            "unable to answer",
            "declined to answer",
        )
        return any(phrase in s for phrase in non_answer_phrases)

    def _get_follow_up(self) -> str:
        """Return the next unused follow-up for the current question."""
        q = self.state.current_question()
        if q is None:
            return _GENERIC_FOLLOW_UP
        probes_fired = self.state.probes_fired_for_current
        if probes_fired < len(q.follow_ups):
            return q.follow_ups[probes_fired]
        return _GENERIC_FOLLOW_UP

    def _advance_to_next(self) -> QuestionConfig:
        """Move to the next question, reset probe counter, track it."""
        self.state.current_question_index += 1
        self.state.probes_fired_for_current = 0
        q = self.state.current_question()
        assert q is not None, "Called _advance_to_next with no remaining questions"
        self.state.questions_asked.append(q.id)
        return q

    def _skip_and_advance(self) -> tuple[QuestionConfig, QuestionConfig | None]:
        """Skip the next optional question(s) and advance to a valid one.

        May skip multiple optional questions in a row if time is very
        short. Returns ``(last_skipped, next_valid_or_None)``.
        """
        # We are skipping the *next* question (peek), not the current one.
        # First, advance past the current question.
        self.state.current_question_index += 1
        self.state.probes_fired_for_current = 0

        skipped_q = self.state.current_question()
        assert skipped_q is not None, "Called _skip_and_advance with nothing to skip"
        self.state.questions_skipped.append(skipped_q.id)

        # Keep skipping consecutive optional questions while time is critical.
        while True:
            self.state.current_question_index += 1
            self.state.probes_fired_for_current = 0
            next_q = self.state.current_question()
            if next_q is None:
                # Exhausted all questions.
                return skipped_q, None
            if next_q.is_mandatory or not self.state.should_skip_optional():
                # Found a question we should actually ask.
                self.state.questions_asked.append(next_q.id)
                return skipped_q, next_q
            # Still optional + still time-critical: skip it too.
            skipped_q = next_q
            self.state.questions_skipped.append(next_q.id)

    @staticmethod
    def _closing_instruction() -> str:
        """Return the closing instruction for the LLM."""
        return (
            "The interview is complete. Thank the candidate warmly, "
            "mention that they'll hear about next steps soon, and say "
            "goodbye. Keep it brief — 2-3 sentences."
        )
