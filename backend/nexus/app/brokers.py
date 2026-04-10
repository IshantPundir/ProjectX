"""Dramatiq broker initialization — imported by BOTH the API and the worker.

This must run BEFORE any module that uses the @dramatiq.actor decorator
is imported. Otherwise Dramatiq's default broker (a RedisBroker pointing
at localhost:6379) is used, which fails inside our docker container where
Redis is a sibling service at `redis:6379`, not localhost.

Both entry points import this module:
  - app/main.py (API process) — imports `from app import brokers` at module
    load time, before router registration triggers actor imports.
  - app/worker.py (Dramatiq worker process) — imports this first, then
    imports actor modules so the CLI can discover them.

Keeping broker setup in a standalone module (no transitive imports from
app.modules.*) ensures no circular import risk."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from app.config import settings

broker = RedisBroker(
    url=settings.redis_url,
    # Prevent workers from hanging indefinitely on a slow/unresponsive Redis.
    # socket_timeout applies to reads/writes, socket_connect_timeout to initial
    # connection. health_check_interval verifies the connection is alive before
    # each command if it has been idle for this many seconds.
    socket_timeout=5,
    socket_connect_timeout=5,
    health_check_interval=10,
    retry_on_timeout=True,
)

# AsyncIO middleware is REQUIRED for `async def` @dramatiq.actor functions.
# Without it, the worker raises:
#   RuntimeError: Global event loop thread not set. Have you added the
#   AsyncIO middleware to your middleware stack?
# The middleware spawns a background thread running an asyncio event loop
# that async actors get scheduled onto. Sync actors are unaffected.
broker.add_middleware(AsyncIO())

dramatiq.set_broker(broker)
