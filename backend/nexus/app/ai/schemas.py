"""Call 1 structured output schemas — strict Pydantic models.

Signal Schema v2: universal flat list where each signal carries type,
priority, weight, knockout, and stage metadata. AI determines all fields
autonomously from JD context.

Validators enforce:
  - Provenance: ai_inferred requires inference_basis, ai_extracted requires null
  - Coverage: at least 5 signals, at least 1 screen + 1 interview, at least 1 competency
  - Knockout cap: max 5 knockout signals to prevent over-flagging"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SignalType = Literal["competency", "experience", "credential", "behavioral"]
SignalPriority = Literal["required", "preferred"]
SignalStage = Literal["screen", "interview"]
SignalSource = Literal["ai_extracted", "ai_inferred"]


class SignalItemV2(BaseModel):
    """A single hiring signal extracted from a JD."""

    # What
    value: str = Field(min_length=1)
    type: SignalType

    # How important
    priority: SignalPriority
    weight: Literal[1, 2, 3] = 2
    knockout: bool = False

    # When
    stage: SignalStage

    # Provenance
    source: SignalSource
    inference_basis: str | None = Field(
        default=None,
        description="Required when source='ai_inferred', else null",
    )

    @model_validator(mode="after")
    def check_provenance(self) -> "SignalItemV2":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError(
                "Signal with source='ai_inferred' must have an inference_basis"
            )
        if self.source == "ai_extracted" and self.inference_basis is not None:
            raise ValueError(
                "Signal with source='ai_extracted' must have inference_basis=null"
            )
        return self


class ExtractedSignals(BaseModel):
    """Flat signal list with coverage validators."""

    signals: list[SignalItemV2] = Field(min_length=5)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def check_coverage(self) -> "ExtractedSignals":
        stages = {s.stage for s in self.signals}
        types = {s.type for s in self.signals}
        knockouts = [s for s in self.signals if s.knockout]

        if "screen" not in stages:
            raise ValueError("Must include at least one signal with stage='screen'")
        if "interview" not in stages:
            raise ValueError("Must include at least one signal with stage='interview'")
        if "competency" not in types:
            raise ValueError("Must include at least one competency signal")
        if len(knockouts) > 5:
            raise ValueError("Too many knockout signals (max 5)")
        return self


class ReEnrichmentOutput(BaseModel):
    enriched_jd: str = Field(min_length=200)


class EnrichmentOutput(BaseModel):
    """Phase 1 output — JD enrichment only.

    Produced by the jd_enrichment.txt prompt against the raw JD + 4-layer
    context. The actor writes this to JobPosting.description_enriched and
    sets enrichment_status='completed' before invoking phase 2.
    """

    enriched_jd: str = Field(min_length=50)


class SignalExtractionOutput(BaseModel):
    """Phase 2 output — signal extraction only.

    Produced by the jd_signal_extraction.txt prompt against either the
    enriched JD (when phase 1 ran) or the raw JD (when skip_enrichment=true).
    Persisted as a JobPostingSignalSnapshot v1 row.
    """

    signals: ExtractedSignals
