"""Dramatiq worker entry point for the VISION queue only.

Run by the dedicated `nexus-vision-worker` service (heavier `Dockerfile.vision`
image with onnxruntime + uniface + opencv):

    dramatiq app.vision_worker --processes 1 --threads 2 -Q vision

Why a separate entrypoint (not `app.worker`)?
  The lean `nexus`/`nexus-worker` image does NOT install onnxruntime/uniface.
  `app/worker.py` therefore must not register the vision actor — otherwise the
  default worker (which listens on all queues) would consume "vision" messages
  and crash at runtime with ModuleNotFoundError. Registering the vision actor
  ONLY here means only this process declares + consumes the "vision" queue.

Mirrors `app/worker.py`'s broker/structlog/OTel bootstrap; it just imports a
different (single) actor module.
"""

import atexit

import structlog

from app.config import settings

# --- structlog init (mirrors app/worker.py) ---
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

# --- OpenTelemetry init (mirrors app/worker.py) ---
# I001/E402 are suppressed throughout: this bootstrap intentionally defers all
# imports until after the structlog/OTel setup runs, so import-sorting and
# "imports at top of file" do not apply (same deferred-import pattern as
# app/worker.py).
from opentelemetry import trace  # noqa: E402, I001
from app.ai.otel import bootstrap_tracer_provider  # noqa: E402, I001

_otel_provider = bootstrap_tracer_provider()
trace.set_tracer_provider(_otel_provider)

# Broker setup — MUST be imported before any actor module.
from app import brokers  # noqa: F401, E402, I001

# The ONLY actor this worker registers/consumes — the vision proctoring queue.
from app.modules.vision import actors as _vision_actors  # noqa: F401, E402

# Configure the FULL ORM mapper registry. This process registers only the vision
# actor (+ Session), but `SessionProctoringAnalysis` has FKs to `clients` /
# `sessions`; without importing every model + configure(), the first query fails
# with NoReferencedTableError. Shared with app/main.py — single source of truth.
from app.model_registry import configure_all_models  # noqa: E402

configure_all_models()

# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)
