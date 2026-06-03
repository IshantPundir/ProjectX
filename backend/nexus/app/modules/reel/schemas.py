"""Wire schemas for the Candidate Reel API (frontend contract)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReelStatus = Literal["absent", "pending", "generating", "ready", "failed"]


class ReelChapter(BaseModel):
    """One beat in the reel, for the player's chapter rail (jump-to-beat)."""
    kind: str           # title | match | experience | point | clip | outro
    label: str          # short, human-readable (on-screen text or a kind label)
    start_ms: int       # offset from the reel's start


class ReelPlayback(BaseModel):
    """The reel's playback envelope. ``status='absent'`` = no reel row yet.

    ``signed_url`` is a short-lived presigned R2 GET, minted on read for
    ``ready`` only and never logged.
    """
    status: ReelStatus
    signed_url: str | None = None
    expires_at: str | None = None
    duration_seconds: float | None = None
    chapters: list[ReelChapter] = Field(default_factory=list)
    generation_error: str | None = None
    # Whether a reel CAN be generated (verdict + report-ready + recording-ready).
    eligible: bool = False
    ineligible_reason: str | None = None
    version: int = 1
