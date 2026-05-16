"""TranscriptEntry — extracted into a leaf module to break the circular
import between interview_runtime.schemas and engine.models.speaker.

interview_engine.models.speaker imports TranscriptEntry. Re-importing
from interview_runtime.schemas would re-enter the partially-initialized
package. Importing from this leaf module bypasses the cycle entirely.

interview_runtime.schemas re-exports TranscriptEntry for backward
compatibility — existing callers don't need to change.
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
