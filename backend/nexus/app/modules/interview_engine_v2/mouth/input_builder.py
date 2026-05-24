"""Pure per-act message assembly for the mouth (no livekit, no IO, no LLM).

Produces the bounded, cache-stable message list the mouth's `llm_node` sends every turn
(DESIGN-SPEC §11): a STABLE PREFIX (persona preamble, byte-identical across the session) ->
a per-act block (stable per act) -> a DYNAMIC SUFFIX (the directive payload + the fenced
candidate utterance). The accumulated chat history is deliberately NOT included — the mouth
voices the current directive, not the whole transcript (keeps the dynamic part bounded and
keeps candidate speech spotlighted as DATA). REPEAT replays the cached last question.
"""

from __future__ import annotations

from app.modules.interview_engine_v2.directive import Directive, DirectiveAct

# Acts whose `say` is the active question the mouth should later be able to REPEAT.
_QUESTION_BEARING: frozenset[DirectiveAct] = frozenset({
    DirectiveAct.ASK, DirectiveAct.PROBE, DirectiveAct.ACK_ADVANCE,
    DirectiveAct.CLARIFY, DirectiveAct.REDIRECT,
})


def effective_say(directive: Directive, *, last_question: str | None) -> str | None:
    """The text the mouth should deliver. REPEAT replays the cached last question."""
    if directive.act is DirectiveAct.REPEAT:
        return last_question or "(no previous question to repeat)"
    return directive.say


def is_question_bearing(act: DirectiveAct) -> bool:
    """True if delivering this act updates 'the question currently on the floor' (for REPEAT)."""
    return act in _QUESTION_BEARING


def build_mouth_messages(
    *,
    directive: Directive,
    persona_preamble: str,
    act_block: str,
    candidate_utterance: str | None,
    last_question: str | None,
    just_said_filler: str | None = None,
) -> list[dict[str, str]]:
    """Assemble the [persona | act | dynamic-suffix] message list for one mouth turn."""
    # For REPEAT, directive.say is None by convention; effective_say substitutes last_question.
    # The rendered `say:` field always receives the resolved text (so repeat.txt's "provided in
    # `say`" is accurate from the LLM's view, even though directive.say itself is None).
    say = effective_say(directive, last_question=last_question)

    lines: list[str] = []
    if candidate_utterance and candidate_utterance.strip():
        # Spotlight candidate speech as DATA, never instructions (identity lock backs this).
        lines.append(f"CANDIDATE SAID: «{candidate_utterance.strip()}»")
        lines.append("")
    if just_said_filler and just_said_filler.strip():
        lines.append(f"YOU JUST SAID: «{just_said_filler.strip()}»")
        lines.append(
            "You ALREADY opened this turn with that line. Continue from that in the same breath: "
            "do NOT begin with a fresh generic opener ('okay', 'so', 'got it', 'alright', 'now') "
            "and do NOT just state the question cold. Add a SHORT connective that picks up the "
            "thread of what you just said (e.g. 'and on that —', 'so for those —'), then ask the "
            "line below, keeping its meaning and every specific term intact. Don't repeat the "
            "filler's exact words.")
        lines.append("")
    lines.append("DELIVER THIS NOW:")
    lines.append(f"  intent: {directive.act.value}")
    lines.append(f"  tone: {directive.tone.value}")
    lines.append(f"  say: {say if say is not None else '(compose per the guidance above)'}")
    lines.append(f"  style note: {directive.compose_hint or '(none)'}")

    return [
        {"role": "system", "content": persona_preamble},   # stable cache prefix
        {"role": "system", "content": act_block},           # stable per act
        {"role": "user", "content": "\n".join(lines)},      # dynamic suffix (bounded)
    ]
