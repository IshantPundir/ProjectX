"""TTS-friendly text normalization for Sarvam bulbul:v3 (en-IN).

Sarvam's bulbul:v3 mispronounces certain English forms:

  - English contractions are read as if the apostrophe is absent:
    "I'm"   → "im"  (one syllable instead of "I am" / "eye-em")
    "I'll"  → "ill" (rhymes with "pill" instead of "I will")
    "you're", "you've", "we're", "it's", etc. — same failure mode.

  - camelCase / mixed-case compound acronyms are fragmented:
    "SaaS"  → "Saa S"  (two words)
    "iPaaS" → "IPA AS" (broken into syllables)

Fixes applied:

  1. **Contractions** — expanded to full forms ("I'm" → "I am"). The
     formality cost is mild for an interview agent and the audible
     correctness wins.

  2. **Compound camelCase acronyms** — respelled phonetically
     ("SaaS" → "sass", "iPaaS" → "i-pass"). The hyphen in "i-pass"
     triggers Sarvam's letter-by-letter spell-out for the "i",
     producing "eye pass". 4+-letter forms with no clean phonetic
     word ("IaaS") fall back to full letter spell-out ("I-A-A-S").

Where this runs:

  - PRE-prompt on bank_text (so the LLM sees the normalized form
    and tends to preserve it in its output) — see input_builder.
  - POST-LLM on the Speaker's streaming output (safety net for forms
    the LLM emits fresh that bank_text never contained) — see
    orchestrator's call to ``normalize_for_tts_stream``.

Both paths share the same replacement tables defined here so we have a
single source of truth.

References:
- https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert
- https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/text-to-speech/how-to/enable-text-preprocessing
"""
from __future__ import annotations

import re
from typing import AsyncIterator


# ---------------------------------------------------------------------------
# Contractions
# ---------------------------------------------------------------------------

# Map of lowercased contraction → expanded form.
# Possessive 's (e.g., "John's resume") is NOT in this list — only
# contractions of pronouns + auxiliaries fail; possessives pronounce
# correctly on Sarvam.
_CONTRACTIONS: dict[str, str] = {
    # I-prefixed: I is always capital in English; replacement keeps "I"
    "i'm":  "I am",
    "i'll": "I will",
    "i've": "I have",
    "i'd":  "I would",
    # Second-person
    "you're": "you are",
    "you'll": "you will",
    "you've": "you have",
    "you'd":  "you would",
    # First-person plural
    "we're": "we are",
    "we'll": "we will",
    "we've": "we have",
    "we'd":  "we would",
    # Third-person plural
    "they're": "they are",
    "they'll": "they will",
    "they've": "they have",
    "they'd":  "they would",
    # Third-person singular (ambiguous "X's" expansions chosen by
    # most-common usage; "he's a developer" = "he is", which dominates
    # the "he has a developer" reading)
    "he's":   "he is",
    "she's":  "she is",
    "it's":   "it is",
    "that's": "that is",
    "what's": "what is",
    "where's": "where is",
    "there's": "there is",
    "here's":  "here is",
    "who's":   "who is",
    "let's":   "let us",
    # Negatives
    "don't":     "do not",
    "doesn't":   "does not",
    "didn't":    "did not",
    "can't":     "cannot",
    "won't":     "will not",
    "wouldn't":  "would not",
    "shouldn't": "should not",
    "couldn't":  "could not",
    "isn't":     "is not",
    "aren't":    "are not",
    "wasn't":    "was not",
    "weren't":   "were not",
    "hasn't":    "has not",
    "haven't":   "have not",
    "hadn't":    "had not",
}

# Sort keys longest-first so longer forms match before shorter ones
# (regex alternation is left-to-right; "you'll" must be tried before
# "you'd" to avoid prefix shadowing — though re.escape + \b boundaries
# handle most cases, longest-first is the safe convention).
_CONTRACTIONS_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(k) for k in sorted(_CONTRACTIONS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _replace_contraction(match: re.Match[str]) -> str:
    original = match.group(0)
    expanded = _CONTRACTIONS[original.lower()]
    # Preserve the case of the first letter so "You're awesome" →
    # "You are awesome", not "you are awesome".
    if original and original[0].isupper():
        expanded = expanded[0].upper() + expanded[1:]
    return expanded


# ---------------------------------------------------------------------------
# Compound camelCase acronyms (SaaS, iPaaS, …)
# ---------------------------------------------------------------------------

# Case-sensitive replacement table. The capitalization MUST match
# exactly — "SaaS" is the camelCase canonical form; "SAAS" / "saas"
# fall through unchanged (Sarvam treats those differently and we
# don't want to over-clobber).
#
# Order matters: longer / more-specific patterns first so that
# substring overlap (DevSecOps vs DevOps) doesn't misfire.
_CAMEL_ACRONYMS: tuple[tuple[str, str], ...] = (
    # 4-letter no-clean-phonetic-word forms → full letter spell-out
    ("IaaS", "I-A-A-S"),

    # DevOps family — must be ordered longest-first for substring safety
    ("DevSecOps", "dev sec ops"),
    ("MLOps", "M-L ops"),
    ("DevOps", "dev ops"),
    ("FinOps", "fin ops"),

    # SaaS family
    ("iPaaS", "i-pass"),
    ("SaaS",  "sass"),
    ("PaaS",  "pass"),

    # Other compound acronyms
    ("NoSQL",   "no sequel"),
    ("GraphQL", "graph Q-L"),
)


def _apply_camel_acronyms(text: str) -> str:
    out = text
    for raw, phonetic in _CAMEL_ACRONYMS:
        out = re.sub(rf"\b{re.escape(raw)}\b", phonetic, out)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_for_tts(text: str) -> str:
    """Apply ALL TTS normalizations to a fully-assembled string.

    Idempotent and pure. Safe to call on the same text multiple times.
    Use this for pre-prompt rewrites (bank_text) and for post-LLM
    text where a stream wrapper isn't appropriate.
    """
    if not text:
        return text
    out = _CONTRACTIONS_PATTERN.sub(_replace_contraction, text)
    out = _apply_camel_acronyms(out)
    return out


async def normalize_for_tts_stream(
    upstream: AsyncIterator[str],
) -> AsyncIterator[str]:
    """Async generator that buffers an LLM token stream and yields
    TTS-normalized chunks.

    Streaming-safe: buffers up to the LAST whitespace position in the
    accumulated buffer, applies replacements to that prefix, yields it,
    and keeps the tail (which may be a partial word) for the next
    iteration. The tail is held until the next chunk completes the
    word — preventing a contraction like "I'm" from being half-emitted
    as "I'" then "m walked".

    End-of-stream: any remaining buffer is flushed through
    ``normalize_for_tts`` and yielded.

    Latency cost: roughly one word of additional buffering before the
    first audible token (~100-300ms). Acceptable for interview-agent
    pacing where TTFB is dominated by TTS-TTFB, not LLM-first-token.
    """
    # Boundary regex: match everything up through the last whitespace.
    # ``re.DOTALL`` so newlines are included as whitespace.
    boundary = re.compile(r".*\s", re.DOTALL)

    buffer = ""
    async for chunk in upstream:
        buffer += chunk
        m = boundary.match(buffer)
        if m is not None and m.end() > 0:
            safe_prefix = buffer[: m.end()]
            buffer = buffer[m.end():]
            yield normalize_for_tts(safe_prefix)

    if buffer:
        yield normalize_for_tts(buffer)
