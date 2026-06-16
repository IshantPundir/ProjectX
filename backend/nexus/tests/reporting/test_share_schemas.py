import pytest
from pydantic import ValidationError

from app.modules.reporting.schemas import ShareReportIn, ShareReportOut


def test_share_in_accepts_valid_email():
    assert ShareReportIn(recipient_email="client@acme.com").recipient_email == "client@acme.com"


def test_share_in_rejects_bad_email():
    with pytest.raises(ValidationError):
        ShareReportIn(recipient_email="not-an-email")


def test_share_out_shape():
    out = ShareReportOut(share_id="abc", status="pending")
    assert out.model_dump() == {"share_id": "abc", "status": "pending"}
