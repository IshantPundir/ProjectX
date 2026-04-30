"""Session-config loader.

Two modes:

- ``fixture`` (engine pytest only) — load a SessionConfig from a JSON file
  on disk. Used by interview-engine standalone tests, not by production.
- ``nexus_api`` (production) — fetch SessionConfig from nexus's
  /api/internal/sessions/{id}/config via httpx. The dispatch JWT signed
  by nexus authenticates the call.

Mode selection is implicit in the caller's argument set — pass
``fixture_path`` for fixture mode, or ``session_id``/``jwt``/``base_url``
for nexus_api mode. There is no env-driven mode flag.

Note: production-path callers (``agent.py``) call
``nexus_client.fetch_session_config`` directly. This module exists for the
engine's pytest path that wants to bypass HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.modules.interview_runtime.schemas import SessionConfig

from nexus_client import fetch_session_config

logger = structlog.get_logger(__name__)


async def load_session_config(
    *,
    session_id: str | None = None,
    jwt: str | None = None,
    base_url: str | None = None,
    fixture_path: Path | None = None,
) -> SessionConfig:
    """Load a SessionConfig.

    Pass ``fixture_path`` for engine-pytest fixture mode; pass
    ``session_id`` + ``jwt`` + ``base_url`` for nexus_api mode. Mixing
    both is an error.
    """
    if fixture_path is not None:
        if any(arg is not None for arg in (session_id, jwt, base_url)):
            raise ValueError(
                "fixture_path cannot be combined with session_id/jwt/base_url"
            )
        return _load_from_fixture(fixture_path)

    if not (session_id and jwt and base_url):
        raise ValueError(
            "nexus_api mode requires session_id + jwt + base_url"
        )

    return await fetch_session_config(
        session_id=session_id, jwt=jwt, base_url=base_url
    )


def _load_from_fixture(fixture_path: Path) -> SessionConfig:
    """Load and validate a SessionConfig from a local JSON fixture file."""
    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Fixture file not found: {fixture_path.resolve()}"
        )

    raw = json.loads(fixture_path.read_text())
    config = SessionConfig.model_validate(raw)

    logger.info(
        "session_config.loaded",
        source="fixture",
        fixture_path=str(fixture_path),
        session_id=config.session_id,
        job_title=config.job_title,
        question_count=len(config.stage.questions),
        duration_minutes=config.stage.duration_minutes,
    )

    return config
