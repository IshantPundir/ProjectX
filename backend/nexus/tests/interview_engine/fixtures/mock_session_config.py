"""Test fixture helpers for the interview-engine test suite.

Loads the live-data bank JSON into a SessionConfig instance for tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.modules.interview_runtime.schemas import SessionConfig


_FIXTURE_DIR = Path(__file__).parent
_LIVE_DATA_PATH = _FIXTURE_DIR / "live_data_bank_7d96c5d1.json"


def load_live_data_session_config() -> SessionConfig:
    """Return a SessionConfig populated from live_data_bank_7d96c5d1.json.

    The fixture mirrors the structure of stage 7d96c5d1 in the local
    Supabase instance as captured in the overview spec. Tests that need
    the full 6-question bank for end-to-end controller flow should use
    this helper.
    """
    with _LIVE_DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return SessionConfig.model_validate(data)
