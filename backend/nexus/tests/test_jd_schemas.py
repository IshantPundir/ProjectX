"""Pydantic schema tests for the JD module."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.jd.schemas import JobPostingCreate, JobPostingUpdate


# ---------------------------------------------------------------------------
# JobPostingCreate
# ---------------------------------------------------------------------------

def test_job_posting_create_accepts_minimal_body():
    """Per the unified job-creation flow, the body collects basics only —
    description_raw is optional (defaults to empty)."""
    body = JobPostingCreate(org_unit_id=uuid4(), title="Test")
    assert body.title == "Test"
    assert body.description_raw == ""
    assert body.project_scope_raw is None


def test_job_posting_create_rejects_skip_enrichment():
    """skip_enrichment was removed when enrichment moved from an implicit
    create-time side-effect to an explicit recruiter action via /enrich."""
    with pytest.raises(ValidationError):
        JobPostingCreate(
            org_unit_id=uuid4(),
            title="Test",
            description_raw="x" * 60,
            skip_enrichment=True,
        )


def test_job_posting_create_accepts_full_body():
    body = JobPostingCreate(
        org_unit_id=uuid4(),
        title="Sr. Engineer",
        description_raw="A" * 200,
        project_scope_raw="Build a thing",
        target_headcount=2,
        employment_type="full_time",
        work_arrangement="hybrid",
        location="Bengaluru",
    )
    assert body.target_headcount == 2
    assert body.employment_type == "full_time"


# ---------------------------------------------------------------------------
# JobPostingUpdate
# ---------------------------------------------------------------------------

def test_job_posting_update_all_fields_optional():
    """PATCH body — every field optional. Empty body is a valid no-op."""
    body = JobPostingUpdate()
    assert body.model_dump(exclude_unset=True) == {}


def test_job_posting_update_partial():
    body = JobPostingUpdate(description_raw="Updated JD body.")
    assert body.model_dump(exclude_unset=True) == {
        "description_raw": "Updated JD body.",
    }


def test_job_posting_update_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        JobPostingUpdate(status="active")  # status is not patchable
