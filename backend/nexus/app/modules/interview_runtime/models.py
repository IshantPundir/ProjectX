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


class TranscriptEntry(BaseModel):
    """A single utterance in the interview transcript."""

    role: Literal["agent", "candidate"]
    text: str
    timestamp_ms: int = Field(
        ge=0,
        description="Milliseconds since session start.",
    )
    question_id: str | None = None
