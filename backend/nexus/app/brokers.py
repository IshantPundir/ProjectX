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

from app.config import settings

broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)
