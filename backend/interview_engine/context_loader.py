"""Session context loader.

The single seam that controls standalone vs. integrated operation.
Phase 3A implements fixture mode only. Room metadata and Nexus API
modes are stubs that raise NotImplementedError with clear messages
about what Nexus needs to provide.
"""

import json
from pathlib import Path

import structlog

from models import SessionConfig
from config import InterviewEngineConfig

logger = structlog.get_logger(__name__)


async def load_session_config(config: InterviewEngineConfig) -> SessionConfig:
    """Load interview context based on config.context_source.

    Modes:
        fixture: Load from a local JSON file (Phase 3A standalone testing)
        room_metadata: Deserialize from LiveKit room metadata (Phase 3B)
        nexus_api: Fetch from Nexus API using session_id (Phase 3B)
    """
    match config.context_source:
        case "fixture":
            return _load_from_fixture(config.fixture_path)
        case "room_metadata":
            raise NotImplementedError(
                "room_metadata mode requires Phase 3B integration. "
                "Nexus must set room metadata with a JSON-serialized "
                "SessionConfig when creating the LiveKit room. "
                "See: backend/nexus/app/modules/session/service.py"
            )
        case "nexus_api":
            raise NotImplementedError(
                "nexus_api mode requires Phase 3B integration. "
                "Nexus must expose GET /api/sessions/{session_id}/config "
                "returning a SessionConfig-shaped JSON body. "
                "See: backend/nexus/app/modules/session/router.py"
            )
        case _:
            raise ValueError(
                f"Unknown context_source: {config.context_source!r}. "
                f"Valid values: fixture, room_metadata, nexus_api"
            )


def _load_from_fixture(fixture_path: str) -> SessionConfig:
    """Load and validate a SessionConfig from a local JSON fixture file."""
    path = Path(fixture_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Fixture file not found: {path.resolve()}. "
            f"Create it at backend/interview_engine/fixtures/sample_session.json "
            f"or set FIXTURE_PATH to a different location."
        )

    raw = json.loads(path.read_text())
    config = SessionConfig.model_validate(raw)

    logger.info(
        "session_config.loaded",
        source="fixture",
        fixture_path=str(path),
        session_id=config.session_id,
        job_title=config.job_title,
        question_count=len(config.stage.questions),
        duration_minutes=config.stage.duration_minutes,
    )

    return config
