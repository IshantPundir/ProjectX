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

Import-ordering note: `app.brokers` initializes the global Dramatiq broker and
MUST be imported before any actor module so `@dramatiq.actor` binds to the right
broker. Standard alphabetical import order satisfies this (`app` sorts before
`app.modules.vision`), so no deferred/`# noqa: E402` imports are needed. The two
imported-for-side-effect modules (`brokers`, `actors`) carry `# noqa: F401`.
"""

import atexit

import structlog
from opentelemetry import trace

from app import brokers  # noqa: F401  (side effect: init broker before actor import)
from app.ai.otel import bootstrap_tracer_provider
from app.ai.realtime import prewarm_tts_plugin
from app.config import settings
from app.model_registry import configure_all_models
from app.modules.reel import actors as reel_actors  # noqa: F401  (register reel actor, queue "reel")
from app.modules.vision import actors  # noqa: F401  (side effect: register vision actor)

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
    wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
)

# --- OpenTelemetry init (mirrors app/worker.py) ---
_otel_provider = bootstrap_tracer_provider()
trace.set_tracer_provider(_otel_provider)

# Configure the FULL ORM mapper registry. This process registers only the vision
# actor (+ Session), but `SessionProctoringAnalysis` has FKs to `clients` /
# `sessions`; without importing every model + configure(), the first query fails
# with NoReferencedTableError. Shared with app/main.py — single source of truth.
configure_all_models()

# Register the configured TTS plugin on the MAIN thread. The reel renders
# narration (reel/tts.py → realtime.build_tts_plugin) inside Dramatiq
# worker-thread actors, and LiveKit requires plugin registration on the main
# thread (Plugin.register_plugin raises otherwise). Dramatiq imports this
# entrypoint on the main thread at startup, so registering here means the later
# worker-thread import reuses the cached module. See realtime.prewarm_tts_plugin.
prewarm_tts_plugin()

# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)
