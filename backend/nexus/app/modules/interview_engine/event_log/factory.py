"""Sink factory — env-driven dispatch."""

from __future__ import annotations

from app.config import settings
from app.modules.interview_engine.event_log.local_file import LocalFileSink
from app.modules.interview_engine.event_log.s3 import S3Sink
from app.modules.interview_engine.event_log.sink import EventLogSink


def build_sink_from_settings() -> EventLogSink | None:
    """Return the configured sink, or None when disabled.

    Reads `engine_event_log_sink` (`local`|`s3`|`none`).
    `local` -> LocalFileSink(directory=engine_event_log_dir)
    `s3` -> S3Sink(bucket=aws_s3_bucket_engine_events) — raises if bucket empty
    `none` -> None (no envelope is written; structlog stdout remains the only artifact)
    """
    sink_kind = settings.engine_event_log_sink
    if sink_kind == "none":
        return None
    if sink_kind == "local":
        return LocalFileSink(directory=settings.engine_event_log_dir)
    if sink_kind == "s3":
        bucket = settings.aws_s3_bucket_engine_events
        if not bucket:
            raise ValueError(
                "engine_event_log_sink=s3 but AWS_S3_BUCKET_ENGINE_EVENTS is empty"
            )
        return S3Sink(bucket=bucket)
    raise ValueError(f"unknown engine_event_log_sink: {sink_kind!r}")
