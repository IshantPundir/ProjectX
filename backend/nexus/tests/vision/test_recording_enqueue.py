import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.modules.session import recording as rec


def test_enqueue_on_ready(monkeypatch):
    sess = SimpleNamespace(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        recording_status="ready", recording_s3_key="sessions/x/r.mp4",
    )
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    rec._maybe_enqueue_vision(sess)
    sent.assert_called_once_with(str(sess.id), str(sess.tenant_id))


def test_no_enqueue_when_not_ready(monkeypatch):
    sess = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                           recording_status="recording", recording_s3_key=None)
    sent = MagicMock()
    monkeypatch.setattr(rec, "_enqueue_vision_analysis", sent)
    rec._maybe_enqueue_vision(sess)
    sent.assert_not_called()
