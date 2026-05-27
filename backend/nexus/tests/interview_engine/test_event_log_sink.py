import json

from app.modules.interview_engine.event_log.envelope import EventLogEnvelope
from app.modules.interview_engine.event_log.sink import LocalFileSink


def test_sink_writes_envelope_json(tmp_path):
    sink = LocalFileSink(directory=str(tmp_path))
    env = EventLogEnvelope(
        session_id="11111111-1111-1111-1111-111111111111",
        tenant_id="t",
        correlation_id="c",
        started_at="2026-05-23T00:00:00+00:00",
    )
    ref = sink.write(env)
    assert ref.endswith("11111111-1111-1111-1111-111111111111.json")
    data = json.loads(
        (tmp_path / "11111111-1111-1111-1111-111111111111.json").read_text()
    )
    assert data["session_id"] == "11111111-1111-1111-1111-111111111111"
    assert data["engine_version"] == "v2"


def test_sink_rejects_unsafe_session_id(tmp_path):
    import pytest

    sink = LocalFileSink(directory=str(tmp_path))
    env = EventLogEnvelope(
        session_id="../escape",
        tenant_id="t",
        correlation_id="c",
        started_at="2026-05-23T00:00:00+00:00",
    )
    with pytest.raises(ValueError):
        sink.write(env)
