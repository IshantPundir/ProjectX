"""BehavioralStarTask — Phase 3 task for behavioral STAR questions.

Lifecycle:
  1. Controller dispatches: result = await task (with sibling watchdog).
  2. AgentTask boots; LLM speaks the question in ≤25-word spoken form.
  3. Candidate answers.
  4. LLM calls record_behavioral_answer with nullable STAR fields.
  5. Tool tells LLM what's missing + how many probes remain.
  6. LLM either calls request_star_probe or complete_question.
  7. After a probe, LLM calls record_behavioral_answer AGAIN (cumulative).
  8. Loop until tool says complete; LLM calls complete_question.
  9. complete_question calls self.complete(result), which resolves the
     controller's `await task` with a TaskResult.
"""

from __future__ import annotations

from string import Template
from typing import Literal

import structlog

from livekit.agents import RunContext, function_tool

from app.ai.prompts import prompt_loader
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)


log = structlog.get_logger("interview-engine.tasks.behavioral")


_PROMPT_NAME = "interview/task_behavioral"
_STAR_COMPONENTS: tuple[str, ...] = ("situation", "task", "action", "result")


class BehavioralStarTask(QuestionTask):
    """Per-question task for behavioral STAR questions.

    Tools:
      * record_behavioral_answer — observation; cumulative across calls
      * request_star_probe — fires a targeted follow-up; bumps probe counter
      * complete_question — terminal; resolves the controller's `await task`
      * (inherited) disqualify_knockout, request_clarification
    """

    kind = "behavioral_star"
    max_probes = 2

    def __init__(
        self,
        *,
        question_config,
        controller,
        disqualified_signals,
        rubric_internal,
    ) -> None:
        super().__init__(
            question_config=question_config,
            controller=controller,
            disqualified_signals=disqualified_signals,
            rubric_internal=rubric_internal,
        )
        self._probes_fired: int = 0
        self._last_filled_component_count: int = 0
        # Initialize star_components on the partial state.
        self._partial.star_components = {
            "situation": None, "task": None, "action": None, "result": None,
        }

    def build_task_instructions(self) -> str:
        """Load the prompt template and substitute the question's data."""
        template = Template(prompt_loader.get(_PROMPT_NAME))
        return template.substitute(
            question_text=self.question_config.text,
            rubric_internal=self.rubric_internal,
        )

    # ------------------------------------------------------------------
    # @function_tools
    # ------------------------------------------------------------------

    @function_tool()
    async def record_behavioral_answer(
        self,
        ctx: RunContext,
        situation: str | None,
        task: str | None,
        action: str | None,
        result: str | None,
    ) -> str:
        """Record what STAR components the candidate covered.

        Each parameter is either a short summary (≤20 words) of what the
        candidate said for that component, or None if they didn't cover it.
        Cumulative — a second call (after a probe) fills in newly-covered
        components while keeping previously-covered ones in place.
        """
        # Cumulative update — only overwrite a slot if the new value is non-None.
        components = self._partial.star_components
        if situation is not None:
            components["situation"] = situation
        if task is not None:
            components["task"] = task
        if action is not None:
            components["action"] = action
        if result is not None:
            components["result"] = result

        filled = [k for k, v in components.items() if v is not None]
        missing = [k for k, v in components.items() if v is None]
        probes_left = self.max_probes - self._probes_fired

        log.info(
            "task.behavioral.observation_recorded",
            question_id=self.question_config.id,
            filled_components=filled,
            missing_components=missing,
            probes_fired=self._probes_fired,
        )

        if not filled:
            # All components null — non-answer.
            self._partial.non_answer = True
            return (
                "Non-answer recorded. Do not probe — call complete_question now."
            )

        # At least one component was filled at some point.
        self._partial.non_answer = False

        if not missing:
            return (
                "Complete answer recorded. Call complete_question to move on."
            )

        if probes_left <= 0:
            return (
                "Components still missing but probe budget exhausted. "
                "Call complete_question."
            )

        return (
            f"Components missing: {missing}. {probes_left} probe(s) remaining. "
            f"Call request_star_probe with the most important missing component "
            f"(typically Action first, then Result), then listen and call "
            f"record_behavioral_answer again."
        )

    @function_tool()
    async def request_star_probe(
        self,
        ctx: RunContext,
        missing_component: Literal["situation", "task", "action", "result"],
    ) -> str:
        """Fire a targeted follow-up probe for one missing STAR component.

        Refuses if probe budget is exhausted, if the current state is a
        non-answer (all components null — Q5 case B), or if no progress was
        made since the last probe (Q5 case C).
        """
        if self._probes_fired >= self.max_probes:
            return "Probe budget exhausted. Call complete_question instead."

        components = self._partial.star_components
        filled_count = sum(1 for v in components.values() if v is not None)

        if filled_count == 0:
            # Q5 case B — non-answer, no probing.
            return (
                "Cannot probe a non-answer. Record the null state and call "
                "complete_question."
            )

        if self._probes_fired > 0 and filled_count <= self._last_filled_component_count:
            # Q5 case C — probe-then-non-answer, no new coverage since last probe.
            return (
                "No progress since last probe (no new components covered). "
                "Call complete_question instead of probing again."
            )

        self._probes_fired += 1
        self._last_filled_component_count = filled_count
        self._partial.probes_fired = self._probes_fired

        log.info(
            "task.behavioral.probe_fired",
            question_id=self.question_config.id,
            probe_number=self._probes_fired,
            missing_component=missing_component,
        )

        return (
            f"Ask one natural follow-up that targets the missing '{missing_component}' "
            f"component. Phrase it as curiosity, not as a second-chance offer "
            f"(e.g., 'Walk me through what you actually did' for action). "
            f"After their reply, call record_behavioral_answer again."
        )

    @function_tool()
    async def complete_question(self, ctx: RunContext) -> str:
        """Terminal tool — ends this question's task.

        Builds a TaskResult from recorded state and resolves the controller's
        outer ``await task`` via ``self.complete(result)``.
        """
        result = TaskResult(
            question_id=self.question_config.id,
            kind="behavioral_star",
            non_answer=self._partial.non_answer,
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=False,
            probes_fired=self._probes_fired,
            star_components=dict(self._partial.star_components),
        )
        log.info(
            "task.behavioral.completed",
            question_id=self.question_config.id,
            probes_fired=self._probes_fired,
            filled_components=[
                k for k, v in result.star_components.items() if v is not None
            ],
        )
        # AgentTask is awaitable directly; .complete(result) resolves the
        # controller's await with the result.
        self.complete(result)
        return "Question complete. The controller will dispatch the next."
