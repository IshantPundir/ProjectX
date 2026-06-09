"""Layer 2 — per-QUESTION grade. A deterministic base level over the question's
own elicited notes, refined by an LLM graded against the question's full bank
card (rubric + listen-for + red-flags + evaluation_hint), difficulty-calibrated
and probe-aware. Replaces the per-signal recheck."""
from __future__ import annotations

from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture,
)
from app.modules.reporting.scoring.types import DemonstrationLevel

_TEXTURE_RANK = {EvidenceTexture.thin: 0, EvidenceTexture.concrete: 1, EvidenceTexture.strong: 2}
_RANK_LEVEL = {2: "strong", 1: "solid", 0: "thin"}


def question_base_level(notes: list[EvidenceNote]) -> DemonstrationLevel:
    """Deterministic base for ONE question from the notes IT elicited.
    Supporting notes → best texture (strong>concrete>thin). No supports:
    an un-retracted contradiction → absent; else not_reached."""
    supports = [n for n in notes if n.stance == EvidenceStance.supports]
    if supports:
        best = max(_TEXTURE_RANK[n.texture] for n in supports)
        return _RANK_LEVEL[best]  # type: ignore[return-value]
    if any(n.stance == EvidenceStance.contradicts and n.retracts_seq is None for n in notes):
        return "absent"
    return "not_reached"
