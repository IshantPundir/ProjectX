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
    spoken_setup: str | None = None,
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
        lines.append(f"YOU ALREADY SAID (aloud, a moment ago): «{just_said_filler.strip()}»")
        lines.append(
            "That line was THIS turn's opening acknowledgment — it is DONE and the candidate "
            "has already heard it. Do NOT acknowledge again in ANY form: do not repeat those "
            "words, and do not swap in a different opener or ack either (no 'okay', 'so', "
            "'got it', 'alright', 'right', 'sure', 'mm', 'now', 'I see', 'of course'). The act "
            "instructions above ask for a brief opening beat — that beat IS the line above, so "
            "SKIP it here and do not add another. Go STRAIGHT into the line below, optionally "
            "after ONE short connective that flows on from what you just said (e.g. 'and on "
            "that —', 'and for that one —', 'which means —'). Keep the question's meaning and "
            "every specific term intact; never turn it into a different question.")
        lines.append("")
    if spoken_setup and spoken_setup.strip():
        lines.append(f"SPOKEN SETUP: «{spoken_setup.strip()}»")
        lines.append(
            "Say this short orienting line FIRST (in your own natural spoken words), then deliver "
            "the question below. It sets the scene; do not treat it as part of the question text.")
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
