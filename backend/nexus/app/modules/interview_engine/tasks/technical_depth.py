"""TechnicalDepthTask — Phase 2's only concrete task subclass.

All Phase 2 questions route here. Phase 3 adds BehavioralStarTask and
ComplianceBinaryTask + question_kind-based routing.

Tools:
  * record_answer_assessment — observation; returns probes-remaining instr
  * request_probe — fires the follow-up; bumps probe counter
  * complete_question — terminal; resolves await Task().run()
  * (inherited) disqualify_knockout, request_clarification
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


log = structlog.get_logger("interview-engine.tasks.technical_depth")


_PROMPT_NAME = "interview/task_technical_depth"


class TechnicalDepthTask(QuestionTask):
    """Per-question task for technical-depth questions.

    Lifecycle:
      1. Controller dispatches: `await asyncio.wait_for(task.run(), timeout=...)`
      2. AgentTask boots; the LLM reads task instructions + chat ctx and
         speaks an in-flow ≤25-word phrasing of the question.
      3. Candidate answers.
      4. LLM calls record_answer_assessment with tier/evidence_keys/non_answer/signals_lacked.
      5. The tool returns "probes remaining: N" so the LLM can decide.
      6. LLM either calls request_probe (and re-listens) or complete_question.
      7. Terminal tool resolves run() with a TaskResult.
    """

    kind = "technical_depth"
    max_probes = 1

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
    async def record_answer_assessment(
        self,
        ctx: RunContext,
        tier: Literal["excellent", "strong", "at_bar", "below_bar"],
        evidence_keys: list[str],
        non_answer: bool,
        signals_lacked: list[str],
    ) -> str:
        """Record your assessment of the candidate's answer.

        Args:
            tier: how well the answer met the rubric.
            evidence_keys: short descriptors of what they showed (drawn
                from the rubric's positive_evidence list).
            non_answer: True iff the candidate said "I don't know" or
                similar with no substance to probe.
            signals_lacked: signal values from this question's signal_values
                that the candidate explicitly disclaimed (e.g. "I have no
                experience with X"). The controller propagates these so
                later questions probing the same signal are skipped.

        Returns:
            Instruction telling you how many probes remain. Use that to
            decide between request_probe and complete_question.
        """
        self._record_partial_assessment(
            tier=tier,
            evidence_keys=evidence_keys,
            signals_lacked=signals_lacked,
            non_answer=non_answer,
        )
        log.info(
            "task.observation.recorded",
            question_id=self.question_config.id,
            tier=tier,
            evidence_keys=evidence_keys,
            non_answer=non_answer,
            signals_lacked=signals_lacked,
            probes_fired=self._probes_fired,
        )
        probes_left = self.max_probes - self._probes_fired
        if non_answer:
            return (
                "Non-answer recorded. Do not probe — call complete_question now."
            )
        if probes_left <= 0:
            return (
                "Observation recorded. No probes remaining — call complete_question."
            )
        if tier == "below_bar":
            return (
                f"Observation recorded. {probes_left} probe(s) remaining. "
                "If a follow-up would surface evidence, call request_probe; "
                "otherwise call complete_question."
            )
        return (
            "Observation recorded. Answer is at-bar or above — "
            "call complete_question to move on."
        )

    @function_tool()
    async def request_probe(self, ctx: RunContext) -> str:
        """Fire a follow-up probe. Use only when below_bar AND probes remain."""
        if self._probes_fired >= self.max_probes:
            return (
                "Probe budget exhausted. Call complete_question instead."
            )
        self._probes_fired += 1
        self._partial.probes_fired = self._probes_fired
        log.info(
            "task.probe.fired",
            question_id=self.question_config.id,
            probe_number=self._probes_fired,
        )
        return (
            "Ask a single concise follow-up that targets the missing evidence. "
            "After their reply, call record_answer_assessment again."
        )

    @function_tool()
    async def complete_question(self, ctx: RunContext) -> str:
        """Terminal tool — ends this question's task.

        Builds a TaskResult from recorded state and resolves the
        outer await Task().run() in the controller.
        """
        result = TaskResult(
            question_id=self.question_config.id,
            kind="technical_depth",
            tier=self._partial.tier,
            evidence_keys=list(self._partial.evidence_keys),
            non_answer=self._partial.non_answer,
            signals_lacked=list(self._partial.signals_lacked),
            knockout=self._partial.knockout,
            knockout_reason=self._partial.knockout_reason,
            forced=False,
            probes_fired=self._probes_fired,
        )
        log.info(
            "task.completed",
            question_id=self.question_config.id,
            tier=result.tier,
            forced=False,
            probes_fired=self._probes_fired,
        )
        # `complete()` is the LiveKit AgentTask method that resolves
        # await self.run() with the value. Subclasses don't override run().
        self.complete(result)
        return "Question complete. The controller will dispatch the next."
