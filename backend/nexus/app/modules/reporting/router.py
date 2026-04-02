from fastapi import APIRouter

router = APIRouter(prefix="/api/reports", tags=["reporting"])


@router.get("/{session_id}")
async def get_report(session_id: str) -> dict[str, str]:
    """Get the evaluation report for a completed session. Stub."""
    return {"status": "not_implemented", "session_id": session_id}
