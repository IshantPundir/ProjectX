"""JobPosting carries the v2 selection column."""

from app.modules.jd.models import JobPosting


def test_jobposting_has_interview_engine_version_column():
    assert "interview_engine_version" in JobPosting.__table__.columns
    col = JobPosting.__table__.columns["interview_engine_version"]
    assert col.nullable is True
