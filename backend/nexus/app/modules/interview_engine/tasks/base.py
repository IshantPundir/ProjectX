"""QuestionTask abstract base + shared tools + TaskResult model.

A QuestionTask is a LiveKit AgentTask dedicated to one question:
- holds the question's rubric, signal values, evidence keys, etc.
- exposes a per-kind set of @function_tools
- terminates when its terminal tool fires (or force_complete on watchdog)
- returns a TaskResult that the controller folds into its state

The controller dispatches sequentially via:
  task = build_task_for(question, controller, disqualified_signals)
  result = await asyncio.wait_for(task.run(), timeout=watchdog_seconds)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import structlog
from pydantic import BaseModel

from livekit.agents import AgentTask, RunContext, function_tool

if TYPE_CHECKING:
    from app.modules.interview_engine.controller import InterviewController
    from app.modules.interview_runtime.schemas import QuestionConfig


log = structlog.get_logger("interview-engine.tasks.base")


class TaskResult(BaseModel):
    """The typed result of a completed QuestionTask.

    Returned from the terminal tool's complete-the-task path, or built
    by force_complete when the watchdog fires.
    """

    question_id: str
    kind: Literal["technical_depth"]  # extended in Phase 3
    tier: Literal["excellent", "strong", "at_bar", "below_bar"] | None = None
    evidence_keys: list[str] = []
    non_answer: bool = False
    signals_lacked: list[str] = []
    knockout: bool = False
    knockout_reason: str | None = None
    forced: bool = False
    forced_reason: Literal["task_timeout"] | None = None
    probes_fired: int = 0


@dataclass
class _PartialState:
    """Mutable observation state populated by the LLM during the task.

    Used by force_complete to build a sensible result when the watchdog
    fires before the terminal tool was called.
    """

    tier: Literal["excellent", "strong", "at_bar", "below_bar"] | None = None
    evidence_keys: list[str] = field(default_factory=list)
    signals_lacked: list[str] = field(default_factory=list)
    non_answer: bool = False
    knockout: bool = False
    knockout_reason: str | None = None
    probes_fired: int = 0


class QuestionTask(AgentTask, abc.ABC):
    """Abstract base for per-question tasks.

    Subclasses provide:
      * `kind` class attribute (e.g. "technical_depth")
      * `max_probes` class attribute
      * `build_task_instructions()` — the prompt body for this task
      * `run()` — typically the LiveKit AgentTask default; the terminal
        tool calls `self.complete(result)` which makes await Task().run()
        resolve.

    Shared tools available to every subclass:
      * disqualify_knockout(reason: str) — record_only in Phase 2
      * request_clarification() — repeats the question without recording
    """

    kind: str = "technical_depth"  # overridden by subclasses
    max_probes: int = 1  # overridden by subclasses

    def __init__(
        self,
        *,
        question_config: "QuestionConfig",
        controller: "InterviewController",
        disqualified_signals: frozenset[str],
        rubric_internal: str,
    ) -> None:
        self.question_config = question_config
        self.controller = controller
        self.disqualified_signals = disqualified_signals
        self.rubric_internal = rubric_internal
        self._partial = _PartialState()
        super().__init__(instructions=self.build_task_instructions())

    @abc.abstractmethod
    def build_task_instructions(self) -> str:
        """Assemble the per-task prompt body.

        Subclasses load their `prompts/v1/interview/task_<kind>.txt`,
        substitute placeholders (question text, rubric, etc.) and return
        the result. The rubric_internal block must be wrapped in
        `<<INTERNAL_RUBRIC>>...<<END_INTERNAL_RUBRIC>>` markers and the
        prompt must instruct the LLM never to speak that block aloud.
        """

    def force_complete(self, *, reason: Literal["task_timeout"]) -> TaskResult:
        """Build a TaskResult from whatever the LLM had recorded so far.

        Called by the controller's watchdog path when asyncio.wait_for
        times out. Does NOT call self.complete() (the AgentTask is being
        cancelled; there's no run() to resolve).
        """
        return TaskResult(
            question_id=self.question_config.id,
            kind=self.kind,  # type: ignore[arg-type]
            tier=self._partial.tier,
            evidence_keys=list(self._partial.evidence_keys),
            non_answer=self._partial.non_answer,
            signals_lacked=list(self._partial.signals_lacked),
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=True,
            forced_reason=reason,
            probes_fired=self._partial.probes_fired,
        )

    # Helper used by subclasses' record_answer_assessment-style tools.
    # Exposed under a leading-underscore name because it's not a tool.
    def _record_partial_assessment(
        self,
        *,
        tier: Literal["excellent", "strong", "at_bar", "below_bar"],
        evidence_keys: list[str],
        signals_lacked: list[str],
        non_answer: bool,
    ) -> None:
        self._partial.tier = tier
        self._partial.evidence_keys = list(evidence_keys)
        self._partial.signals_lacked = list(signals_lacked)
        self._partial.non_answer = non_answer

    # ------------------------------------------------------------------
    # Shared @function_tools (every subclass inherits these)
    # ------------------------------------------------------------------

    @function_tool()
    async def disqualify_knockout(self, ctx: RunContext, reason: str) -> str:
        """Record that the candidate's answer is a hard fail on this question.

        Use this ONLY when the candidate self-discloses something that
        invalidates a hard requirement of the role (e.g., "I cannot work
        UK shift hours" for a UK-shift role). Do NOT use it for poor
        answers, "I don't know", or vague responses — those are recorded
        via record_answer_assessment with the appropriate tier.

        After calling, you should still call complete_question to end
        this question's task. The interview will continue normally
        (Phase 2 default policy is record_only).
        """
        self._partial.knockout = True
        self._partial.knockout_reason = reason
        # Audit log emitted by the controller's _handle_task_result via
        # the result's knockout fields. Tool itself emits nothing here
        # to avoid double-logging.
        log.info(
            "task.disqualify_knockout",
            question_id=self.question_config.id,
            reason_chars=len(reason),
        )
        return "Knockout recorded. Call complete_question to end this question."

    @function_tool()
    async def request_clarification(self, ctx: RunContext) -> str:
        """Use when the candidate asks you to repeat or rephrase the question.

        Does NOT record an observation. Returns instructions for you to
        rephrase the question once and listen again. Do not call this
        if the candidate's response was a non-answer — that's an
        observation tier=below_bar via record_answer_assessment.
        """
        log.info(
            "task.request_clarification",
            question_id=self.question_config.id,
        )
        return (
            "Rephrase the question once, more naturally, and listen for "
            "their answer. Do not record an observation for this turn."
        )
