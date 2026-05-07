"""SignalLedger Pydantic models — append-only evidence log + per-signal snapshots."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CoverageState(StrEnum):
    none = "none"
    partial = "partial"
    sufficient = "sufficient"
    failed = "failed"  # terminal — set on no-experience or knockout disclosure
    # No "strong" state. Answer-quality grading lives in the post-session Report Builder.


class LedgerEntry(BaseModel):
    seq: int = Field(ge=1)
    turn_id: str
    signal_value: str
    anchor_id: int = Field(
        ge=-1,
        description="Index into positive_evidence list. -1 sentinel for failure entries.",
    )
    evidence_quote: str = Field(min_length=1, max_length=500)
    coverage_before: CoverageState
    coverage_after: CoverageState
    recorded_at_ms: int = Field(ge=0)


class SignalSnapshot(BaseModel):
    signal_value: str
    coverage: CoverageState
    anchors_hit: list[int] = Field(default_factory=list)
    last_observation_seq: int | None = None


class SignalLedgerSnapshot(BaseModel):
    entries: list[LedgerEntry] = Field(default_factory=list)
    snapshots: dict[str, SignalSnapshot] = Field(default_factory=dict)
    next_seq: int = Field(ge=1, default=1)
