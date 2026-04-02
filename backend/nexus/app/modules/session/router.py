from fastapi import APIRouter

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, str]:
    """Get session details. Stub."""
    return {"status": "not_implemented", "session_id": session_id}


@router.post("/{session_id}/token")
async def get_session_token(session_id: str) -> dict[str, str]:
    """Provision a LiveKit token for this session. Stub."""
    return {"status": "not_implemented", "session_id": session_id}


# --- Candidate-facing endpoints (JWT-gated, no Supabase auth) ---

candidate_router = APIRouter(prefix="/api/candidate-session", tags=["candidate_session"])


@candidate_router.post("/{token}/start")
async def start_candidate_session(token: str) -> dict[str, str]:
    """Start a candidate's interview session. Stub."""
    return {"status": "not_implemented"}


@candidate_router.post("/{token}/consent")
async def record_consent(token: str) -> dict[str, str]:
    """Record candidate consent before recording begins. Stub."""
    return {"status": "not_implemented"}
