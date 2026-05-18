"""Pure functions computing post-LLM naturalness flags.

Each function is side-effect-free and reads PersonaSpec / per-kind
config as needed. Used by the orchestrator after a successful Speaker
turn to populate SpeakerOutputPayload.naturalness_flags.
"""
from __future__ import annotations

from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA


def detect_repeated_opener(
    output: str, recent_reply_starts: list[str],
) -> bool:
    """True iff the first 3 words of `output` match the first 3 words
    of any recent opener.

    Anti-repetition signal. Per LiveKit/Vapi: the highest-frequency
    'sounds AI' tell across turns is the same opener appearing on
    consecutive turns. 3-word slug comparison (was 4-word) so that
    'Mm, OK — an' and 'Mm, OK — iPaaS' both register as the same
    opener — the candidate hears "Mm, OK —" repeat regardless of
    the divergent 4th word.
    """
    if not output or not recent_reply_starts:
        return False

    def _slug(text: str) -> str:
        return " ".join(text.strip().split()[:3]).lower()

    output_slug = _slug(output)
    return any(
        output_slug == _slug(start)
        for start in recent_reply_starts if start.strip()
    )


def detect_banned_phrases(output: str) -> list[str]:
    """Return PersonaSpec.vocab_banned entries found in output.

    Case-insensitive substring match. Empty list = clean. Non-empty
    list = the model emitted at least one phrase that the prompt
    explicitly forbids — a direct rule violation.
    """
    if not output:
        return []
    lower = output.lower()
    return [
        phrase for phrase in DEFAULT_PERSONA.vocab_banned
        if phrase.lower() in lower
    ]


def detect_name_overuse(
    output: str,
    candidate_name: str | None,
    prior_output: str | None,
) -> bool:
    """True iff candidate_name appears in this output AND the previous one.

    Detects salesy name-stacking ('Punar, the question is yours, Punar').
    PersonaSpec.name_usage_policy: at most once every 4-5 turns,
    never consecutive.
    """
    if not candidate_name or not output or not prior_output:
        return False
    name_lower = candidate_name.lower()
    return name_lower in output.lower() and name_lower in prior_output.lower()


# Per-kind soft target ceiling (words). Output exceeding this by >50%
# triggers the exceeded_soft_target flag. Values mirror the per-action
# body 'Soft target' lines.
_SOFT_TARGETS: dict[str, int] = {
    "deliver_first_question": 22,
    "deliver_question": 25,
    "deliver_probe": 18,
    "clarify": 35,
    "push_back": 18,
    "redirect": 18,
    "acknowledge_no_experience": 12,
    "polite_close": 20,
    "repeat": 99999,  # verbatim replay — never flag
}


def detect_exceeded_soft_target(
    output: str, instruction_kind: str,
) -> bool:
    """True iff `output` exceeds the per-kind soft target by >50%.

    Soft caps are not hard caps. Flag fires when the model is rambling
    (1.5x the target), not for normal overrun. Per user feedback:
    hard-capping produces choppy speech.
    """
    target = _SOFT_TARGETS.get(instruction_kind)
    if not target or not output:
        return False
    return len(output.split()) > int(target * 1.5)
