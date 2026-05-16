"""Error code taxonomy for engine-driven session failures.

The Literal values here are pinned by a CHECK constraint on
sessions.error_code (migration 0039). Adding a value requires:
  1. Update the Literal.
  2. Update the CHECK constraint via a new migration.
  3. Update the two frontend label maps:
     - frontend/session/components/interview/lib/session-error-messages.ts
     - frontend/app/components/dashboard/tracker/session-error-labels.ts
"""
from __future__ import annotations

from typing import Literal

from app.modules.interview_runtime import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
)

ErrorCode = Literal[
    "engine_session_config_invalid",
    "engine_company_profile_missing",
    "engine_question_bank_not_ready",
    "engine_room_join_failed",
    "engine_internal_error",
    "engine_unresponsive",
]


def classify_engine_exception(exc: BaseException) -> ErrorCode:
    """Map an exception raised during entrypoint to an ErrorCode.

    Order matters — more-specific types first. Default catch-all is
    engine_internal_error.

    Pydantic ValidationError is identified by module-path inspection
    rather than isinstance so this module doesn't have to import
    pydantic_core's internal class hierarchy (which differs between
    pydantic 2.x minor versions).
    """
    if isinstance(exc, CompanyProfileMissingError):
        return "engine_company_profile_missing"
    if isinstance(exc, QuestionBankNotReadyError):
        return "engine_question_bank_not_ready"
    if type(exc).__name__ == "ValidationError" and type(exc).__module__.startswith(
        "pydantic"
    ):
        return "engine_session_config_invalid"
    # TODO(verify-at-implementation): LiveKit ctx.connect() raises
    # ConnectError / asyncio.TimeoutError — once we observe the actual
    # exception types in dev, add an isinstance check that maps them to
    # engine_room_join_failed. Until then, those land in engine_internal_error.
    return "engine_internal_error"
