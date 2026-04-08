"""Call 1 structured output schemas — strict Pydantic models.

These are the exact shape returned by the gpt-5.2 extraction call via
instructor. Field names match job_posting_signal_snapshots column names.
Validators enforce the provenance rule: ai_inferred requires inference_basis,
ai_extracted requires it to be null."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SignalItem(BaseModel):
    value: str = Field(min_length=1)
    source: Literal["ai_extracted", "ai_inferred"]
    inference_basis: str | None = Field(
        default=None,
        description="Required when source='ai_inferred', else null",
    )

    @model_validator(mode="after")
    def check_basis_matches_source(self) -> "SignalItem":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError(
                "SignalItem with source='ai_inferred' must have an inference_basis"
            )
        if self.source == "ai_extracted" and self.inference_basis is not None:
            raise ValueError(
                "SignalItem with source='ai_extracted' must have inference_basis=null"
            )
        return self


class ExtractedSignals(BaseModel):
    required_skills: list[SignalItem]
    preferred_skills: list[SignalItem]
    must_haves: list[SignalItem]
    good_to_haves: list[SignalItem]
    min_experience_years: int = Field(ge=0, le=50)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)


class ExtractionOutput(BaseModel):
    enriched_jd: str = Field(min_length=50)
    signals: ExtractedSignals
