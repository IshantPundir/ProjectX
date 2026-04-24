"""Unit tests for app/pubsub.py."""
from __future__ import annotations

import asyncio

import pytest
from redis.exceptions import RedisError

from app import pubsub

pytestmark = pytest.mark.asyncio


async def test_envelope_round_trip():
    env = pubsub.Envelope(
        event=pubsub.Events.BANK_QUESTION_UPDATED,
        payload={"job_id": "abc", "bank_id": "def"},
        correlation_id="corr-1",
        emitted_at="2026-04-24T00:00:00+00:00",
    )
    reconstructed = pubsub.Envelope.from_json(env.to_json())
    assert reconstructed == env


async def test_publish_swallows_redis_error(monkeypatch, caplog):
    """publish() must NEVER raise — failures become structlog warnings."""
    class FailingClient:
        async def publish(self, *_args, **_kwargs):
            raise RedisError("simulated outage")

    monkeypatch.setattr(pubsub, "_get_client", lambda: FailingClient())

    # Should return None, not raise.
    result = await pubsub.publish(
        pubsub.job_channel("job-123"),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {"bank_id": "bank-1"},
        correlation_id="corr-1",
    )
    assert result is None


async def test_publish_ok_path(monkeypatch):
    published: list[tuple[str, bytes]] = []

    class FakeClient:
        async def publish(self, channel, data):
            published.append((channel, data))

    monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

    await pubsub.publish(
        pubsub.job_channel("job-123"),
        pubsub.Events.BANK_QUESTION_UPDATED,
        {"bank_id": "bank-1"},
        correlation_id="corr-xyz",
    )

    assert len(published) == 1
    channel, data = published[0]
    assert channel == "job:job-123"
    env = pubsub.Envelope.from_json(data)
    assert env.event == pubsub.Events.BANK_QUESTION_UPDATED
    assert env.correlation_id == "corr-xyz"
    assert env.payload == {"bank_id": "bank-1"}


async def test_subscribe_skips_non_message_frames(monkeypatch):
    """The pubsub.listen() generator yields subscribe/unsubscribe control
    frames before the first real message. subscribe() must skip them."""

    class FakePubSub:
        def __init__(self):
            self.frames = [
                {"type": "subscribe", "channel": b"job:1", "data": 1},
                {
                    "type": "message",
                    "channel": b"job:1",
                    "data": pubsub.Envelope(
                        event=pubsub.Events.BANK_QUESTION_UPDATED,
                        payload={"hello": "world"},
                        correlation_id="c",
                        emitted_at="2026-04-24T00:00:00+00:00",
                    ).to_json(),
                },
            ]

        async def subscribe(self, *_channels):
            pass

        async def listen(self):
            for f in self.frames:
                yield f
            # Simulate channel close — exit the generator.

        async def aclose(self):
            pass

    class FakeClient:
        def pubsub(self):
            return FakePubSub()

    monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

    envelopes: list[pubsub.Envelope] = []
    async def collect():
        async for env in pubsub.subscribe("job:1"):
            envelopes.append(env)
            if len(envelopes) == 1:
                break

    # subscribe loops forever on reconnect; break out after first real message.
    await asyncio.wait_for(collect(), timeout=2.0)
    assert len(envelopes) == 1
    assert envelopes[0].payload == {"hello": "world"}


async def test_subscribe_reconnects_on_error(monkeypatch):
    """If the underlying connection raises, subscribe() reconnects."""
    attempt = {"count": 0}

    class FlakyPubSub:
        def __init__(self, should_fail):
            self.should_fail = should_fail

        async def subscribe(self, *_channels):
            pass

        async def listen(self):
            if self.should_fail:
                raise RedisError("connection reset")
            yield {
                "type": "message",
                "channel": b"job:1",
                "data": pubsub.Envelope(
                    event=pubsub.Events.BANK_QUESTION_UPDATED,
                    payload={"final": True},
                    correlation_id="c",
                    emitted_at="2026-04-24T00:00:00+00:00",
                ).to_json(),
            }

        async def aclose(self):
            pass

    class FakeClient:
        def pubsub(self):
            attempt["count"] += 1
            # Fail the first attempt, succeed the second.
            return FlakyPubSub(should_fail=attempt["count"] == 1)

    monkeypatch.setattr(pubsub, "_get_client", lambda: FakeClient())

    # Patch sleep so the backoff doesn't dominate the test runtime.
    async def fast_sleep(_):
        pass
    monkeypatch.setattr(pubsub.asyncio, "sleep", fast_sleep)

    envelopes = []
    async def collect():
        async for env in pubsub.subscribe("job:1"):
            envelopes.append(env)
            break

    await asyncio.wait_for(collect(), timeout=2.0)
    assert attempt["count"] == 2, "subscribe() did not reconnect after error"
    assert envelopes[0].payload == {"final": True}
