"""Tests for the Call 1 structured output schemas (Signal Schema v2).

Covers:
- SignalItemV2: valid creation, provenance rules
- ExtractedSignals: coverage validators (min 5, stage coverage, knockout cap)
- EnrichmentOutput: enriched_jd length validation
"""

import pytest
from pydantic import ValidationError

from app.ai.schemas import EnrichmentOutput, ExtractedSignals, SignalItemV2


# ---------------------------------------------------------------------------
# SignalItemV2 — valid creation
# ---------------------------------------------------------------------------

def test_extracted_signal_item_no_basis():
    item = SignalItemV2(
        value="Kafka",
        type="competency",
        priority="required",
        weight=2,
        knockout=False,
        stage="interview",
        source="ai_extracted",
        inference_basis=None,
    )
    assert item.value == "Kafka"
    assert item.source == "ai_extracted"
    assert item.inference_basis is None


def test_inferred_signal_item_has_basis():
    item = SignalItemV2(
        value="REST/SOAP APIs",
        type="competency",
        priority="preferred",
        weight=1,
        knockout=False,
        stage="screen",
        source="ai_inferred",
        inference_basis="MuleSoft adjacency — REST/SOAP is a prerequisite",
    )
    assert item.source == "ai_inferred"
    assert item.inference_basis is not None


# ---------------------------------------------------------------------------
# SignalItemV2 — provenance rules
# ---------------------------------------------------------------------------

def test_invalid_source_rejected():
    with pytest.raises(ValidationError):
        SignalItemV2(
            value="Anything",
            type="competency",
            priority="required",
            weight=2,
            knockout=False,
            stage="interview",
            source="recruiter",  # not valid for AI schema
            inference_basis=None,
        )


def test_inferred_without_basis_rejected():
    with pytest.raises(ValidationError):
        SignalItemV2(
            value="Something",
            type="competency",
            priority="required",
            weight=2,
            knockout=False,
            stage="interview",
            source="ai_inferred",
            inference_basis=None,
        )


def test_extracted_with_basis_rejected():
    with pytest.raises(ValidationError):
        SignalItemV2(
            value="Python",
            type="competency",
            priority="required",
            weight=2,
            knockout=False,
            stage="interview",
            source="ai_extracted",
            inference_basis="should not be here",
        )


# ---------------------------------------------------------------------------
# ExtractedSignals — coverage validators
# ---------------------------------------------------------------------------

def _make_signals(count: int = 5, **overrides) -> list[SignalItemV2]:
    """Build a list of valid signals with stage/type diversity."""
    base = [
        SignalItemV2(value="Python", type="competency", priority="required", weight=2, knockout=False, stage="interview", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="5+ years backend", type="experience", priority="required", weight=2, knockout=True, stage="screen", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="CS degree", type="credential", priority="preferred", weight=1, knockout=False, stage="screen", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="System Design", type="competency", priority="required", weight=3, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role implies architectural ownership"),
        SignalItemV2(value="Mentoring", type="behavioral", priority="preferred", weight=1, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role at growth-stage company"),
    ]
    return base[:count]


def test_enrichment_output_minimum_fields():
    out = EnrichmentOutput(enriched_jd="A" * 80)
    assert len(out.enriched_jd) == 80


def test_fewer_than_5_signals_rejected():
    with pytest.raises(ValidationError):
        ExtractedSignals(
            signals=_make_signals(3),
            seniority_level="senior",
            role_summary="A valid role summary that meets the minimum length requirement.",
        )


def test_missing_screen_stage_rejected():
    """All signals with stage='interview' and none with 'screen' is rejected."""
    all_interview = [
        SignalItemV2(value=f"Skill {i}", type="competency", priority="required", weight=2, knockout=False, stage="interview", source="ai_extracted", inference_basis=None)
        for i in range(5)
    ]
    with pytest.raises(ValidationError, match="screen"):
        ExtractedSignals(
            signals=all_interview,
            seniority_level="senior",
            role_summary="A valid role summary that meets the minimum length requirement.",
        )


def test_missing_interview_stage_rejected():
    """All signals with stage='screen' and none with 'interview' is rejected."""
    all_screen = [
        SignalItemV2(value=f"Skill {i}", type="competency", priority="required", weight=2, knockout=False, stage="screen", source="ai_extracted", inference_basis=None)
        for i in range(5)
    ]
    with pytest.raises(ValidationError, match="interview"):
        ExtractedSignals(
            signals=all_screen,
            seniority_level="senior",
            role_summary="A valid role summary that meets the minimum length requirement.",
        )


def test_missing_competency_type_rejected():
    """Signals without at least one competency type is rejected."""
    no_competency = [
        SignalItemV2(value="5+ years", type="experience", priority="required", weight=2, knockout=False, stage="screen", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="CS degree", type="credential", priority="preferred", weight=1, knockout=False, stage="screen", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="Teamwork", type="behavioral", priority="preferred", weight=1, knockout=False, stage="interview", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="10+ years", type="experience", priority="required", weight=2, knockout=False, stage="interview", source="ai_extracted", inference_basis=None),
        SignalItemV2(value="Leadership", type="behavioral", priority="required", weight=2, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role"),
    ]
    with pytest.raises(ValidationError, match="competency"):
        ExtractedSignals(
            signals=no_competency,
            seniority_level="senior",
            role_summary="A valid role summary that meets the minimum length requirement.",
        )


def test_knockout_cap_exceeded_rejected():
    """More than 5 knockout signals is rejected."""
    too_many_knockouts = [
        SignalItemV2(value=f"Skill {i}", type="competency", priority="required", weight=2, knockout=True, stage="screen" if i < 3 else "interview", source="ai_extracted", inference_basis=None)
        for i in range(6)
    ]
    with pytest.raises(ValidationError, match="knockout"):
        ExtractedSignals(
            signals=too_many_knockouts,
            seniority_level="senior",
            role_summary="A valid role summary that meets the minimum length requirement.",
        )


# ---------------------------------------------------------------------------
# EnrichmentOutput — enriched_jd validation
# ---------------------------------------------------------------------------

def test_enriched_jd_too_short():
    with pytest.raises(ValidationError):
        EnrichmentOutput(enriched_jd="too short")
