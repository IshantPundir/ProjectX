"""Session module — candidate interview session lifecycle."""
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.models import CandidateSessionToken, Session
from app.modules.session.schemas import SessionState
from app.modules.session.state_machine import transition

__all__ = [
    "CandidateSessionToken",
    "ErrorCode",
    "Session",
    "SessionNotFoundError",
    "SessionState",
    "classify_engine_exception",
    "transition",
    "transition_to_error",
]


def __getattr__(name: str) -> object:
    """Lazy re-exports to break the circular import:

    session/__init__ -> error_codes -> interview_runtime/__init__
    -> interview_runtime/service -> candidates -> candidates/service
    -> session (partially initialized!) -> ImportError.

    `ErrorCode`, `classify_engine_exception`, and `transition_to_error`
    are only loaded on first access (never during package init), so the
    session package is fully initialized before `interview_runtime` and
    `candidates` are touched.
    """
    if name in ("ErrorCode", "classify_engine_exception"):
        from app.modules.session.error_codes import (  # noqa: PLC0415
            ErrorCode,
            classify_engine_exception,
        )
        globals()["ErrorCode"] = ErrorCode
        globals()["classify_engine_exception"] = classify_engine_exception
        return globals()[name]
    if name == "transition_to_error":
        from app.modules.session.service import transition_to_error  # noqa: PLC0415
        globals()["transition_to_error"] = transition_to_error
        return transition_to_error
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
