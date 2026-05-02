"""Audit-grade event log for the interview engine.

Phase 1 of the engine redesign. Provides:
- EventLogEnvelope: the single JSON file written per session
- EventLogEvent: one row in the envelope's events list
- EventCollector: in-memory aggregator fed by agent.py listeners
- EventLogSink protocol + LocalFileSink + S3Sink
- build_sink_from_settings: env-driven sink dispatch

See docs/superpowers/specs/2026-05-02-interview-engine-redesign-overview-design.md §3.3-§3.4.
"""

from app.modules.interview_engine.event_log.collector import EventCollector
from app.modules.interview_engine.event_log.envelope import (
    EventLogEnvelope,
    EventLogEvent,
)
from app.modules.interview_engine.event_log.factory import build_sink_from_settings
from app.modules.interview_engine.event_log.sink import EventLogSink

__all__ = [
    "EventCollector",
    "EventLogEnvelope",
    "EventLogEvent",
    "EventLogSink",
    "build_sink_from_settings",
]
