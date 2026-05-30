# tests/vision/test_proctoring_analysis_rls.py
from app.main import _TENANT_SCOPED_TABLES
from app.modules.vision.models import SessionProctoringAnalysis


def test_table_registered_for_rls_completeness():
    assert "session_proctoring_analysis" in _TENANT_SCOPED_TABLES


def test_model_is_tenant_scoped():
    cols = SessionProctoringAnalysis.__table__.columns
    assert "tenant_id" in cols
    assert "session_id" in cols
    assert cols["session_id"].unique is True
