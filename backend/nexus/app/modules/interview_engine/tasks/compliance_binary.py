"""ComplianceBinaryTask — Phase 3 task for yes/no compliance questions.

Lifecycle:
  1. Controller dispatches with watchdog_seconds=60 (per
     effective_budget_seconds_for + budget_seconds_hard_cap=60.0).
  2. AgentTask boots; LLM speaks the question briefly.
  3. Candidate answers.
  4. Branch on candidate's response shape:
     a. Clear yes/no → record_compliance_attestation(...) (terminal).
     b. Ambiguous → request_compliance_clarification() (single-shot),
        then listen, then record_compliance_attestation(...).
  5. If the recorded answer is a "no" against a hard requirement, the LLM
     pairs disqualify_knockout(reason) before/with the terminal call.
  6. record_compliance_attestation calls self.complete(result).
"""

from __future__ import annotations

from string import Template

import structlog

from livekit.agents import RunContext, function_tool

from app.ai.prompts import prompt_loader
from app.modules.interview_engine.tasks.base import (
    QuestionTask,
    TaskResult,
)


log = structlog.get_logger("interview-engine.tasks.compliance_binary")


_PROMPT_NAME = "interview/task_compliance_binary"


class ComplianceBinaryTask(QuestionTask):
    """Per-question task for yes/no compliance attestation.

    Tools:
      * record_compliance_attestation — terminal observation
      * request_compliance_clarification — single-shot, doesn't count as probe
      * (inherited) disqualify_knockout, request_clarification

    Per-task hard cap: 60 seconds. The factory's effective_budget_seconds_for
    consumes `budget_seconds_hard_cap` to cap the controller's watchdog.
    """

    kind = "compliance_binary"
    max_probes = 0
    budget_seconds_hard_cap: float = 60.0

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
        self._clarification_used: bool = False

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
    async def record_compliance_attestation(
        self,
        ctx: RunContext,
        confirmed: bool,
        reason_or_example: str,
    ) -> str:
        """Terminal observation — record the candidate's yes/no with brief context.

        Builds a TaskResult carrying compliance_confirmed,
        compliance_reason_or_example, compliance_clarification_used, plus any
        knockout state the LLM set via disqualify_knockout. Calls
        self.complete(result) to resolve the controller's `await task`.
        """
        result = TaskResult(
            question_id=self.question_config.id,
            kind="compliance_binary",
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=False,
            probes_fired=0,
            compliance_confirmed=confirmed,
            compliance_reason_or_example=reason_or_example,
            compliance_clarification_used=self._clarification_used,
        )
        log.info(
            "task.compliance.recorded",
            question_id=self.question_config.id,
            confirmed=confirmed,
            knockout=result.knockout,
            clarification_used=self._clarification_used,
        )
        # AgentTask is awaitable directly; .complete(result) resolves it.
        self.complete(result)
        return "Question complete. The controller will dispatch the next."

    @function_tool()
    async def request_compliance_clarification(
        self,
        ctx: RunContext,
    ) -> str:
        """Single-shot clarification turn for ambiguous yes/no answers.

        First call: sets _clarification_used=True and returns the
        ask-once instruction. Second call: returns the "already clarified"
        instruction. Single-shot is enforced in code regardless of
        prompt compliance.
        """
        if self._clarification_used:
            log.info(
                "task.compliance.clarification_blocked_second_call",
                question_id=self.question_config.id,
            )
            return (
                "Already clarified once. Record record_compliance_attestation now — "
                "if still ambiguous, set confirmed=False with reason "
                "'ambiguous response, did not confirm'."
            )

        self._clarification_used = True
        log.info(
            "task.compliance.clarification_fired",
            question_id=self.question_config.id,
        )
        return (
            "Ask once, plainly: 'To confirm — yes or no?' Then listen to the "
            "candidate's reply and call record_compliance_attestation with the result."
        )
