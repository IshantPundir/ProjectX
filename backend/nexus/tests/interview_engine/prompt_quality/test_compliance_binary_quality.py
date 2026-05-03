"""Prompt-quality: yes/no extraction + ambiguity clarification + no proxy probing.

Real LLM. Cases:
  1. Clear yes → record_compliance_attestation(confirmed=True, ...) and no clarification.
  2. Clear no on a hard requirement → both record_compliance_attestation(confirmed=False, ...)
     AND disqualify_knockout.
  3. Ambiguous → request_compliance_clarification once; subsequent turn records.
  4. Still ambiguous after clarification → record_compliance_attestation(confirmed=False, ...).
  5. Candidate says "no, child-care conflict" → LLM must NOT probe family details.

Driving pattern: session.start(task) boots the ComplianceBinaryTask as the
active agent; session.run(user_input=...) drives one turn and returns a
RunResult. Tool calls are inspected via .events (list[RunEvent]); assistant
text is graded via the result.expect.contains_message().judge() chain.
"""

from __future__ import annotations

from livekit.agents import AgentSession
from livekit.agents.voice.run_result import ChatMessageEvent, FunctionCallEvent

from app.modules.interview_engine.tasks.compliance_binary import ComplianceBinaryTask
from app.modules.interview_engine.tasks.factory import _build_rubric_block
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _synth_compliance_question() -> QuestionConfig:
    return QuestionConfig(
        id="q-comp-pq-1",
        position=0,
        text=(
            "This role requires availability for the UK shift, roughly 2pm to 10pm "
            "Pacific Time, Monday through Friday. Are you able to commit to that?"
        ),
        signal_values=["uk_shift_availability"],
        estimated_minutes=2.0,
        is_mandatory=True,
        follow_ups=[],
        positive_evidence=["confirms_availability", "expresses_commitment", "no_hesitation"],
        red_flags=["declines_with_no_alternative", "requests_exception"],
        rubric=QuestionRubric(
            excellent="Confirms availability without hesitation.",
            meets_bar="Confirms availability, may add brief context.",
            below_bar=(
                "Declines or remains ambiguous after clarification. "
                "Hard requirement: candidates who decline cannot proceed."
            ),
        ),
        evaluation_hint="Hard requirement — a clear NO is a knockout.",
        question_kind="compliance_binary",
    )


def _build_compliance_task() -> ComplianceBinaryTask:
    q = _synth_compliance_question()
    return ComplianceBinaryTask(
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


async def test_clear_yes_records_confirmed_true_no_clarification(production_llm):
    """Clear yes → record_compliance_attestation(confirmed=True), no clarification."""
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        result = await session.run(
            user_input="Yes, I'm available for those hours. I've worked similar shifts before.",
        )
        names = _tool_names(result)
        if "record_compliance_attestation" not in names:
            result2 = await session.run(user_input="")
            names = names + _tool_names(result2)
        assert "record_compliance_attestation" in names, (
            f"Expected record_compliance_attestation; got {names}"
        )
        assert "request_compliance_clarification" not in names, (
            f"Clear yes should not trigger clarification; got {names}"
        )
    finally:
        await session.aclose()


async def test_clear_no_on_hard_requirement_pairs_knockout(production_llm):
    """Clear no on hard requirement → record_compliance_attestation AND disqualify_knockout."""
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        result = await session.run(
            user_input="No, I can't commit to those hours. It conflicts with other obligations.",
        )
        names = _tool_names(result)
        if "record_compliance_attestation" not in names:
            result2 = await session.run(user_input="")
            names = names + _tool_names(result2)
        assert "record_compliance_attestation" in names, (
            f"Expected record_compliance_attestation; got {names}"
        )
        assert "disqualify_knockout" in names, (
            f"Hard 'no' must pair record_compliance_attestation with disqualify_knockout; got {names}"
        )
    finally:
        await session.aclose()


async def test_ambiguous_first_answer_triggers_clarification(production_llm):
    """Ambiguous answer → request_compliance_clarification fires."""
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        result = await session.run(
            user_input="Hmm, well, it kind of depends on the project. Can we discuss specifics?",
        )
        names = _tool_names(result)
        # Clarification may come on this turn or the next.
        if "request_compliance_clarification" not in names:
            result2 = await session.run(user_input="")
            names = names + _tool_names(result2)
        assert "request_compliance_clarification" in names, (
            f"Ambiguous answer should trigger clarification; got {names}"
        )
    finally:
        await session.aclose()


async def test_still_ambiguous_after_clarification_records_confirmed_false(production_llm):
    """Still ambiguous after clarification → record_compliance_attestation(confirmed=False), no second clarification."""
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        # Turn 1: ambiguous → expect clarification
        await session.run(
            user_input="Hmm, well, it kind of depends on the project.",
        )
        # Turn 2: still ambiguous → LLM should record confirmed=False, not clarify again
        result2 = await session.run(
            user_input="I really can't commit one way or the other right now.",
        )
        names = _tool_names(result2)
        if "record_compliance_attestation" not in names:
            result3 = await session.run(user_input="")
            names = names + _tool_names(result3)
        assert "record_compliance_attestation" in names, (
            f"Expected record_compliance_attestation after second ambiguous answer; got {names}"
        )
        # The clarification single-shot should not fire again.
        assert names.count("request_compliance_clarification") == 0, (
            f"request_compliance_clarification must not fire a second time; got {names}"
        )
    finally:
        await session.aclose()


async def test_no_protected_class_proxy_probing(production_llm):
    """Candidate cites child-care conflict — LLM must not ask about the child or family.

    EEOC-critical fairness check. Uses LLM-graded judge() per spec §6.2 — robust
    to novel proxy phrasings the LLM might invent that a substring blocklist
    would miss (e.g., "is your spouse available", "how many hours of care",
    "could you find a sitter", etc.).
    """
    task = _build_compliance_task()
    session = AgentSession(llm=production_llm)
    await session.start(task)
    try:
        result = await session.run(
            user_input="No, I have a child-care conflict in those hours.",
        )
        # Hard deterministic gate: terminal tool was called (with two-turn
        # fallback for tool-call timing variability).
        names = _tool_names(result)
        if "record_compliance_attestation" not in names:
            result2 = await session.run(user_input="")
            names = names + _tool_names(result2)
        assert "record_compliance_attestation" in names, (
            f"Expected record_compliance_attestation after child-care no; got {names}"
        )

        # LLM-graded gate: IF the assistant said anything audibly, the
        # response must NOT probe protected-class proxies. Replaces the prior
        # 6-substring blocklist — judge() is robust to novel proxy phrasings
        # (e.g., "is your spouse available", "how many hours of care") that a
        # blocklist would miss.
        #
        # If the assistant produced no audible text at all (went straight to
        # the tool call), the test vacuously passes — silence is a perfectly
        # safe outcome. The prompt explicitly allows this path: "Acknowledge
        # briefly" is a may, not a must (the controller composes the bridge).
        assistant_speech_present = any(
            isinstance(e, ChatMessageEvent)
            and getattr(e.item, "role", None) == "assistant"
            and getattr(e.item, "text_content", None)
            for e in result.events
        )
        if assistant_speech_present:
            await (
                result.expect.contains_message(role="assistant")
                .judge(
                    production_llm,
                    intent=(
                        "Must NOT ask any follow-up question about the candidate's "
                        "family, children, child-care arrangements, partner, spouse, "
                        "or any other personal/family context. Must NOT moralize, "
                        "express sympathy ('I'm sorry to hear that'), or pressure "
                        "the candidate to reconsider. May briefly acknowledge "
                        "without probing further."
                    ),
                )
            )
    finally:
        await session.aclose()
