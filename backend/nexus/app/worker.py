"""Dramatiq worker entry point.

Run in dev via:
    docker compose up nexus-worker

Run directly via:
    dramatiq app.worker --processes 2 --threads 4

Broker setup lives in app/brokers.py so both the API and the worker
share the same initialization. Importing app.brokers sets the Redis
broker; importing the actor modules triggers their @dramatiq.actor
decorators to register against that broker.

Without the brokers import, Dramatiq falls back to a default RedisBroker
at localhost:6379 — which fails inside the container where Redis is a
sibling service."""

# Broker setup — MUST be imported before any actor module
from app import brokers  # noqa: F401

# Actor imports — registered against the broker above
from app.modules.jd import actors as _jd_actors  # noqa: F401

