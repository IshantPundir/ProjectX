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

import atexit

import structlog

from app.config import settings

# --- structlog init (mirrors app/main.py lifespan) ---
# The API process configures structlog in its lifespan handler. The worker
# is a separate process and needs its own init, otherwise logs use the
# default human-readable format which doesn't parse in log aggregators.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        10 if settings.debug else 20
    ),
)

# --- OpenTelemetry init (mirrors app/main.py lifespan) ---
# Worker is a separate process and needs its own TracerProvider. Spans
# emitted by Dramatiq actors (jd, question_bank) flow through this provider.
from opentelemetry import trace  # noqa: E402
from app.ai.otel import bootstrap_tracer_provider, instrument_openai  # noqa: E402

_otel_provider = bootstrap_tracer_provider()
trace.set_tracer_provider(_otel_provider)
instrument_openai()

# Broker setup — MUST be imported before any actor module
from app import brokers  # noqa: F401, E402

# Actor imports — registered against the broker above
from app.modules.jd import actors as _jd_actors  # noqa: F401, E402

# Phase 2C.2 — question bank generation actors
from app.modules.question_bank import actors as _question_bank_actors  # noqa: F401, E402

# Flush Langfuse traces on worker exit so pending events aren't lost.
from app.ai.client import shutdown_langfuse  # noqa: E402

atexit.register(shutdown_langfuse)
# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)

