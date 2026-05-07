"""CandidateClaimsPool Pydantic models — capped pool of biographical claims."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimEntry(BaseModel):
    """Canonical ClaimEntry shape with capture metadata.

    The Judge emits a narrower shape (no captured_at_*) in models.judge.ClaimEntry;
    the State Engine canonicalizes to this shape when ingesting.
    """

    claim_topic: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=200)
    source_quote: str = Field(min_length=1, max_length=500)
    captured_at_turn: int = Field(ge=0)
    captured_at_seq: int = Field(ge=1)


class ClaimsPoolSnapshot(BaseModel):
    entries: list[ClaimEntry] = Field(default_factory=list)
