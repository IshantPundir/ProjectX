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


def test_bank_to_response_serializes_generation_status_by_kind():
    """Seam test: the router serializer must carry generation_status_by_kind to the wire.

    Regression guard for the M2 final-review finding — the field existed on the ORM
    column + the BankResponse schema, but `_bank_to_response` dropped it, so every bank
    read serialized `{}` and the FE section-status pills always showed "Pending". The
    other tests in this file build BankResponse DIRECTLY, so they never exercised the
    serializer seam; this one goes through `_bank_to_response`.
    """
    import uuid
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.modules.question_bank.router import _bank_to_response

    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    status_by_kind = {"behavioral": "reviewing", "technical": "generating"}
    bank = SimpleNamespace(
        id=uuid.uuid4(),
        stage_id=uuid.uuid4(),
        job_posting_id=uuid.uuid4(),
        signal_snapshot_id=uuid.uuid4(),
        status="reviewing",
        prompt_version="v2",
        generation_error=None,
        coverage_notes=None,
        generation_status_by_kind=status_by_kind,
        generated_at=None,
        generated_by=None,
        confirmed_at=None,
        confirmed_by=None,
        created_at=now,
        updated_at=now,
    )
    resp = _bank_to_response(
        bank, question_count=3, total_minutes=6.0, is_stale=False
    )
    assert resp.generation_status_by_kind == status_by_kind
