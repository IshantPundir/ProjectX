from unittest.mock import AsyncMock

import pytest

from app.modules.reporting.schemas import (
    DecisionOut, MethodologyOut, QuestionOut, ReportRead, WhyColumn,
)


def _report_with_question(qid: str) -> ReportRead:
    return ReportRead(
        verdict="reject", verdict_reason="r", overall_score=35, overall_coverage=0.3,
        overall_confidence="low",
        decision=DecisionOut(headline="h", why_positive=WhyColumn(title="", body=""),
                             why_negative=WhyColumn(title="", body="")),
        scores={}, methodology=MethodologyOut(note="", charity_flags=[]),
        questions=[QuestionOut(seq=1, question_id=qid, title="t",
                               status_badge="failed_required", status_tone="danger",
                               question_text="Q?", candidate_quote="a")],
    )


@pytest.mark.asyncio
async def test_attaches_presigned_url_by_question_id(monkeypatch):
    report = _report_with_question("q1")

    class FakeThumb:
        kind = "question"; ref_id = "q1"; s3_key = "thumbs/t/s/question_q1.webp"

    async def fake_get_thumbs(db, *, session_id, tenant_id):
        return [FakeThumb()]

    fake_storage = type("S", (), {"presign_get_url": AsyncMock(return_value="https://signed/q1")})()

    import app.modules.reporting.assets as rt
    monkeypatch.setattr(rt, "get_session_timeline_thumbnails", fake_get_thumbs)
    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    await rt.attach_question_thumbnails(db=None, report=report, session_id="s", tenant_id="t")
    assert report.questions[0].thumbnail_url == "https://signed/q1"


@pytest.mark.asyncio
async def test_no_thumbnail_leaves_url_none(monkeypatch):
    report = _report_with_question("q1")

    async def fake_get_thumbs(db, *, session_id, tenant_id):
        return []

    import app.modules.reporting.assets as rt
    monkeypatch.setattr(rt, "get_session_timeline_thumbnails", fake_get_thumbs)
    await rt.attach_question_thumbnails(db=None, report=report, session_id="s", tenant_id="t")
    assert report.questions[0].thumbnail_url is None
