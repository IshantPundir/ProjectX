"""TranscriptEntry — a leaf data model kept in its own module so callers can
import it without re-entering the partially-initialized
``interview_runtime`` package (importing from
``interview_runtime.schemas`` would trigger that cycle).

``interview_runtime.schemas`` re-exports TranscriptEntry for backward
compatibility — existing callers don't need to change.

NOTE: This file is named `models.py` because the module-boundary test
(tests/test_module_boundaries.py) explicitly allows cross-module deep
imports of the `models` submodule for ORM / data-class ergonomics.
The previous name `transcript_entry.py` was a leaf but not a recognised
exception, which caused a boundary violation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WordTiming(BaseModel):
    """One spoken word, timed RELATIVE to the start of its turn (first word = 0).

    Relative offsets are clock-agnostic and exact: they need no audio-stream /
    session / recording clock reconciliation. Absolute placement on the video
    timeline is resolved later (Phase 2) by anchoring the turn to its
    ``timestamp_ms`` and applying the calibrated recording offset.
    """

    text: str
    start_ms: int = Field(ge=0, description="Ms from the turn's first word.")
    end_ms: int = Field(ge=0, description="Ms from the turn's first word.")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class TranscriptEntry(BaseModel):
    """A single utterance in the interview transcript."""

    role: Literal["agent", "candidate"]
    text: str
    timestamp_ms: int = Field(
        ge=0,
        description="Milliseconds since session start (turn commit anchor).",
    )
    question_id: str | None = None
    # Word-level timing (candidate turns only; agent turns are re-voiced, not
    # clipped, so they stay None). Added Phase 1 for the candidate reel.
    start_ms: int | None = Field(
        default=None, ge=0,
        description="Best-effort turn speech start on the session clock "
                    "(= timestamp_ms - spoken duration). None when unknown.",
    )
    end_ms: int | None = Field(
        default=None, ge=0,
        description="Best-effort turn speech end on the session clock "
                    "(= timestamp_ms). None when unknown.",
    )
    words: list[WordTiming] | None = None
