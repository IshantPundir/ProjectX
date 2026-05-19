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
    """True iff the first 2 words of ``output`` match the first 2 words
    of any recent agent reply.

    Anti-repetition signal. Per OpenAI Realtime / LiveKit / Vapi, the
    highest-frequency "sounds AI" tell across turns is the same opener
    appearing on consecutive turns. The post-hoc flag drives the
    `naturalness_flags.repeated_opener` observability bit so we can
    measure how often the model violates the Variety RULE in
    _preamble.txt.

    **Why 2 words, not 3?** The retired ``opener_slug`` helper used a
    3-word lowercased slug, which silently failed for short openers
    like "See —" (2 words). The actual heard-by-the-candidate repetition
    is the FIRST 1-2 words. Comparing 2 lowercased words gives a
    detector that catches "See —" → "See —" (both slug "see —")
    AND "Mm, OK —" → "Mm, OK —" (both slug "mm, ok") while still
    allowing genuine variation. The 2-word check matches the Variety
    RULE the Speaker prompt enforces.
    """
    if not output or not recent_reply_starts:
        return False

    def _slug(text: str) -> str:
        return " ".join(text.strip().split()[:2]).lower()

    output_slug = _slug(output)
    if not output_slug:
        return False
    return any(
        _slug(start) == output_slug
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
# triggers the exceeded_soft_target flag. Keys are
# (instruction_kind, clarify_kind | None) — the second element is None
# for non-clarify kinds and for the legacy clarify fallback when the
# Judge hasn't supplied a clarify_kind (back-compat with pre-intent-layer
# audit envelopes).
_SOFT_TARGETS: dict[tuple[str, str | None], int] = {
    # clarify: per-kind targets reflect the speaker shape required.
    # concept_explanation needs space for a failure scenario; term is
    # one clause + re-ask; probe is short.
    ("clarify", "term_definition"):     25,
    ("clarify", "concept_explanation"): 50,
    ("clarify", "use_case_anchor"):     40,
    ("clarify", "broad_rephrase"):      35,
    ("clarify", "probe_context"):       25,
    ("clarify", None):                  35,  # legacy fallback
    # Non-clarify kinds keep their existing targets unchanged.
    ("deliver_first_question", None):     22,
    ("deliver_question", None):           25,
    ("deliver_probe", None):              18,
    ("push_back", None):                  18,
    ("redirect", None):                   18,
    ("acknowledge_no_experience", None):  12,
    ("polite_close", None):               20,
    ("repeat", None):                     99999,
}


def detect_exceeded_soft_target(
    output: str,
    instruction_kind: str,
    clarify_kind: str | None = None,
) -> bool:
    """True iff ``output`` exceeds the per-kind soft target by >50%.

    Soft caps are not hard caps. The flag fires when the model is
    rambling (1.5x the target), not for normal overrun. Per user
    feedback: hard-capping produces choppy speech.

    Lookup is two-step: first ``(instruction_kind, clarify_kind)``, then
    fallback to ``(instruction_kind, None)``. ``clarify_kind`` is only
    meaningful when ``instruction_kind == 'clarify'`` — for every other
    kind, the second-element-None entry is the target.
    """
    target = (
        _SOFT_TARGETS.get((instruction_kind, clarify_kind))
        or _SOFT_TARGETS.get((instruction_kind, None))
    )
    if not target or not output:
        return False
    return len(output.split()) > int(target * 1.5)


# ---------------- detect_solution_leak ----------------


# Last-sentence leak indicators for PATH E (concept_explanation). When
# the Speaker output's last sentence contains one of these verbs (with
# trailing space to avoid sub-word matches), the model has crossed the
# anti-leak boundary by proposing the fix rather than handing the
# question back. Informational — does not block emission; surfaces in
# the audit envelope so we can grep across sessions.
_LEAK_INDICATOR_VERBS: tuple[str, ...] = (
    "use ", "implement ", "add a ", "store a ", "key on ",
    "track ", "deduplicate by ",
)


def detect_solution_leak(
    output: str,
    clarify_kind: str | None,
) -> bool:
    """True iff PATH E (concept_explanation) output's last sentence
    contains a leak-indicator verb.

    Anti-leak signal specific to concept_explanation: the Speaker is
    supposed to describe ONE failure scenario and hand the question
    back, NOT propose the fix. A last sentence containing "use",
    "implement", "add a", etc. almost always means the model proposed
    the solution.

    Returns False for any other clarify_kind (the signal is meaningless
    outside concept_explanation) and for empty output.
    """
    if clarify_kind != "concept_explanation" or not output:
        return False
    # Take everything after the last period; strip trailing punctuation
    # so a final "." or "?" doesn't interfere with substring matching.
    last_sentence = output.rstrip(".!?").rsplit(".", 1)[-1].lower()
    return any(verb in last_sentence for verb in _LEAK_INDICATOR_VERBS)
