"""Prompt-quality: STAR component detection on behavioral_star questions.

Real LLM. Cases:
  1. Candidate covers Situation+Task only → expects record_behavioral_answer
     with action=null, result=null, AND request_star_probe(action|result).
  2. Candidate covers all four components → expects no probe, complete_question.
  3. Non-answer ("I don't have an example") → expects no probe, complete_question.

Driving pattern: session.start(task) boots the BehavioralStarTask as the
active agent; session.run(user_input=...) drives one turn and returns a
RunResult whose .events list carries FunctionCallEvent items.
"""

from __future__ import annotations

from livekit.agents import AgentSession
from livekit.agents.voice.run_result import FunctionCallEvent

from app.modules.interview_engine.tasks.behavioral import BehavioralStarTask
from app.modules.interview_engine.tasks.factory import _build_rubric_block
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _synth_behavioral_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-bhv-pq-1",
        position=0,
        text="Tell me about a time you led a team through a tight deadline.",
        signal_values=["leadership"],
        estimated_minutes=4.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["delegation", "communication", "outcome"],
        red_flags=["solo_hero", "blame_team"],
        rubric=QuestionRubric(
            excellent="Specific, measurable outcomes; clear delegation and communication.",
            meets_bar="Coherent story with clear role and outcome, even if outcome modest.",
            below_bar="Vague generalities; no concrete actions or outcome.",
        ),
        evaluation_hint="Look for STAR coverage: situation, task, action, result.",
        question_kind="behavioral_star",
    )


def _build_behavioral_task() -> BehavioralStarTask:
    q = _synth_behavioral_question()
    return BehavioralStarTask(
        question_config=q,
        controller=None,  # type: ignore[arg-type]
        disqualified_signals=frozenset(),
        rubric_internal=_build_rubric_block(q),
    )


def _tool_names(result) -> list[str]:
    """Extract function call names from a RunResult's events list."""
    return [
        e.item.name
        for e in result.events
        if isinstance(e, FunctionCallEvent)
    ]


async def test_partial_coverage_triggers_probe(production_llm):
    """Candidate covers Situation+Task only — LLM should fire request_star_probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        candidate_utterance = (
            "Sure — last quarter at my prior job, I was the team lead on a "
            "two-week migration project. We had a hard deadline before a customer demo."
        )
        # Drive one turn: candidate utterance, agent responds with tool calls.
        result = await session.run(user_input=candidate_utterance)

        tool_names = _tool_names(result)
        assert "record_behavioral_answer" in tool_names, (
            f"Expected record_behavioral_answer in tool calls; got {tool_names}"
        )
        # Then either request_star_probe or follow-up text — we accept the probe
        # firing on this turn or on the next agent turn.
        probed = "request_star_probe" in tool_names
        if not probed:
            # The probe may come in the next agent message after the tool-result
            # roundtrip. Drive one more turn and check.
            result2 = await session.run(user_input="")
            tool_names2 = _tool_names(result2)
            probed = "request_star_probe" in tool_names2
        assert probed, (
            f"Expected request_star_probe to fire on partial STAR coverage. "
            f"Turn 1 tools: {_tool_names(result)}"
        )
    finally:
        await session.aclose()


async def test_complete_coverage_skips_probe(production_llm):
    """Candidate covers all four STAR components — LLM should call complete_question, no probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        candidate_utterance = (
            "Last quarter at my prior job, I was the team lead for a two-week migration "
            "before a customer demo. I broke the work into four parallel tracks, paired "
            "the two strongest engineers on the riskiest piece, and ran a daily 15-minute "
            "standup to surface blockers fast. We shipped two days ahead of the deadline "
            "and the customer demo went smoothly."
        )
        result = await session.run(user_input=candidate_utterance)
        tool_names = _tool_names(result)

        assert "record_behavioral_answer" in tool_names, (
            f"Expected record_behavioral_answer; got {tool_names}"
        )
        assert "request_star_probe" not in tool_names, (
            f"Did not expect a probe for complete coverage; got {tool_names}"
        )
        # complete_question may come on this turn or the next.
        completed = "complete_question" in tool_names
        if not completed:
            result2 = await session.run(user_input="")
            tool_names2 = _tool_names(result2)
            completed = "complete_question" in tool_names2
        assert completed, (
            f"Expected complete_question to fire after complete answer. "
            f"Turn 1 tools: {tool_names}"
        )
    finally:
        await session.aclose()


async def test_non_answer_skips_probe(production_llm):
    """Candidate explicitly says no example — LLM must not probe."""
    task = _build_behavioral_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        candidate_utterance = (
            "Honestly, I haven't really led a team through a tight deadline before. "
            "I don't have a good example for that."
        )
        result = await session.run(user_input=candidate_utterance)
        tool_names = _tool_names(result)

        assert "record_behavioral_answer" in tool_names, (
            f"Expected record_behavioral_answer; got {tool_names}"
        )
        assert "request_star_probe" not in tool_names, (
            f"Probing a non-answer is forbidden; got {tool_names}"
        )
    finally:
        await session.aclose()
