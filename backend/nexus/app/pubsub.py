"""Centralized pub/sub for domain events.

This is the module boundary: callers depend on `publish()` and
`subscribe()` — the Redis transport is an implementation detail.
Swap-out to SNS / Cloud Pub/Sub at enterprise is a change inside
this file, not a change at call sites.

Design invariants:
  - publish() is fire-and-forget and NEVER raises. Failures are logged
    and counted via a structlog event; the calling flow continues.
  - publish() must be called AFTER the DB transaction has committed.
    In FastAPI handlers, use `BackgroundTasks.add_task(publish, ...)`
    — FastAPI runs background tasks after the response is sent, which
    is after dependency-cleanup commits the transaction. In Dramatiq
    actors, call publish() inline after the `async with session.begin():`
    context exits.
  - subscribe() auto-reconnects with exponential backoff. Events missed
    during a disconnect are NOT re-delivered — callers must have a
    correctness backstop (e.g. DB polling) if event loss is unacceptable.
  - A separate Redis client instance is used from Dramatiq's broker to
    avoid starving task workers (pub/sub subscribe blocks).
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import orjson
import redis.asyncio as aioredis
import structlog
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from app.config import settings

logger = structlog.get_logger(__name__)


# --- Event name constants -------------------------------------------------

class Events:
    """Canonical event-name strings. Compare against these, never raw strings."""
    BANK_QUESTION_ADDED = "bank.question_added"
    BANK_QUESTION_UPDATED = "bank.question_updated"
    BANK_STATUS_CHANGED = "bank.status_changed"
    PIPELINE_GENERATION_COMPLETE = "pipeline.generation_complete"
    JD_STATUS_CHANGED = "jd.status_changed"


# --- Channel helpers ------------------------------------------------------

def job_channel(job_id: str | uuid.UUID) -> str:
    """Channel for all events scoped to one job."""
    return f"job:{job_id}"


# --- Envelope -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Envelope:
    """Transport-level wrapper around a domain event.

    All events share this shape so subscribers can deserialize uniformly.
    """
    event: str
    payload: dict
    correlation_id: str
    emitted_at: str  # ISO-8601 UTC

    def to_json(self) -> bytes:
        return orjson.dumps({
            "event": self.event,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "emitted_at": self.emitted_at,
        })

    @classmethod
    def from_json(cls, raw: bytes | str) -> "Envelope":
        data = orjson.loads(raw)
        return cls(
            event=data["event"],
            payload=data["payload"],
            correlation_id=data["correlation_id"],
            emitted_at=data["emitted_at"],
        )


# --- Client lifecycle -----------------------------------------------------

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        # Separate pool from Dramatiq — pub/sub subscribe connections block.
        _client = aioredis.from_url(
            settings.redis_url,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=10,
            max_connections=100,
        )
    return _client


async def startup() -> None:
    """Initialize the client and verify connectivity. Called from FastAPI lifespan."""
    client = _get_client()
    try:
        await client.ping()
        logger.info("pubsub.startup", status="ok")
    except RedisError as exc:
        logger.error("pubsub.startup", status="failed", error=str(exc))
        raise


async def shutdown() -> None:
    """Close the client and drain any pending operations."""
    global _client
    if _client is not None:
        with suppress(Exception):
            await _client.aclose()
        _client = None
        logger.info("pubsub.shutdown")


# --- Public API -----------------------------------------------------------

async def publish(
    channel: str,
    event: str,
    payload: dict,
    *,
    correlation_id: str,
) -> None:
    """Publish an event. Best-effort — never raises.

    MUST be called AFTER the DB transaction that produced the event has
    committed. In FastAPI handlers, use BackgroundTasks. In actors, call
    after `async with session.begin():` exits.
    """
    envelope = Envelope(
        event=event,
        payload=payload,
        correlation_id=correlation_id,
        emitted_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        client = _get_client()
        # asyncio.shield so a cancelled caller doesn't abort a half-sent publish.
        await asyncio.shield(client.publish(channel, envelope.to_json()))
        logger.info(
            "pubsub.publish.ok",
            channel=channel,
            event_name=event,
            correlation_id=correlation_id,
            metric_name="pubsub.publish.ok",
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, catch everything
        logger.warning(
            "pubsub.publish.failed",
            channel=channel,
            event_name=event,
            correlation_id=correlation_id,
            error=str(exc),
            metric_name="pubsub.publish.failed",
        )


async def subscribe(*channels: str) -> AsyncIterator[Envelope]:
    """Subscribe to one or more channels, yielding envelopes.

    Auto-reconnects with exponential backoff on connection drops.
    Events missed during reconnects are NOT re-delivered — the caller
    is responsible for any correctness backstop.

    Honors asyncio cancellation: cancelling the iterator closes the
    pubsub connection cleanly.
    """
    backoff_seconds = 1.0
    max_backoff = 30.0
    while True:
        client = _get_client()
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(*channels)
            logger.info(
                "pubsub.subscribe.connected",
                channels=list(channels),
                metric_name="pubsub.subscribe.connected",
            )
            backoff_seconds = 1.0  # reset on successful connection
            # Inner loop restarts listen() on idle socket-read timeouts
            # without tearing down the connection. socket_timeout=5 fires
            # every 5s of channel silence; treating that as a disconnect
            # would spam reconnect logs once per idle window per subscriber.
            # health_check_interval=10 sends PINGs that surface as
            # ConnectionError — real death still flows through the
            # outer except → backoff reconnect path.
            while True:
                try:
                    async for raw in pubsub.listen():
                        if raw.get("type") != "message":
                            continue  # skip subscribe/unsubscribe control messages
                        try:
                            yield Envelope.from_json(raw["data"])
                        except (orjson.JSONDecodeError, KeyError) as exc:
                            logger.warning(
                                "pubsub.subscribe.malformed_message",
                                error=str(exc),
                            )
                    # listen() exhausted normally (channel closed) — fall out.
                    break
                except (asyncio.TimeoutError, RedisTimeoutError):
                    continue
        except asyncio.CancelledError:
            logger.info("pubsub.subscribe.cancelled", channels=list(channels))
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pubsub.subscribe.reconnected",
                channels=list(channels),
                error=str(exc),
                backoff_seconds=backoff_seconds,
                metric_name="pubsub.subscribe.reconnected",
            )
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, max_backoff)
        finally:
            with suppress(Exception):
                await pubsub.aclose()
