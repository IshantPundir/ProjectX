import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.session import recording as rec


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeDB:
    """Minimal async db stub: every execute() returns the preconfigured row."""

    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _FakeResult(self._row)


def _sess(**over):
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        recording_status="ready", recording_s3_key="sessions/x/r.mp4",
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_enqueue_when_no_analysis_row(monkeypatch):
    # No proctoring row yet → enqueue.
    sess = _sess()
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    await rec._maybe_enqueue_vision(_FakeDB(None), sess)
    sent.assert_called_once_with(str(sess.id), str(sess.tenant_id))


@pytest.mark.asyncio
async def test_no_enqueue_when_not_ready(monkeypatch):
    sess = _sess(recording_status="recording", recording_s3_key=None)
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    await rec._maybe_enqueue_vision(_FakeDB(None), sess)
    sent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["ready", "unscorable", "failed"])
async def test_no_enqueue_when_terminal(monkeypatch, terminal_status):
    # Terminal rows are never auto-re-enqueued from a report read:
    #  - ready / unscorable: analysis is done (this was the report-read storm bug).
    #  - failed: Dramatiq has already exhausted its own per-message retries;
    #    re-driving on every report read would slow-loop a genuinely-broken
    #    recording. Re-analysis of a failed row is an explicit action
    #    (regenerate), not a side effect of viewing the report.
    sess = _sess()
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    row = (terminal_status, datetime.now(UTC))
    await rec._maybe_enqueue_vision(_FakeDB(row), sess)
    sent.assert_not_called()


@pytest.mark.asyncio
async def test_no_enqueue_when_running_fresh(monkeypatch):
    # A running pass updated just now → do NOT pile on (the fix).
    sess = _sess()
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    row = ("running", datetime.now(UTC) - timedelta(seconds=30))
    await rec._maybe_enqueue_vision(_FakeDB(row), sess)
    sent.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_when_running_stale(monkeypatch):
    # A running row older than the stale threshold → presumed-dead worker, re-drive.
    sess = _sess()
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    # Default threshold is 3600s; 2h old is comfortably stale.
    row = ("running", datetime.now(UTC) - timedelta(hours=2))
    await rec._maybe_enqueue_vision(_FakeDB(row), sess)
    sent.assert_called_once_with(str(sess.id), str(sess.tenant_id))


def test_no_send_when_proctoring_disabled(monkeypatch):
    """AUTO_ANALYZE_PROCTORING off => the vision actor is never enqueued."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", False)
    send = MagicMock()
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", send)

    rec._enqueue_vision_analysis("sid-1", "tid-1")
    send.assert_not_called()


def test_send_when_proctoring_enabled(monkeypatch):
    """AUTO_ANALYZE_PROCTORING on (default) => the vision actor is enqueued."""
    import app.modules.vision as vision
    from app.config import settings

    monkeypatch.setattr(settings, "auto_analyze_proctoring", True)
    send = MagicMock()
    monkeypatch.setattr(vision.analyze_session_proctoring, "send", send)

    rec._enqueue_vision_analysis("sid-1", "tid-1")
    send.assert_called_once_with("sid-1", "tid-1")
