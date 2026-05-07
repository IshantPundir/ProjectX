import pytest
from pydantic import ValidationError

from app.modules.interview_engine.models.claims import ClaimEntry, ClaimsPoolSnapshot


def test_claim_entry_required_fields():
    claim = ClaimEntry(
        claim_topic="automation",
        claim_text="Built CI pipelines for 50+ services.",
        source_quote="I built CI pipelines for over fifty services in my last role.",
        captured_at_turn=3,
        captured_at_seq=12,
    )
    assert claim.claim_topic == "automation"
    assert claim.captured_at_turn == 3


def test_claim_entry_topic_max_length():
    with pytest.raises(ValidationError):
        ClaimEntry(
            claim_topic="x" * 41,  # > 40
            claim_text="ok",
            source_quote="ok",
            captured_at_turn=1,
            captured_at_seq=1,
        )


def test_claim_entry_text_max_length():
    with pytest.raises(ValidationError):
        ClaimEntry(
            claim_topic="ok",
            claim_text="x" * 201,  # > 200
            source_quote="ok",
            captured_at_turn=1,
            captured_at_seq=1,
        )


def test_claims_pool_snapshot_empty_default():
    pool = ClaimsPoolSnapshot()
    assert pool.entries == []
