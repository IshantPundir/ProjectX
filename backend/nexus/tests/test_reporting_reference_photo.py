# tests/test_reporting_reference_photo.py
"""Tests for _attach_reference_photo — presigns sessions.reference_photo_key
into ReportRead.reference_photo_url at report-read time.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.reporting.schemas import (
    DecisionOut,
    MethodologyOut,
    ReportRead,
    WhyColumn,
)


def _bare_report() -> ReportRead:
    return ReportRead(
        verdict="advance",
        verdict_reason="strong across the board",
        overall_score=82,
        overall_coverage=0.9,
        overall_confidence="high",
        decision=DecisionOut(
            headline="Advance",
            why_positive=WhyColumn(title="", body=""),
            why_negative=WhyColumn(title="", body=""),
        ),
        scores={},
        methodology=MethodologyOut(note="", charity_flags=[]),
    )


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, sess_obj):
        self._obj = sess_obj

    async def execute(self, _stmt):
        return _FakeResult(self._obj)


def _fake_session(*, key: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        reference_photo_key=key,
    )


# ---------------------------------------------------------------------------
# reference_photo_url is presigned when the key exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_photo_url_presigned_when_key_set(monkeypatch):
    report = _bare_report()
    assert report.reference_photo_url is None  # starts empty

    sess = _fake_session(key="thumbnails/t/s/ref.webp")
    fake_storage = type(
        "S",
        (),
        {"presign_get_url": AsyncMock(return_value="https://r2.example.com/ref-signed")},
    )()

    import app.modules.reporting.router as rt

    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    await rt._attach_reference_photo(
        db=_FakeDB(sess),
        report=report,
        session_id=sess.id,
        tenant_id=sess.tenant_id,
    )

    assert report.reference_photo_url == "https://r2.example.com/ref-signed"
    fake_storage.presign_get_url.assert_awaited_once()


# ---------------------------------------------------------------------------
# reference_photo_url stays None when key is absent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_photo_url_none_when_no_key(monkeypatch):
    report = _bare_report()
    sess = _fake_session(key=None)  # no reference photo taken

    fake_storage = type(
        "S", (), {"presign_get_url": AsyncMock(return_value="SHOULD_NOT_BE_CALLED")}
    )()

    import app.modules.reporting.router as rt

    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    await rt._attach_reference_photo(
        db=_FakeDB(sess),
        report=report,
        session_id=sess.id,
        tenant_id=sess.tenant_id,
    )

    assert report.reference_photo_url is None
    fake_storage.presign_get_url.assert_not_awaited()


# ---------------------------------------------------------------------------
# reference_photo_url stays None when session row is missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_photo_url_none_when_session_missing(monkeypatch):
    report = _bare_report()

    fake_storage = type(
        "S", (), {"presign_get_url": AsyncMock(return_value="SHOULD_NOT_BE_CALLED")}
    )()

    import app.modules.reporting.router as rt

    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    # DB returns None — session not found for this tenant
    await rt._attach_reference_photo(
        db=_FakeDB(None),
        report=report,
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )

    assert report.reference_photo_url is None
    fake_storage.presign_get_url.assert_not_awaited()


# ---------------------------------------------------------------------------
# Presign failure is swallowed — reference_photo_url stays None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_photo_url_none_on_presign_failure(monkeypatch):
    report = _bare_report()
    sess = _fake_session(key="thumbnails/t/s/ref.webp")

    async def _boom(*a, **k):
        raise RuntimeError("R2 exploded")

    fake_storage = type("S", (), {"presign_get_url": _boom})()

    import app.modules.reporting.router as rt

    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    # Must NOT raise
    await rt._attach_reference_photo(
        db=_FakeDB(sess),
        report=report,
        session_id=sess.id,
        tenant_id=sess.tenant_id,
    )

    assert report.reference_photo_url is None


# ---------------------------------------------------------------------------
# DB failure is swallowed — reference_photo_url stays None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_photo_url_none_on_db_failure(monkeypatch):
    report = _bare_report()

    class _BoomDB:
        async def execute(self, _stmt):
            raise RuntimeError("DB exploded")

    fake_storage = type(
        "S", (), {"presign_get_url": AsyncMock(return_value="SHOULD_NOT_BE_CALLED")}
    )()

    import app.modules.reporting.router as rt

    monkeypatch.setattr(rt, "get_object_storage", lambda: fake_storage)

    # Must NOT raise
    await rt._attach_reference_photo(
        db=_BoomDB(),
        report=report,
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )

    assert report.reference_photo_url is None
    fake_storage.presign_get_url.assert_not_awaited()
