"""Verify every LLM-cited evidence quote is a real substring of the transcript.
Kills hallucinated competence. Normalizes case + whitespace (STT/formatting jitter)."""
from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()

def is_grounded(quote: str, transcript_text: str) -> bool:
    if not quote.strip():
        return False
    return _norm(quote) in _norm(transcript_text)

def ground_quotes(quotes: list[str], transcript_text: str) -> tuple[list[str], list[str]]:
    grounded, ungrounded = [], []
    for q in quotes:
        (grounded if is_grounded(q, transcript_text) else ungrounded).append(q)
    return grounded, ungrounded
