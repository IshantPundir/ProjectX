import pytest
from uuid import uuid4
from app.modules.jd.schemas import JobPostingCreate


def test_job_posting_create_skip_enrichment_default_false():
    body = JobPostingCreate(
        org_unit_id=uuid4(),
        title="Test",
        description_raw="x" * 60,
    )
    assert body.skip_enrichment is False


def test_job_posting_create_skip_enrichment_true():
    body = JobPostingCreate(
        org_unit_id=uuid4(),
        title="Test",
        description_raw="x" * 60,
        skip_enrichment=True,
    )
    assert body.skip_enrichment is True
