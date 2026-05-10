"""OpenerLibrary — curated vocabulary + selection logic.

See docs/superpowers/specs/2026-05-10-opener-prefetch-architecture-design.md
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit import rtc

from app.modules.interview_engine.models.speaker import InstructionKind


class SubContext(StrEnum):
    """Discriminator for opener vocabulary lookup within an InstructionKind.

    Maps to ``turn_metadata`` flags + reason_codes + is_post_cap_advance
    on SpeakerInput.
    """
    DEFAULT = "default"
    POST_CAP_ADVANCE = "post_cap_advance"
    SOCIAL_OR_GREETING = "social_or_greeting"
    OFF_TOPIC = "off_topic"
    ABUSIVE = "abusive"
    INJECTION = "injection"
    VAGUE_ANSWER = "vague_answer"
    DEFLECTION = "deflection"
    MISSING_SPECIFICS = "missing_specifics"
    UNANSWERED_SUBQUESTION = "unanswered_subquestion"
    KNOCKOUT = "knockout"


@dataclass
class OpenerVariant:
    """One opener phrase + its pre-synthesized audio.

    ``audio_frames`` is populated by ``build_opener_cache`` at engine
    startup. None means the cache wasn't built (or this variant failed
    synthesis); the orchestrator falls back to text-only TTS for those.
    """
    text: str
    audio_frames: list[rtc.AudioFrame] | None = None


@dataclass(frozen=True)
class OpenerSelection:
    """The chosen opener for one orchestrator turn.

    ``text`` is None when this turn has no opener (e.g., clean
    polite_close, deliver_first_question). ``audio_iter`` is None
    when the audio cache is unavailable for the picked variant —
    the orchestrator falls back to ``session.say(text=...)``.
    """
    text: str | None
    audio_iter: Callable[[], AsyncIterator[rtc.AudioFrame]] | None


# ---------------------------------------------------------------------------
# Curated vocabulary per spec §5.
#
# Register guidance (load-bearing):
#   * Senior interviewer voice — brief, neutral, slightly clinical.
#   * NEVER customer-service phrases ("Happy to", "Of course!", "No problem.").
#   * NEVER evaluative phrases ("Great!", "Perfect!", "Good answer!").
#   * Variety per slot so a 20-turn session doesn't feel scripted.
# ---------------------------------------------------------------------------
_VOCABULARY: dict[tuple[InstructionKind, SubContext], list[str]] = {
    # ----- deliver_question -----
    (InstructionKind.deliver_question, SubContext.DEFAULT): [
        "Got it.",
        "Understood.",
        "Right.",
        "OK.",
        "Mhm.",
        "Thanks for walking me through that.",
        "Thanks.",
    ],
    (InstructionKind.deliver_question, SubContext.POST_CAP_ADVANCE): [
        "OK, let's switch gears.",
        "Alright, moving on.",
        "Let's try a different angle.",
        "On a different note —",
        "Setting that aside for now —",
    ],

    # ----- deliver_probe -----
    (InstructionKind.deliver_probe, SubContext.DEFAULT): [
        "Got it. And —",
        "Right. And —",
        "OK. And —",
        "Mhm. And —",
        "OK, on that —",
        "Building on that —",
    ],

    # ----- push_back -----
    (InstructionKind.push_back, SubContext.VAGUE_ANSWER): [
        "Got it.",
        "OK.",
        "Right —",
        "Mhm —",
        "Hmm —",
        "OK, let me press on that —",
    ],
    (InstructionKind.push_back, SubContext.DEFLECTION): [
        "Fair.",
        "Fair enough.",
        "Understood.",
        "Got it.",
        "OK.",
    ],
    (InstructionKind.push_back, SubContext.MISSING_SPECIFICS): [
        "Right —",
        "OK —",
        "Got it —",
        "Mhm —",
    ],
    (InstructionKind.push_back, SubContext.UNANSWERED_SUBQUESTION): [
        "OK on that —",
        "Got the first part —",
        "Right —",
    ],

    # ----- clarify -----
    (InstructionKind.clarify, SubContext.DEFAULT): [
        "OK, let me put it differently.",
        "Let me reframe that.",
        "Different way to ask that —",
        "Let me give you a more concrete example.",
        "Hmm, OK — let me reword that.",
        "Let me try a different angle.",
        "Think of it this way —",
    ],

    # ----- redirect -----
    (InstructionKind.redirect, SubContext.SOCIAL_OR_GREETING): [
        "Hey there.",
        "Hi there.",
        "Hello.",
        "Good to meet you.",
        "Likewise.",
        "Doing fine.",
    ],
    (InstructionKind.redirect, SubContext.OFF_TOPIC): [
        "Got it.",
        "OK.",
        "Right, but —",
        "Hmm —",
        "Noted.",
    ],
    (InstructionKind.redirect, SubContext.ABUSIVE): [
        "Alright.",
        "OK.",
        "Let's keep this professional —",
        "Hmm.",
    ],
    (InstructionKind.redirect, SubContext.INJECTION): [
        "OK.",
        "Right —",
        "Let's stay focused —",
        "Back to the interview —",
    ],

    # ----- acknowledge_no_experience -----
    (InstructionKind.acknowledge_no_experience, SubContext.DEFAULT): [
        "Got it.",
        "Thanks for being upfront.",
        "Appreciate the honesty.",
        "Understood.",
        "Fair enough.",
        "OK, that's helpful to know.",
    ],

    # ----- polite_close -----
    (InstructionKind.polite_close, SubContext.DEFAULT): [
        "Alright.",
        "OK.",
    ],
    (InstructionKind.polite_close, SubContext.KNOCKOUT): [
        "Thanks for being upfront.",
        "Appreciate the honesty.",
        "Got it.",
    ],

    # ----- repeat -----
    (InstructionKind.repeat, SubContext.DEFAULT): [
        "OK.",
        "Sure.",
        "Right.",
    ],
}


class OpenerLibrary:
    """Curated vocabulary + selection logic for opener prefetch.

    Variants are mutable dataclasses — ``build_opener_cache`` populates
    each variant's ``audio_frames`` field in place at engine startup.
    """

    def __init__(self) -> None:
        self._vocabulary: dict[tuple[InstructionKind, SubContext], list[OpenerVariant]] = {
            key: [OpenerVariant(text=text) for text in texts]
            for key, texts in _VOCABULARY.items()
        }

    def _variants_for(
        self,
        kind: InstructionKind,
        sub_context: SubContext,
    ) -> list[OpenerVariant]:
        """Look up variants for a (kind, sub_context) pair.

        Falls back to (kind, DEFAULT) if the specific sub_context is
        absent. Returns an empty list only if the kind itself has no
        DEFAULT entry — caller handles the empty case.
        """
        variants = self._vocabulary.get((kind, sub_context))
        if variants is not None:
            return variants
        return self._vocabulary.get((kind, SubContext.DEFAULT), [])

    def pick(
        self,
        *,
        kind: InstructionKind,
        sub_context: SubContext,
        recent_openers: Iterable[str],
    ) -> OpenerSelection:
        """Pick one opener for this turn.

        Selection rule:
          1. Look up variants for (kind, sub_context). Fall back to
             (kind, DEFAULT) if no variants.
          2. Filter out any variant whose text is in recent_openers.
          3. If filtering empties the pool, return the longest-ago
             recent entry (first in iteration order — still better
             than re-using the most recent).
          4. Otherwise pick the FIRST eligible variant.

        Why first instead of random? Deterministic + audit-friendly.
        Anti-repetition (excluding recent) gives plenty of variety;
        the deque-of-5 ensures we cycle through the full pool before
        any single variant repeats. Random selection on top of that
        would obscure the rotation pattern in audit traces without
        meaningfully improving naturalness.
        """
        variants = self._variants_for(kind, sub_context)
        if not variants:
            # No variants registered (e.g., deliver_first_question) —
            # signal "no opener for this turn".
            return OpenerSelection(text=None, audio_iter=None)

        recent_set = set(recent_openers)
        eligible = [v for v in variants if v.text not in recent_set]

        if eligible:
            chosen = eligible[0]
        else:
            # All variants used recently — use the longest-ago entry
            # (first item in recent_openers).
            recent_list = list(recent_openers)
            target_text = recent_list[0] if recent_list else variants[0].text
            chosen = next(
                (v for v in variants if v.text == target_text),
                variants[0],
            )

        audio_iter: Callable[[], AsyncIterator[rtc.AudioFrame]] | None = None
        if chosen.audio_frames is not None:
            frames = chosen.audio_frames

            async def _replay() -> AsyncIterator[rtc.AudioFrame]:
                for frame in frames:
                    yield frame

            audio_iter = _replay

        return OpenerSelection(text=chosen.text, audio_iter=audio_iter)
