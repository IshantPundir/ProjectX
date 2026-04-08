"""Dramatiq worker entry point.

Run in dev via:
    docker compose up nexus-worker

Run directly via:
    dramatiq app.worker --processes 2 --threads 4

Every actor module must be imported here so Dramatiq registers the
actors with the broker at worker startup. Without these imports,
the Dramatiq CLI finds zero actors and exits with 'no actors registered'."""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.config import settings

broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(broker)

# Actor imports — MUST stay after set_broker so actors register against
# the correct broker instance. Prefer `noqa: F401, E402` to suppress the
# unused-import and module-level-not-at-top warnings.
from app.modules.jd import actors as _jd_actors  # noqa: F401, E402
