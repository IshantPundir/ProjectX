"""Tests for the Call 1 structured output schemas."""

import pytest
from pydantic import ValidationError

from app.ai.schemas import ExtractedSignals, ExtractionOutput, SignalItem


def test_extracted_signal_item_no_basis():
    item = SignalItem(value="Kafka", source="ai_extracted", inference_basis=None)
    assert item.value == "Kafka"
    assert item.source == "ai_extracted"
    assert item.inference_basis is None


def test_inferred_signal_item_has_basis():
    item = SignalItem(
        value="REST/SOAP APIs",
        source="ai_inferred",
        inference_basis="MuleSoft adjacency — REST/SOAP is a prerequisite",
    )
    assert item.source == "ai_inferred"
    assert item.inference_basis is not None


def test_invalid_source_rejected():
    with pytest.raises(ValidationError):
        SignalItem(value="Anything", source="recruiter", inference_basis=None)


def test_inferred_without_basis_rejected():
    with pytest.raises(ValidationError):
        SignalItem(value="Something", source="ai_inferred", inference_basis=None)


def test_extracted_with_basis_rejected():
    with pytest.raises(ValidationError):
        SignalItem(
            value="Python", source="ai_extracted", inference_basis="should not be here"
        )


def test_extraction_output_minimum_fields():
    out = ExtractionOutput(
        enriched_jd="A" * 60,
        signals=ExtractedSignals(
            required_skills=[
                SignalItem(value="Python", source="ai_extracted", inference_basis=None)
            ],
            preferred_skills=[],
            must_haves=[],
            good_to_haves=[],
            min_experience_years=5,
            seniority_level="senior",
            role_summary="A senior Python engineer building a scalable ingestion pipeline.",
        ),
    )
    assert out.signals.min_experience_years == 5
    assert out.signals.seniority_level == "senior"


def test_min_experience_out_of_range():
    with pytest.raises(ValidationError):
        ExtractedSignals(
            required_skills=[],
            preferred_skills=[],
            must_haves=[],
            good_to_haves=[],
            min_experience_years=-1,
            seniority_level="senior",
            role_summary="Something reasonable here for the role.",
        )


def test_enriched_jd_too_short():
    with pytest.raises(ValidationError):
        ExtractionOutput(
            enriched_jd="too short",
            signals=ExtractedSignals(
                required_skills=[],
                preferred_skills=[],
                must_haves=[],
                good_to_haves=[],
                min_experience_years=0,
                seniority_level="junior",
                role_summary="A valid role summary that meets the minimum length requirement.",
            ),
        )
