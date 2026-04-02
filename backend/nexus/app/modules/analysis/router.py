from fastapi import APIRouter

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{session_id}/signals")
async def get_session_signals(session_id: str) -> dict[str, str]:
    """Get real-time signal analysis for a session. Stub."""
    return {"status": "not_implemented", "session_id": session_id}
