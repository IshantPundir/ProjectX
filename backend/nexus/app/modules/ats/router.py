from fastapi import APIRouter

router = APIRouter(prefix="/api/ats", tags=["ats"])


@router.get("/connections")
async def list_connections() -> dict[str, str]:
    """List ATS connections for the current tenant. Stub."""
    return {"status": "not_implemented"}
