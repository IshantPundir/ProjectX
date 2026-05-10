"""OpenerLibrary — curated vocabulary + selection logic.

See docs/superpowers/specs/2026-05-10-opener-prefetch-architecture-design.md
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit import rtc


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
