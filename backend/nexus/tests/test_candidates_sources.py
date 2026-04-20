"""Tests for the CandidateSource abstraction and the ManualSource adapter."""
from app.modules.candidates.schemas import CandidateCreateRequest, CandidateSource
from app.modules.candidates.sources import ManualSource, SourcedCandidate


def test_sourced_candidate_is_frozen_dataclass():
    sc = SourcedCandidate(
        name="Alice",
        email="alice@example.com",
        phone=None,
        location=None,
        current_title=None,
        linkedin_url=None,
        notes=None,
        source="manual",
        external_id=None,
        source_metadata=None,
    )
    import dataclasses
    assert dataclasses.is_dataclass(sc)
    assert dataclasses.fields(sc)  # has fields
    # frozen — mutation raises
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        sc.name = "Bob"


def test_manual_source_produces_sourced_candidate():
    req = CandidateCreateRequest(name="Alice", email="alice@example.com")
    result = ManualSource().normalize(req)
    assert isinstance(result, SourcedCandidate)
    assert result.name == "Alice"
    assert result.email == "alice@example.com"
    assert result.source == CandidateSource.MANUAL.value == "manual"
    assert result.external_id is None
    assert result.source_metadata is None


def test_manual_source_stringifies_linkedin_url():
    req = CandidateCreateRequest(
        name="Alice",
        email="alice@example.com",
        linkedin_url="https://linkedin.com/in/alice",
    )
    result = ManualSource().normalize(req)
    assert isinstance(result.linkedin_url, str)
    assert result.linkedin_url.startswith("https://linkedin.com/in/alice")


def test_manual_source_preserves_optional_fields():
    req = CandidateCreateRequest(
        name="Alice",
        email="alice@example.com",
        phone="+15551234567",
        location="Berlin",
        current_title="Senior Engineer",
        notes="Strong EC2 background",
        external_id="ceipal-abc-123",
        source_metadata={"recruiter_note": "x"},
    )
    result = ManualSource().normalize(req)
    assert result.phone == "+15551234567"
    assert result.location == "Berlin"
    assert result.current_title == "Senior Engineer"
    assert result.notes == "Strong EC2 background"
    assert result.external_id == "ceipal-abc-123"
    assert result.source_metadata == {"recruiter_note": "x"}
