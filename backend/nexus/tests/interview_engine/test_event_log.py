"""v2 event-log collector: records events + decision records into a valid envelope."""

from app.modules.interview_engine.audit import TurnDecisionRecord
from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope


def test_collector_records_event():
    c = EventCollector(session_id="s-1", tenant_id="ten-1", correlation_id="corr-1")
    c.record("engine.v2.dispatched", {"job_title": "FDE"}, t_ms=0, wall_ms=1)
    env = c.envelope(closed_at="2026-05-22T00:00:01Z")
    assert isinstance(env, EventLogEnvelope)
    assert env.session_id == "s-1"
    assert env.correlation_id == "corr-1"
    assert len(env.events) == 1
    assert env.events[0].kind == "engine.v2.dispatched"


def test_collector_records_decision():
    c = EventCollector(session_id="s-1", tenant_id="ten-1", correlation_id="corr-1")
    rec = TurnDecisionRecord(turn_ref="t-1", candidate_quote="x", move="advance",
                             reasoning="y", directive_id="d-1")
    c.record_decision(rec, t_ms=10, wall_ms=2)
    env = c.envelope()
    assert env.events[0].kind == "turn.decision"
    assert env.events[0].payload["directive_id"] == "d-1"
    assert env.closed_at is None
