"""Reel eligibility (pure) + RLS membership (lean nexus image)."""
from app.modules.reel.service import eligibility_decision


def test_eligible_when_report_ready_verdict_ok_recording_present():
    ok, reason = eligibility_decision(
        report_status="ready", verdict="advance", recording_key="reels/x.mp4")
    assert ok is True and reason is None


def test_borderline_is_eligible():
    ok, _ = eligibility_decision(
        report_status="ready", verdict="borderline", recording_key="k")
    assert ok is True


def test_reject_verdict_is_ineligible():
    ok, reason = eligibility_decision(
        report_status="ready", verdict="reject", recording_key="k")
    assert ok is False and "advancing or borderline" in reason


def test_report_not_ready_is_ineligible():
    ok, reason = eligibility_decision(
        report_status="generating", verdict="advance", recording_key="k")
    assert ok is False and "Report" in reason


def test_no_report_row_is_ineligible():
    ok, reason = eligibility_decision(
        report_status=None, verdict=None, recording_key="k")
    assert ok is False


def test_missing_recording_is_ineligible():
    ok, reason = eligibility_decision(
        report_status="ready", verdict="advance", recording_key=None)
    assert ok is False and "recording" in reason.lower()


def test_session_reels_is_tenant_scoped():
    from app.main import _TENANT_SCOPED_TABLES
    assert "session_reels" in _TENANT_SCOPED_TABLES
