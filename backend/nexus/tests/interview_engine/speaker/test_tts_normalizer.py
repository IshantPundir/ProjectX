"""Tests for the Sarvam TTS-friendly text normalizer.

Two surfaces:
  1. ``normalize_for_tts(text)`` — pure function for plain-text paths
     (repeat replay, fallbacks). Idempotent.
  2. ``normalize_for_tts_stream(upstream)`` — async generator wrapper
     for the LLM token stream. Streaming-safe: never splits a word
     across emit boundaries.
"""
from __future__ import annotations

import pytest

from app.modules.interview_engine.speaker.tts_normalizer import (
    normalize_for_tts,
    normalize_for_tts_stream,
)


# ---------------------------------------------------------------------------
# normalize_for_tts — pure function tests
# ---------------------------------------------------------------------------


class TestContractions:
    """Expand contractions Sarvam mispronounces ('I'm' → 'im' audibly)."""

    def test_i_prefix_contractions(self) -> None:
        assert normalize_for_tts("I'm Arjun") == "I am Arjun"
        assert normalize_for_tts("I'll ask you") == "I will ask you"
        assert normalize_for_tts("I've worked on this") == "I have worked on this"
        assert normalize_for_tts("I'd say so") == "I would say so"

    def test_second_person_contractions(self) -> None:
        assert normalize_for_tts("you're correct") == "you are correct"
        assert normalize_for_tts("you'll see") == "you will see"
        assert normalize_for_tts("you've been there") == "you have been there"

    def test_we_contractions(self) -> None:
        assert normalize_for_tts("we're chatting") == "we are chatting"
        assert normalize_for_tts("we'll cover this") == "we will cover this"

    def test_negative_contractions(self) -> None:
        assert normalize_for_tts("don't worry") == "do not worry"
        assert normalize_for_tts("can't do that") == "cannot do that"
        assert normalize_for_tts("won't happen") == "will not happen"
        assert normalize_for_tts("isn't right") == "is not right"

    def test_misc_contractions(self) -> None:
        assert normalize_for_tts("it's about time") == "it is about time"
        assert normalize_for_tts("let's begin") == "let us begin"
        assert normalize_for_tts("that's the role") == "that is the role"

    def test_sentence_start_capitalization_preserved(self) -> None:
        """Capital 'You' / 'It' at sentence start stays capitalized."""
        assert normalize_for_tts("You're awesome") == "You are awesome"
        assert normalize_for_tts("It's about time") == "It is about time"
        assert normalize_for_tts("Let's begin") == "Let us begin"

    def test_possessive_s_is_NOT_expanded(self) -> None:
        """'John's resume' is a possessive, not a contraction — Sarvam
        pronounces it correctly. Leave alone."""
        # Word boundary requires "'s" be a contraction of "is/has" only —
        # in "John's resume" the 's binds to a proper noun and isn't a
        # listed contraction. We avoid blanket-stripping apostrophes
        # exactly to preserve this case.
        assert "John's resume" in normalize_for_tts("the John's resume")

    def test_idempotent(self) -> None:
        """Running normalize twice produces the same output as once."""
        once = normalize_for_tts("I'm Arjun and we're chatting")
        twice = normalize_for_tts(once)
        assert once == twice
        assert once == "I am Arjun and we are chatting"

    def test_empty_input(self) -> None:
        assert normalize_for_tts("") == ""
        assert normalize_for_tts(None) is None


class TestCamelCaseAcronyms:
    """Respell compound acronyms Sarvam fragments ('SaaS' → 'Saa S' audibly)."""

    def test_saas_family(self) -> None:
        assert normalize_for_tts("a SaaS company") == "a sass company"
        assert normalize_for_tts("an iPaaS platform") == "an i-pass platform"
        assert normalize_for_tts("PaaS or IaaS") == "pass or I-A-A-S"

    def test_devops_family(self) -> None:
        assert normalize_for_tts("DevOps team") == "dev ops team"
        assert normalize_for_tts("MLOps pipeline") == "M-L ops pipeline"
        assert normalize_for_tts("DevSecOps practice") == "dev sec ops practice"

    def test_other_compound_acronyms(self) -> None:
        assert normalize_for_tts("a NoSQL store") == "a no sequel store"
        assert normalize_for_tts("GraphQL endpoint") == "graph Q-L endpoint"

    def test_devsecops_takes_precedence_over_devops(self) -> None:
        """DevSecOps must match before DevOps (substring-safety)."""
        out = normalize_for_tts("we run DevSecOps and DevOps")
        assert "dev sec ops" in out
        assert "dev ops" in out
        # The substring "dev ops" should not appear inside "dev sec ops"
        # — they're separate matches because of \b boundaries.

    def test_case_sensitive_camelcase_only(self) -> None:
        """All-caps 'SAAS' and lowercase 'saas' fall through unchanged.

        Sarvam handles all-caps differently from camelCase; we don't
        want to clobber correct spellings.
        """
        assert normalize_for_tts("a SAAS company") == "a SAAS company"
        assert normalize_for_tts("a saas platform") == "a saas platform"


class TestCombinedNormalization:
    """Both transforms apply to the same string."""

    def test_contractions_plus_acronyms(self) -> None:
        out = normalize_for_tts(
            "I'm working on a SaaS platform, and we're building an iPaaS connector."
        )
        assert out == (
            "I am working on a sass platform, and we are building an i-pass connector."
        )

    def test_intro_brief_sample(self) -> None:
        """The actual intro_brief shape from session 5b966895."""
        out = normalize_for_tts(
            "Hey Ishant, I'm Arjun — good to meet you. So today we're "
            "chatting about the Jr. Forward Deployed Engineer role at "
            "Workato. It's about fifteen minutes, four questions — "
            "sound good?"
        )
        assert "I am Arjun" in out
        assert "we are chatting" in out
        assert "It is about" in out
        assert "I'm" not in out
        assert "we're" not in out
        assert "It's" not in out


# ---------------------------------------------------------------------------
# normalize_for_tts_stream — streaming wrapper tests
# ---------------------------------------------------------------------------


async def _collect(stream):
    """Drain an async iterator into a list."""
    out = []
    async for chunk in stream:
        out.append(chunk)
    return out


async def _chunks_iter(items):
    """Convert a plain list into an async iterator (test helper)."""
    for item in items:
        yield item


class TestStreamWrapper:
    @pytest.mark.asyncio
    async def test_single_chunk_passes_through_normalized(self) -> None:
        out = await _collect(
            normalize_for_tts_stream(_chunks_iter(["I'm Arjun. "]))
        )
        # Single emit at end-of-stream
        assert "".join(out) == "I am Arjun. "

    @pytest.mark.asyncio
    async def test_word_boundaries_preserved_across_chunks(self) -> None:
        """Tokens split mid-contraction (e.g., 'I' + ''m Arjun') must
        still normalize correctly because the wrapper buffers the
        last partial word until a safe split point arrives."""
        chunks = ["I", "'m Arjun. ", "We", "'re", " chatting."]
        out = await _collect(
            normalize_for_tts_stream(_chunks_iter(chunks))
        )
        assembled = "".join(out)
        assert assembled == "I am Arjun. We are chatting."

    @pytest.mark.asyncio
    async def test_acronym_normalization_in_stream(self) -> None:
        chunks = ["We use ", "SaaS and ", "iPaaS at scale."]
        out = await _collect(
            normalize_for_tts_stream(_chunks_iter(chunks))
        )
        assembled = "".join(out)
        assert "sass" in assembled
        assert "i-pass" in assembled
        assert "SaaS" not in assembled
        assert "iPaaS" not in assembled

    @pytest.mark.asyncio
    async def test_no_partial_word_emitted_mid_stream(self) -> None:
        """When a chunk ends mid-word, the wrapper holds the partial
        word until the next chunk completes it. Prevents 'I' being
        emitted alone before ''m' arrives."""
        chunks = ["I", "'", "m here."]
        emitted = []
        async for piece in normalize_for_tts_stream(_chunks_iter(chunks)):
            emitted.append(piece)
        # The final assembled text is correct
        assert "".join(emitted) == "I am here."
        # AND no intermediate piece contained a partial contraction
        # like "I" or "I'" — the buffering must hold those back.
        for piece in emitted[:-1]:  # all but the final flush
            assert not piece.endswith("I")
            assert not piece.endswith("I'")

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        out = await _collect(normalize_for_tts_stream(_chunks_iter([])))
        assert out == []

    @pytest.mark.asyncio
    async def test_stream_ending_without_trailing_whitespace(self) -> None:
        """Final flush handles the no-trailing-whitespace case."""
        out = await _collect(
            normalize_for_tts_stream(_chunks_iter(["I'm here"]))
        )
        assert "".join(out) == "I am here"
