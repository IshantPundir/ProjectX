"""Engine-side keyterm assembly for Deepgram nova-3 STT.

The heavy lifting (LLM-driven keyterm extraction from the job + question bank
+ company profile) happens upstream in question_bank/refine.py at
bank-generation time and is cached on stage_question_banks.extracted_keyterms.

This module's only job is to merge that cached list with session-specific
context (the candidate's first name) and produce a final, deduped, capped
list for deepgram.STT(keyterm=[...]).

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.interview_runtime.schemas import SessionConfig

_KEYTERM_CAP = 50


@dataclass(frozen=True)
class KeytermExtraction:
    """Output of assemble_keyterms — final list + per-source attribution counts."""

    terms: list[str]
    sources: dict[str, int]


def assemble_keyterms(session_config: SessionConfig) -> KeytermExtraction:
    terms: list[str] = []
    sources: dict[str, int] = {}

    def _add(term: str, source: str) -> None:
        if not term:
            return
        if len(terms) >= _KEYTERM_CAP:
            return
        if any(t.lower() == term.lower() for t in terms):
            return
        terms.append(term)
        sources[source] = sources.get(source, 0) + 1

    # Candidate first name — the only session-specific term. build_session_config
    # already projects to first token, but defend against full names anyway.
    if session_config.candidate.name.strip():
        _add(session_config.candidate.name.split()[0], "candidate_name")

    # Bank-cached terms (LLM-extracted at bank-generation time, Task 6).
    for term in session_config.keyterms:
        _add(term, "bank_cached")

    return KeytermExtraction(terms=terms, sources=sources)
