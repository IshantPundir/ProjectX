from app.main import _TENANT_SCOPED_TABLES
from app.modules.reporting.models import ReportShare


def test_report_shares_is_tenant_scoped():
    assert "report_shares" in _TENANT_SCOPED_TABLES


def test_report_share_model_columns():
    cols = ReportShare.__table__.columns.keys()
    for expected in (
        "id", "tenant_id", "session_id", "report_id", "recipient_email",
        "status", "pdf_r2_key", "requested_by", "requested_at", "sent_at", "error",
    ):
        assert expected in cols
    assert ReportShare.__table__.name == "report_shares"
