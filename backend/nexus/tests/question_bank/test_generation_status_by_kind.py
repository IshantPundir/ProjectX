"""Verifies the generation_status_by_kind column reads/writes through ORM + schema."""
import pytest

from app.modules.question_bank.schemas import BankResponse


def test_bank_response_accepts_generation_status_by_kind():
    """Pydantic schema accepts the new field and round-trips correctly."""
    payload = {
        "behavioral": "reviewing",
        "technical": "reviewing",
    }
    # Minimal valid BankResponse — match the existing required-field set
    response = BankResponse(
        id="00000000-0000-0000-0000-000000000001",
        stage_id="00000000-0000-0000-0000-000000000002",
        job_posting_id="00000000-0000-0000-0000-000000000003",
        signal_snapshot_id="00000000-0000-0000-0000-000000000004",
        status="reviewing",
        prompt_version="v1",
        generation_error=None,
        coverage_notes=None,
        generated_at=None,
        generated_by=None,
        confirmed_at=None,
        confirmed_by=None,
        question_count=0,
        total_minutes=0.0,
        is_stale=False,
        created_at="2026-05-19T00:00:00Z",
        updated_at="2026-05-19T00:00:00Z",
        generation_status_by_kind=payload,
    )
    assert response.generation_status_by_kind == payload


def test_bank_response_defaults_empty_dict():
    """Field defaults to empty dict when omitted (legacy banks)."""
    response = BankResponse(
        id="00000000-0000-0000-0000-000000000001",
        stage_id="00000000-0000-0000-0000-000000000002",
        job_posting_id="00000000-0000-0000-0000-000000000003",
        signal_snapshot_id="00000000-0000-0000-0000-000000000004",
        status="draft",
        prompt_version="v1",
        generation_error=None,
        coverage_notes=None,
        generated_at=None,
        generated_by=None,
        confirmed_at=None,
        confirmed_by=None,
        question_count=0,
        total_minutes=0.0,
        is_stale=False,
        created_at="2026-05-19T00:00:00Z",
        updated_at="2026-05-19T00:00:00Z",
    )
    assert response.generation_status_by_kind == {}
