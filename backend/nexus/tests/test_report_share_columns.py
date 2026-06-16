from app.modules.reporting.models import ReportShare


def test_report_share_has_token_columns():
    cols = set(ReportShare.__table__.columns.keys())
    for c in ("share_token_hash", "share_expires_at", "revoked_at",
              "last_viewed_at", "view_count"):
        assert c in cols, f"missing column {c}"
