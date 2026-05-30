# app/modules/vision/schemas.py
from __future__ import annotations

from pydantic import BaseModel, Field


class ProctoringAnalysisRead(BaseModel):
    """Report-page payload. `status='absent'` when no analysis row exists."""

    status: str  # absent | pending | running | ready | failed | unscorable
    risk_band: str | None = None
    detector_summary: dict | None = None
    gaze_heatmap: dict | None = None
    flagged_intervals: list[dict] = Field(default_factory=list)
    gaze_signal_quality: str | None = None
    unscorable_pct: float | None = None
