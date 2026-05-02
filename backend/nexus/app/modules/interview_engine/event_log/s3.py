"""S3Sink — writes the envelope to s3://{bucket}/{tenant_id}/{session_id}/engine_events.json.

Deploy-gate destination per spec §3.3. Bucket must have versioning ON
(deployment-side concern; not enforced here). Sync via boto3 — call from
asyncio.to_thread() in agent code.
"""

from __future__ import annotations

import boto3
import structlog

from app.config import settings
from app.modules.interview_engine.event_log.envelope import EventLogEnvelope

logger = structlog.get_logger("engine.event_log.s3")


def _create_s3_client():
    """Create a fresh S3 client. Overridden via monkeypatch in tests.

    Mirrors the same pattern used in app/modules/candidates/resume_service.py.
    """
    return boto3.client("s3", region_name=settings.aws_region)


class S3Sink:
    """Concrete sink writing one JSON object per envelope to S3."""

    def __init__(self, *, bucket: str) -> None:
        if not bucket:
            raise ValueError("S3Sink requires a non-empty bucket name")
        self._bucket = bucket

    def write(self, envelope: EventLogEnvelope) -> str:
        key = f"{envelope.tenant_id}/{envelope.session_id}/engine_events.json"
        body = envelope.model_dump_json()
        client = _create_s3_client()
        client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        s3_uri = f"s3://{self._bucket}/{key}"
        logger.info(
            "event_log.s3.written",
            uri=s3_uri,
            session_id=envelope.session_id,
            events=len(envelope.events),
        )
        return s3_uri
